import asyncio
import logging
import os
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# --- НАСТРОЙКА БАЗЫ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT NOT NULL,
            game_id TEXT NOT NULL,
            clan_tag TEXT DEFAULT 'Нет',
            rating INTEGER DEFAULT 1000
        )
    """)
    conn.commit()
    conn.close()

def add_player(user_id, nickname, game_id, clan_tag):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO players (user_id, nickname, game_id, clan_tag, rating)
        VALUES (?, ?, ?, ?, COALESCE((SELECT rating FROM players WHERE user_id = ?), 1000))
    """, (user_id, nickname, game_id, clan_tag, user_id))
    conn.commit()
    conn.close()

def get_player(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, game_id, clan_tag, rating FROM players WHERE user_id = ?", (user_id,))
    player = cursor.fetchone()
    conn.close()
    return player

# --- СОСТОЯНИЯ РЕГИСТРАЦИИ (FSM) ---
class Registration(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_game_id = State()
    waiting_for_clan_tag = State()

# --- ИНИЦИАЛИЗА БОТА ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- КНОПКИ ---
def main_keyboard():
    kb = [
        [KeyboardButton(text="📝 Регистрация / Изменить профиль")],
        [KeyboardButton(text="👤 Мой профиль")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ОБРАБОТЧИКИ КОМАНД ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 **Привет! Добро пожаловать в проект «Битва»!**\n\n"
        "Этот бот предназначен для учета матчей, подсчета рейтинга игроков и отслеживания вашей статистики.\n\n"
        "Чтобы участвовать в матчах и попадать в общий рейтинг, пройдите быструю регистрацию.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "📝 Регистрация / Изменить профиль")
async def start_registration(message: types.Message, state: FSMContext):
    await message.answer(
        "Шаг 1/3: Введите ваш **игровой никнейм**:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Registration.waiting_for_nickname)
async def process_nickname(message: types.Message, state: FSMContext):
    await state.update_data(nickname=message.text.strip())
    await message.answer("Шаг 2/3: Введите ваш **игровой ID** (числа из профиля):", parse_mode="Markdown")
    await state.set_state(Registration.waiting_for_game_id)

@dp.message(Registration.waiting_for_game_id)
async def process_game_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Игровой ID должен состоять только из цифр! Попробуйте еще раз:")
        return
    await state.update_data(game_id=message.text.strip())

    skip_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True
    )
    await message.answer(
        "Шаг 3/3: Введите ваш **клан-тег** (например, `[ABC]`) или нажмите **Пропустить**:",
        parse_mode="Markdown",
        reply_markup=skip_kb
    )
    await state.set_state(Registration.waiting_for_clan_tag)

@dp.message(Registration.waiting_for_clan_tag)
async def process_clan_tag(message: types.Message, state: FSMContext):
    clan_tag = "Нет" if message.text == "Пропустить" else message.text.strip()
    data = await state.get_data()
    
    add_player(
        user_id=message.from_user.id,
        nickname=data["nickname"],
        game_id=data["game_id"],
        clan_tag=clan_tag
    )
    
    await state.clear()
    await message.answer(
        f"✅ **Регистрация успешно завершена!**\n\n"
        f"👤 **Ник:** {data['nickname']}\n"
        f"🆔 **ID:** {data['game_id']}\n"
        f"🏷 **Клан-тег:** {clan_tag}\n"
        f"🏆 **Начальный рейтинг:** 1000 MMR\n\n"
        f"Теперь вы автоматически состоите в системе рейтинга!",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message):
    player = get_player(message.from_user.id)
    if player:
        nickname, game_id, clan_tag, rating = player
        await message.answer(
            f"📊 **Ваш профиль:**\n\n"
            f"👤 **Ник:** {nickname}\n"
            f"🆔 **ID:** {game_id}\n"
            f"🏷 **Клан:** {clan_tag}\n"
            f"🏆 **Рейтинг:** {rating} MMR",
            parse_mode="Markdown"
        )
    else:
        await message.answer("⚠️ Вы еще не зарегистрированы. Нажмите кнопку **📝 Регистрация / Изменить профиль** ниже.")

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Bot is running!")

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

