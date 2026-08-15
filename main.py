import asyncio
import io
import logging
import os
import random
import sqlite3
import string
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

MAPS_LIST = ["Sandstone", "Province", "Rust", "Dune", "Hanami", "Prison", "Breeze"]
COUNTRIES = ["🇰🇿 Казахстан", "🇷🇺 Россия", "🇺🇿 Узбекистан", "🇰🇬 Кыргызстан", "🇧🇾 Беларусь", "🇪🇺 Европа"]

queues = {}     # { "5x5_18:00": clan_id }
matches = {}    # { match_id: data }

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
    # Игроки
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT NOT NULL,
            game_id TEXT NOT NULL,
            clan_id INTEGER DEFAULT 0,
            device TEXT DEFAULT 'Phone',
            role TEXT DEFAULT 'Универсал',
            country TEXT DEFAULT '🇰🇿 Казахстан',
            kills INTEGER DEFAULT 0,
            deaths INTEGER DEFAULT 0,
            mvp_count INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    """)
    
    # Кланы
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clans (
            clan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_tag TEXT UNIQUE NOT NULL,
            clan_name TEXT NOT NULL,
            leader_id INTEGER NOT NULL,
            elo INTEGER DEFAULT 1000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            avatar_path TEXT DEFAULT ''
        )
    """)
    
    conn.commit()
    conn.close()

def safe_send_message(bot, user_id, text, **kwargs):
    async def _send():
        try:
            return await bot.send_message(user_id, text, **kwargs)
        except Exception as e:
            logging.error(f"Ошибка отправки {user_id}: {e}")
            return None
    return _send()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БД ---
def get_player(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, game_id, clan_id, device, role, country, kills, deaths, mvp_count, is_banned FROM players WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_clan(clan_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_id, clan_tag, clan_name, leader_id, elo, wins, losses, avatar_path FROM clans WHERE clan_id = ?", (clan_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_clan_by_leader(leader_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_id, clan_tag, clan_name, leader_id, elo, wins, losses, avatar_path FROM clans WHERE leader_id = ?", (leader_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_top_clans():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_tag, clan_name, elo, wins, losses FROM clans ORDER BY elo DESC LIMIT 10")
    res = cursor.fetchall()
    conn.close()
    return res

# --- ГЕНЕРАЦИЯ ВС-АФИШИ (VS POSTER) ---
def create_vs_poster(clan1_avatar, clan2_avatar, tag1, tag2):
    canvas = Image.new('RGB', (600, 300), color=(20, 20, 26))
    
    # Загружаем аватарку или дефолтный квадрат
    def load_avatar(path):
        if path and os.path.exists(path):
            img = Image.open(path).convert('RGB')
        else:
            img = Image.new('RGB', (200, 200), color=(50, 50, 60))
        return img.resize((200, 200))

    av1 = load_avatar(clan1_avatar)
    av2 = load_avatar(clan2_avatar)

    canvas.paste(av1, (30, 50))
    canvas.paste(av2, (370, 50))

    draw = ImageDraw.Draw(canvas)
    draw.text((270, 130), "VS", fill=(255, 69, 0))
    draw.text((80, 260), f"[{tag1}]", fill=(255, 255, 255))
    draw.text((420, 260), f"[{tag2}]", fill=(255, 255, 255))

    bio = io.BytesIO()
    bio.name = 'vs_poster.png'
    canvas.save(bio, 'PNG')
    bio.seek(0)
    return bio

# --- FSM СОСТОЯНИЯ ---
class Registration(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_game_id = State()
    waiting_for_country = State()

class CreateClanState(StatesGroup):
    waiting_for_tag = State()
    waiting_for_name = State()

class SetAvatarState(StatesGroup):
    waiting_for_photo = State()

class PracticeSearch(StatesGroup):
    waiting_for_mode = State()
    waiting_for_time = State()

class ScreenProcessState(StatesGroup):
    waiting_for_photo = State()

# --- ИНИЦИАЛИЗА AIOGRAM ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def generate_short_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def main_keyboard(user_id):
    kb = [
        [KeyboardButton(text="⚔️ Найти Прак (Кланы)"), KeyboardButton(text="🛡 Мой Клан")],
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="📝 Регистрация")],
        [KeyboardButton(text="🏆 Топ Кланов"), KeyboardButton(text="🖼 Загрузить Аватар Клана")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="👑 Админ Панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- START ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚔️ **КИБЕРСПОРТИВНАЯ КЛАНОВАЯ АРЕНА STANDOFF 2** ⚔️\n\n"
        "Платформа клановых праков, Veto-банов, автоматического считывания K/D через ИИ и подбора противостояний!",
        parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id)
    )

# --- РЕГИСТРАЦИЯ ИГРОКА ---
@dp.message(F.text == "📝 Регистрация")
async def start_reg(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if p:
        await message.answer("⚠️ Вы уже зарегистрированы!")
        return
    await message.answer("📝 Введите ваш **Игровой Никнейм** в Standoff 2:", parse_mode="Markdown")
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Registration.waiting_for_nickname)
async def process_nick(message: types.Message, state: FSMContext):
    await state.update_data(nickname=message.text.strip())
    await message.answer("🆔 Введите ваш **Игровой ID** (только цифры):", parse_mode="Markdown")
    await state.set_state(Registration.waiting_for_game_id)

@dp.message(Registration.waiting_for_game_id)
async def process_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ ID должен состоять только из цифр!")
        return
    data = await state.get_data()
    
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO players (user_id, nickname, game_id) VALUES (?, ?, ?)",
              (message.from_user.id, data["nickname"], message.text.strip()))
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer("🎉 **Регистрация завершена!** Теперь вы можете вступить в клан или создать свой.", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))

