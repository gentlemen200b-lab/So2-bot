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
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

MAPS_LIST = ["Sandstone", "Province", "Rust", "Dune", "Hanami", "Prison", "Breeze"]

queues = {}     # { "5x5_19:00": clan_id }
matches = {}    # { match_id: data }

def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
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
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clans (
            clan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_tag TEXT UNIQUE NOT NULL,
            clan_name TEXT NOT NULL,
            leader_id INTEGER NOT NULL,
            elo INTEGER DEFAULT 1000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            avatar_path TEXT DEFAULT ''
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clan_blacklist (
            clan_id INTEGER,
            blocked_clan_id INTEGER,
            PRIMARY KEY (clan_id, blocked_clan_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS match_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            opponent_nick TEXT,
            map_name TEXT,
            result TEXT,
            elo_change INTEGER,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_player(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, game_id, clan_id, device, role, country, kills, deaths, mvp_count, wins, losses, is_banned FROM players WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_clan(clan_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_id, clan_tag, clan_name, leader_id, elo, wins, losses, streak, avatar_path FROM clans WHERE clan_id = ?", (clan_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_clan_by_leader(leader_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_id, clan_tag, clan_name, leader_id, elo, wins, losses, streak, avatar_path FROM clans WHERE leader_id = ?", (leader_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_top_clans():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_tag, clan_name, elo, wins, losses, streak FROM clans ORDER BY elo DESC LIMIT 10")
    res = cursor.fetchall()
    conn.close()
    return res

def is_blocked(clan_id, target_clan_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM clan_blacklist WHERE clan_id = ? AND blocked_clan_id = ?", (clan_id, target_clan_id))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def create_vs_poster(clan1_avatar, clan2_avatar, tag1, tag2):
    canvas = Image.new('RGB', (600, 300), color=(15, 15, 20))
    
    def load_avatar(path):
        if path and os.path.exists(path):
            img = Image.open(path).convert('RGB')
        else:
            img = Image.new('RGB', (200, 200), color=(40, 40, 50))
        return img.resize((200, 200))

    av1 = load_avatar(clan1_avatar)
    av2 = load_avatar(clan2_avatar)

    canvas.paste(av1, (30, 50))
    canvas.paste(av2, (370, 50))

    draw = ImageDraw.Draw(canvas)
    draw.text((275, 130), "VS", fill=(255, 69, 0))
    draw.text((65, 260), f"[{tag1}]", fill=(255, 255, 255))
    draw.text((405, 260), f"[{tag2}]", fill=(255, 255, 255))

    bio = io.BytesIO()
    bio.name = 'vs_poster.png'
    canvas.save(bio, 'PNG')
    bio.seek(0)
    return bio

class Registration(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_game_id = State()
    waiting_for_country = State()
    waiting_for_device = State()
    waiting_for_role = State()

class CreateClanState(StatesGroup):
    waiting_for_tag = State()
    waiting_for_name = State()

class PracticeSearch(StatesGroup):
    waiting_for_mode = State()
    waiting_for_time = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def generate_short_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

def main_keyboard(user_id):
    kb = [
        [KeyboardButton(text="⚔️ Найти Прак (Кланы)"), KeyboardButton(text="🛡 Мой Клан")],
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="📝 Регистрация")],
        [KeyboardButton(text="📜 История матчей"), KeyboardButton(text="🏆 Топ Кланов")],
        [KeyboardButton(text="⚙️ Управление Кланом")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="👑 Админ Панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(F.text == "❌ Отмена")
@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено.", reply_markup=main_keyboard(message.from_user.id))

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚡️ **STANDOFF 2 | КЛАНОВАЯ АРЕНА PRO** ⚡️\n\n"
        "Добро пожаловать на профессиональную арену праков! Здесь тебя ждут Veto-баны карт, автоматическая аналитика матчей с помощью ИИ, система ростеров и таблица лидеров Elo.",
        parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id)
    )

# --- РЕГИСТРАЦИЯ ИГРОКА ---
@dp.message(F.text == "📝 Регистрация")
async def start_reg(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if p:
        await message.answer("⚠️ Вы уже зарегистрированы в системе!")
        return
    await message.answer("📝 **Шаг 1/5:** Введите ваш **Игровой Никнейм** в Standoff 2:", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Registration.waiting_for_nickname)
async def process_nick(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    await state.update_data(nickname=message.text.strip())
    await message.answer("🆔 **Шаг 2/5:** Введите ваш **Игровой ID** в Standoff 2 (только цифры):", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(Registration.waiting_for_game_id)

@dp.message(Registration.waiting_for_game_id)
async def process_id(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    if not message.text.isdigit():
        await message.answer("⚠️ Игровой ID должен состоять только из цифр!")
        return
    await state.update_data(game_id=message.text.strip())
    
    country_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🇰🇿 Казахстан"), KeyboardButton(text="🇷🇺 Россия")],
        [KeyboardButton(text="🇺🇿 Узбекистан"), KeyboardButton(text="🇰🇬 Кыргызстан")],
        [KeyboardButton(text="❌ Отмена")]
    ], resize_keyboard=True)
    
    await message.answer("🌍 **Шаг 3/5:** Выберите вашу **Страну / Регион**:", parse_mode="Markdown", reply_markup=country_kb)
    await state.set_state(Registration.waiting_for_country)

@dp.message(Registration.waiting_for_country)
async def process_country(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    await state.update_data(country=message.text.strip())

    dev_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 Phone (60 FPS)"), KeyboardButton(text="📱 Phone (90 FPS)")],
        [KeyboardButton(text="📱 Phone (120 FPS)"), KeyboardButton(text="📱 iPad (120 FPS)")],
        [KeyboardButton(text="❌ Отмена")]
    ], resize_keyboard=True)
    
    await message.answer("📱 **Шаг 4/5:** Выберите ваше **Устройство / FPS**:", parse_mode="Markdown", reply_markup=dev_kb)
    await state.set_state(Registration.waiting_for_device)

@dp.message(Registration.waiting_for_device)
async def process_device(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    await state.update_data(device=message.text.strip())
    
    role_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👑 Captain (IGL)"), KeyboardButton(text="🎯 Sniper (AWP)")],
        [KeyboardButton(text="💥 Entry Fragger"), KeyboardButton(text="🔫 Rifler / Support")],
        [KeyboardButton(text="❌ Отмена")]
    ], resize_keyboard=True)
    
    await message.answer("🎯 **Шаг 5/5:** Выберите вашу **Основную роль**:", parse_mode="Markdown", reply_markup=role_kb)
    await state.set_state(Registration.waiting_for_role)

@dp.message(Registration.waiting_for_role)
async def process_role(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    role = message.text.strip()
    data = await state.get_data()
    
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO players (user_id, nickname, game_id, country, device, role)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (message.from_user.id, data["nickname"], data["game_id"], data["country"], data["device"], role))
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer("🎉 **Регистрация успешно завершена!**", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))

# --- ПРОФИЛЬ ИГРОКА ---
@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message):
    p = get_player(message.from_user.id)
    if not p:
        await message.answer("⚠️ Вы еще не зарегистрированы! Нажмите «📝 Регистрация»")
        return

    clan_tag = "Нет клана"
    if p[2] != 0:
        c = get_clan(p[2])
        if c:
            clan_tag = f"[{c[1]}] {c[2]}"

    kills = p[6]
    deaths = p[7]
    kd = round(kills / deaths, 2) if deaths > 0 else kills

    text = (
        f"👤 **ПРОФИЛЬ КИБЕРСПОРТСМЕНА**\n\n"
        f"🎮 Никнейм: `{p[0]}`\n"
        f"🆔 Игровой ID: `{p[1]}`\n"
        f"🛡 Клан: **{clan_tag}**\n"
        f"🌍 Регион: `{p[5]}` | 📱 Устройство: `{p[3]}`\n"
        f"🎯 Роль: `{p[4]}`\n\n"
        f"📊 **Статистика матчей:**\n"
        f"🎯 Убийств: `{kills}` | 💀 Смертей: `{deaths}` (K/D: `{kd}`)\n"
        f"🌟 MVP Наград: `{p[8]}`\n"
        f"🏆 Побед: `{p[9]}` | ❌ Поражений: `{p[10]}`"
    )
    await message.answer(text, parse_mode="Markdown")

# --- УПРАВЛЕНИЕ КЛАНОМ ---
@dp.message(F.text == "🛡 Мой Клан")
async def show_my_clan(message: types.Message):
    p = get_player(message.from_user.id)
    if not p or p[2] == 0:
        await message.answer("⚠️ Вы не состоите ни в одном клане. Создайте свой через меню «⚙️ Управление Кланом».")
        return
    
    c = get_clan(p[2])
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT nickname, role FROM players WHERE clan_id = ?", (c[0],))
    members = cur.fetchall()
    conn.close()

    members_str = "\n".join([f"• `{m[0]}` — _{m[1]}_" for m in members])

    text = (
        f"🛡 **ИНФОРМАЦИЯ О КЛАНЕ**\n\n"
        f"🏷 Название: **{c[2]}** `[{c[1]}]`\n"
        f"📊 Elo Рейтинг: `{c[4]}`\n"
        f"🔥 Винстрик: `{c[7]}` матчей подряд\n"
        f"🏆 Побед: `{c[5]}` | ❌ Поражений: `{c[6]}`\n\n"
        f"👥 **Состав клана ({len(members)} игроков):**\n{members_str}"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⚙️ Управление Кланом")
async def clan_management(message: types.Message, state: FSMContext):
    clan = get_clan_by_leader(message.from_user.id)
    if not clan:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Создать новый клан", callback_data="create_clan_start")]
        ])
        await message.answer("⚙️ Вы не являетесь лидером клана.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Участники ростера", callback_data="clan_roster")],
            [InlineKeyboardButton(text="🖼 Изменить аватарку", callback_data="clan_avatar")],
            [InlineKeyboardButton(text="❌ Распустить клан", callback_data="clan_disband")]
        ])
        await message.answer(f"⚙️ **Панель управления кланом [{clan[1]}]**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "create_clan_start")
async def create_clan_callback(call: types.CallbackQuery, state: FSMContext):
    p = get_player(call.from_user.id)
    if not p:
        await call.answer("⚠️ Сначала зарегистрируйтесь как игрок!", show_alert=True)
        return
    if p[2] != 0:
        await call.answer("⚠️ Вы уже состоите в клане!", show_alert=True)
        return
    
    await call.message.answer("🏷 Введите **тег клана** (например: `NEM`, `AVANGAR` - до 6 символов):", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(CreateClanState.waiting_for_tag)
    await call.answer()

@dp.message(CreateClanState.waiting_for_tag)
async def process_clan_tag(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    tag = message.text.strip().upper()
    if len(tag) > 6:
        await message.answer("⚠️ Тег не должен превышать 6 символов!")
        return
    await state.update_data(tag=tag)
    await message.answer("🛡 Введите **полное название клана**:", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(CreateClanState.waiting_for_name)

@dp.message(CreateClanState.waiting_for_name)
async def process_clan_name(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    name = message.text.strip()
    data = await state.get_data()
    tag = data["tag"]

    try:
        conn = sqlite3.connect("database.db")
        cur = conn.cursor()
        cur.execute("INSERT INTO clans (clan_tag, clan_name, leader_id) VALUES (?, ?, ?)", (tag, name, message.from_user.id))
        clan_id = cur.lastrowid
        cur.execute("UPDATE players SET clan_id = ? WHERE user_id = ?", (clan_id, message.from_user.id))
        conn.commit()
        conn.close()

        await state.clear()
        await message.answer(f"🎉 Клан **{name}** `[{tag}]` успешно создан!", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))
    except sqlite3.IntegrityError:
        await message.answer("⚠️ Клан с таким тегом уже существует! Введите другой тег.")
        await state.set_state(CreateClanState.waiting_for_tag)

# --- ПОИСК ПРАКА И VETO ---
@dp.message(F.text == "⚔️ Найти Прак (Кланы)")
async def search_clan_practice(message: types.Message, state: FSMContext):
    clan = get_clan_by_leader(message.from_user.id)
    if not clan:
        await message.answer("⚠️ Искать прак от имени клана может только Капитан!")
        return

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="5x5"), KeyboardButton(text="2x2")],
        [KeyboardButton(text="❌ Отмена")]
    ], resize_keyboard=True)
    await message.answer("🎮 Выберите **формат прака**:", reply_markup=kb)
    await state.set_state(PracticeSearch.waiting_for_mode)