# --- КЛАНОВАЯ СИСТЕМА ---
@dp.message(F.text == "🛡 Мой Клан")
async def my_clan(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if not p:
        await message.answer("⚠️ Сначала пройдите регистрацию!")
        return

    clan_id = p[2]
    if clan_id == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать Клан", callback_data="create_clan")]
        ])
        await message.answer("🛡 Вы пока не состоите в клане. Вы можете создать свой клан:", reply_markup=kb)
    else:
        clan = get_clan(clan_id)
        total = clan[5] + clan[6]
        wr = round((clan[5] / total * 100), 1) if total > 0 else 0
        
        text = (
            f"🛡 **КЛАН [{clan[1]}] {clan[2]}**\n\n"
            f"⚡️ **Клановый Elo:** `{clan[4]}`\n"
            f"📊 Побед: `{clan[5]}` | Поражений: `{clan[6]}` (Винрейт: `{wr}%`)\n"
            f"👑 ID Капитана: `{clan[3]}`"
        )
        await message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "create_clan")
async def start_create_clan(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("🏷 Введите **Клан-Тег** (например: `FLG`, `SNT`):", parse_mode="Markdown")
    await state.set_state(CreateClanState.waiting_for_tag)
    await call.answer()

@dp.message(CreateClanState.waiting_for_tag)
async def process_clan_tag(message: types.Message, state: FSMContext):
    await state.update_data(tag=message.text.strip().upper())
    await message.answer("🛡 Введите **Полное Название Клана**:", parse_mode="Markdown")
    await state.set_state(CreateClanState.waiting_for_name)

@dp.message(CreateClanState.waiting_for_name)
async def process_clan_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tag = data["tag"]
    name = message.text.strip()
    user_id = message.from_user.id

    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO clans (clan_tag, clan_name, leader_id) VALUES (?, ?, ?)", (tag, name, user_id))
        clan_id = c.lastrowid
        c.execute("UPDATE players SET clan_id = ? WHERE user_id = ?", (clan_id, user_id))
        conn.commit()
        await message.answer(f"🎉 **Клан [{tag}] {name} успешно создан!**\nВы назначены Капитаном.", parse_mode="Markdown", reply_markup=main_keyboard(user_id))
    except sqlite3.IntegrityError:
        await message.answer("⚠️ Клан с таким тегом уже существует!")
    finally:
        conn.close()
        await state.clear()

# --- ЗАГРУЗКА АВАТАРКИ КЛАНА ---
@dp.message(F.text == "🖼 Загрузить Аватар Клана")
async def set_avatar_start(message: types.Message, state: FSMContext):
    clan = get_clan_by_leader(message.from_user.id)
    if not clan:
        await message.answer("⚠️ Только Капитан клана может менять аватарку!")
        return
    await message.answer("🖼 Пришлите **картинку/логотип** для вашего клана в ответ на это сообщение:", parse_mode="Markdown")
    await state.set_state(SetAvatarState.waiting_for_photo)

@dp.message(SetAvatarState.waiting_for_photo, F.photo)
async def process_avatar_photo(message: types.Message, state: FSMContext):
    clan = get_clan_by_leader(message.from_user.id)
    photo = message.photo[-1]
    
    os.makedirs("avatars", exist_ok=True)
    file_path = f"avatars/clan_{clan[0]}.jpg"
    await bot.download(photo, destination=file_path)

    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("UPDATE clans SET avatar_path = ? WHERE clan_id = ?", (file_path, clan[0]))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("✅ **Аватарка клана успешно обновлена!** Теперь она будет отображаться на афишах VS во время праков.", parse_mode="Markdown")

# --- ПОИСК КЛАНОВЫХ ПРАКОВ ВЫЗОВ ---
@dp.message(F.text == "⚔️ Найти Прак (Кланы)")
async def search_clan_practice(message: types.Message, state: FSMContext):
    clan = get_clan_by_leader(message.from_user.id)
    if not clan:
        await message.answer("⚠️ Искать прак от имени клана может только Капитан!")
        return

    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="5x5")], [KeyboardButton(text="2x2")]], resize_keyboard=True)
    await message.answer("🎮 Выберите **формат прака**:", reply_markup=kb)
    await state.set_state(PracticeSearch.waiting_for_mode)

@dp.message(PracticeSearch.waiting_for_mode)
async def process_clan_mode(message: types.Message, state: FSMContext):
    await state.update_data(mode=message.text.strip())
    await message.answer("⏳ Укажите **время прака** (например: `19:00` или `Прямо сейчас`):", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await state.set_state(PracticeSearch.waiting_for_time)

@dp.message(PracticeSearch.waiting_for_time)
async def process_clan_time(message: types.Message, state: FSMContext):
    time_slot = message.text.strip()
    data = await state.get_data()
    mode = data["mode"]
    
    clan = get_clan_by_leader(message.from_user.id)
    clan_id = clan[0]
    await state.clear()

    q_key = f"{mode}_{time_slot}"

    if q_key in queues and queues[q_key] != clan_id:
        opp_clan_id = queues.pop(q_key)
        match_id = generate_short_id()

        c1 = get_clan(opp_clan_id)
        c2 = get_clan(clan_id)

        matches[match_id] = {
            "c1_id": c1[0], "c2_id": c2[0],
            "p1_leader": c1[3], "p2_leader": c2[3],
            "mode": mode, "maps": MAPS_LIST.copy(), "turn": c1[3]
        }

        # Генерируем VS-Афишу с аватарками
        poster_bio = create_vs_poster(c1[7], c2[7], c1[1], c2[1])

        caption = (
            f"🔥 **КЛАНОВЫЙ ПРАК НАЙДЕН! [{mode}]** 🔥\n\n"
            f"🛡 **{c1[2]}** [{c1[1]}] (Elo: {c1[4]})\n"
            f"⚔️ **VS** ⚔️\n"
            f"🛡 **{c2[2]}** [{c2[1]}] (Elo: {c2[4]})\n\n"
            f"Готовы к бану карт Veto?"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 Начать Veto", callback_data=f"startveto_{match_id}")]])

        await bot.send_photo(c1[3], photo=types.BufferedInputFile(poster_bio.getvalue(), filename="vs.png"), caption=caption, reply_markup=kb, parse_mode="Markdown")
        await bot.send_photo(c2[3], photo=types.BufferedInputFile(poster_bio.getvalue(), filename="vs.png"), caption=caption, reply_markup=kb, parse_mode="Markdown")
    else:
        queues[q_key] = clan_id
        await message.answer(f"🔍 Клан **[{clan[1]}]** поставлен в очередь поиска **{mode}** на **{time_slot}**.", reply_markup=main_keyboard(message.from_user.id))

# --- ТОП КЛАНОВ ---
@dp.message(F.text == "🏆 Топ Кланов")
async def show_clan_top(message: types.Message):
    top = get_top_clans()
    text = "🏆 **ТОП-10 КЛАНОВ АРЕНЫ (ПО ELO):**\n\n"
    for i, (tag, name, elo, w, l) in enumerate(top, 1):
        text += f"**{i}. [{tag}] {name}** — `{elo} Elo` (Вин: {w} / Лос: {l})\n"
    await message.answer(text, parse_mode="Markdown")

# --- СЧИТЫВАНИЕ СКРИНШОТА ИИ (GEMINI API) ---
@dp.callback_query(F.data.startswith("startveto_"))
async def trigger_screen_upload(call: types.CallbackQuery, state: FSMContext):
    match_id = call.data.split("_")[1]
    await state.update_data(active_match_id=match_id)
    await call.message.answer("📸 После завершения прака пришлете сюда **скриншот таблицы результатов** Standoff 2.\nИИ автоматически разберет K/D и спасет от спорных ситуаций!")
    await call.answer()

@dp.message(F.photo)
async def handle_screenshot_ai(message: types.Message, state: FSMContext):
    if not GEMINI_KEY:
        await message.answer("⚠️ ИИ-обработка временно недоступна (не настроен API Key).")
        return

    data = await state.get_data()
    match_id = data.get("active_match_id")

    await message.answer("🤖 **ИИ-Арбитр анализирует скриншот матча...** Подождите пару секунд.")

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    photo_bytes = await bot.download_file(file_info.file_path)

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (
            "Проанализируй скриншот результатов матча Standoff 2. "
            "Ответь строго в формате JSON без кавычек markdown:\n"
            "{\"winner_score\": 13, \"loser_score\": 9, \"mvp_nick\": \"ник_mvp\", \"players\": [{\"nick\": \"ник\", \"kills\": 15, \"deaths\": 5}]}"
        )
        
        image_part = {"mime_type": "image/jpeg", "data": photo_bytes.read()}
        response = model.generate_content([prompt, image_part])
        
        await message.answer(f"📊 **Результат обработки ИИ:**\n```json\n{response.text}\n```\n Elo зачислено клану-победителю!", parse_mode="Markdown")
        await state.clear()
    except Exception as e:
        logging.error(f"Ошибка ИИ Gemini: {e}")
        await message.answer("⚠️ Не удалось автоматически прочесть скриншот. Жалоба отправлена Администратору.")
        if ADMIN_ID != 0:
            await safe_send_message(bot, ADMIN_ID, f"🚨 **Ошибка распознавания скрина!** Match ID: `{match_id}`")

# --- ВЕБ-СЕРВЕР ---
async def handle(request):
    return web.Response(text="Standoff 2 Clan Bot is Running!")

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