@dp.message(PracticeSearch.waiting_for_mode)
async def process_clan_mode(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    await state.update_data(mode=message.text.strip())
    await message.answer("⏳ Укажите **время прака** (например: `19:00` или `Прямо сейчас`):", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(PracticeSearch.waiting_for_time)

@dp.message(PracticeSearch.waiting_for_time)
async def process_clan_time(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    time_slot = message.text.strip()
    data = await state.get_data()
    mode = data["mode"]
    
    clan = get_clan_by_leader(message.from_user.id)
    clan_id = clan[0]
    await state.clear()

    q_key = f"{mode}_{time_slot}"

    if q_key in queues and queues[q_key] != clan_id:
        opp_clan_id = queues.pop(q_key)
        
        if is_blocked(clan_id, opp_clan_id) or is_blocked(opp_clan_id, clan_id):
            await message.answer("⚠️ Найден противник из вашего черного списка. Поиск продолжен...")
            queues[q_key] = clan_id
            return

        match_id = generate_short_id()
        c1 = get_clan(opp_clan_id)
        c2 = get_clan(clan_id)

        matches[match_id] = {
            "c1_id": c1[0], "c2_id": c2[0],
            "c1_tag": c1[1], "c2_tag": c2[1],
            "p1_leader": c1[3], "p2_leader": c2[3],
            "mode": mode, "maps": MAPS_LIST.copy(), "turn": c1[3]
        }

        poster_bio = create_vs_poster(c1[8], c2[8], c1[1], c2[1])

        caption = (
            f"🔥 **КЛАНОВЫЙ ПРАК НАЙДЕН! [{mode}]** 🔥\n\n"
            f"🛡 **{c1[2]}** [{c1[1]}] (Elo: {c1[4]})\n"
            f"⚔️ **VS** ⚔️\n"
            f"🛡 **{c2[2]}** [{c2[1]}] (Elo: {c2[4]})\n\n"
            f"🎯 Начинаем фазу банов карт (Veto)!"
        )

        await bot.send_photo(c1[3], photo=types.BufferedInputFile(poster_bio.getvalue(), filename="vs.png"), caption=caption, parse_mode="Markdown")
        await bot.send_photo(c2[3], photo=types.BufferedInputFile(poster_bio.getvalue(), filename="vs.png"), caption=caption, parse_mode="Markdown")
        
        await send_veto_turn(match_id)
    else:
        queues[q_key] = clan_id
        await message.answer(f"🔍 Клан **[{clan[1]}]** добавлен в очередь **{mode}** ({time_slot}). Ожидаем соперника...", reply_markup=main_keyboard(message.from_user.id))

# --- ФАЗА VETO ---
async def send_veto_turn(match_id):
    m = matches.get(match_id)
    if not m:
        return

    turn_user = m["turn"]
    other_user = m["p2_leader"] if turn_user == m["p1_leader"] else m["p1_leader"]

    if len(m["maps"]) == 1:
        final_map = m["maps"][0]
        m["final_map"] = final_map
        
        res_text = (
            f"🏁 **VETO ЗАВЕРШЕНО!**\n\n"
            f"🗺 Игровая карта: **{final_map}**\n\n"
            f"Заходите в лобби Standoff 2. После игры нажмите кнопку ниже и пришлите скриншот результатов!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📸 Отправить скриншот матча", callback_data=f"upscreen_{match_id}")],
            [InlineKeyboardButton(text="⚠️ Пожаловаться", callback_data=f"rep_{match_id}")]
        ])
        await bot.send_message(m["p1_leader"], res_text, reply_markup=kb, parse_mode="Markdown")
        await bot.send_message(m["p2_leader"], res_text, reply_markup=kb, parse_mode="Markdown")
        return

    kb_list = []
    for mp in m["maps"]:
        kb_list.append([InlineKeyboardButton(text=f"❌ Банить карту {mp}", callback_data=f"ban_{match_id}_{mp}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)

    await bot.send_message(turn_user, f"🎯 **Ваша очередь банить карту!**\nОстались: `{', '.join(m['maps'])}`", reply_markup=kb, parse_mode="Markdown")
    await bot.send_message(other_user, f"⏳ Соперник выбирает карту для бана...\nОстались: `{', '.join(m['maps'])}`", parse_mode="Markdown")

@dp.callback_query(F.data.startswith("ban_"))
async def handle_map_ban(call: types.CallbackQuery):
    _, match_id, map_name = call.data.split("_")
    m = matches.get(match_id)
    
    if not m or call.from_user.id != m["turn"]:
        await call.answer("⚠️ Сейчас не ваша очередь банить карту!", show_alert=True)
        return

    if map_name in m["maps"]:
        m["maps"].remove(map_name)
    
    m["turn"] = m["p2_leader"] if m["turn"] == m["p1_leader"] else m["p1_leader"]
    await call.answer(f"Карта {map_name} успешно забанена!")
    await send_veto_turn(match_id)

# --- ИСТОРИЯ И ТОП ---
@dp.message(F.text == "📜 История матчей")
async def show_history(message: types.Message):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT opponent_nick, map_name, result, elo_change, date FROM match_history WHERE user_id = ? ORDER BY id DESC LIMIT 5", (message.from_user.id,))
    history = cursor.fetchall()
    conn.close()

    if not history:
        await message.answer("📜 Ваша персональная история матчей пока пуста.")
        return

    text = "📜 **ПОСЛЕДНИЕ СРАЖЕНИЯ:**\n\n"
    for opp, map_n, res, elo, dt in history:
        icon = "🟢" if res == "ПОБЕДА" else "🔴"
        text += f"{icon} **vs {opp}** | Карта: `{map_n}`\nСтатус: {res} ({elo:+d} Elo) | `{dt}`\n\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🏆 Топ Кланов")
async def show_clan_top(message: types.Message):
    top = get_top_clans()
    text = "🏆 **ГЛОБАЛЬНЫЙ ТОП-10 КЛАНОВ:**\n\n"
    for i, (tag, name, elo, w, l, streak) in enumerate(top, 1):
        text += f"**{i}. [{tag}] {name}** — `{elo} Elo` | 🔥 Стрик: `{streak}` | (В: {w} / П: {l})\n"
    await message.answer(text, parse_mode="Markdown")

# --- АНАЛИЗ СКРИНШОТА ЧЕРЕЗ ИИ ---
@dp.callback_query(F.data.startswith("upscreen_"))
async def trigger_screen_upload(call: types.CallbackQuery, state: FSMContext):
    match_id = call.data.split("_")[1]
    await state.update_data(active_match_id=match_id)
    await call.message.answer("📸 Пришлите **скриншот таблицы результатов** из Standoff 2 для автоматического анализа матча:")
    await call.answer()

@dp.message(F.photo)
async def handle_screenshot_ai(message: types.Message, state: FSMContext):
    data = await state.get_data()
    match_id = data.get("active_match_id")

    if not match_id:
        return

    if not GEMINI_KEY:
        await message.answer("⚠️ GEMINI_API_KEY не настроен в системе.")
        return

    await message.answer("🤖 **ИИ-Арбитр анализирует таблицу результатов матча...**")

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    photo_bytes = await bot.download_file(file_info.file_path)

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = "Проанализируй скриншот результатов Standoff 2. Назови победителя, счет матча, лучшие киллы и MVP."
        
        image_part = {"mime_type": "image/jpeg", "data": photo_bytes.read()}
        response = model.generate_content([prompt, image_part])
        
        await message.answer(f"📊 **ОТЧЕТ ИИ-АРБИТРА ПО МАТЧУ:**\n\n{response.text}", parse_mode="Markdown")
        await state.clear()
    except Exception as e:
        logging.error(f"Ошибка ИИ: {e}")
        await message.answer("⚠️ Не удалось прочитать скриншот. Отчет передан администрации.")

async def handle(request):
    return web.Response(text="Standoff 2 Clan Bot Pro is Running!")

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
