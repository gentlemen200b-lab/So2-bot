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

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    # Таблица игроков
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT NOT NULL,
            game_id TEXT NOT NULL,
            clan_tag TEXT DEFAULT 'Нет',
            elo INTEGER DEFAULT 1000
        )
    """)
    # Таблица кланов/команд
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clans (
            clan_tag TEXT PRIMARY KEY,
            captain_id INTEGER,
            roster TEXT,
            elo INTEGER DEFAULT 1000
        )
    """)
    conn.commit()
    conn.close()

def add_player(user_id, nickname, game_id, clan_tag):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO players (user_id, nickname, game_id, clan_tag, elo)
        VALUES (?, ?, ?, ?, COALESCE((SELECT elo FROM players WHERE user_id = ?), 1000))
    """, (user_id, nickname, game_id, clan_tag, user_id))
    conn.commit()
    conn.close()

def get_player(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, game_id, clan_tag, elo FROM players WHERE user_id = ?", (user_id,))
    player = cursor.fetchone()
    conn.close()
    return player

def get_top_players():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, clan_tag, elo FROM players ORDER BY elo DESC LIMIT 10")
    top = cursor.fetchall()
    conn.close()
    return top

def get_top_clans():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_tag, elo FROM clans ORDER BY elo DESC LIMIT 10")
    top = cursor.fetchall()
    conn.close()
    return top

# --- FSM СОСТОЯНИЯ ---
class Registration(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_game_id = State()
    waiting_for_clan_tag = State()

# --- ИНИЦИАЛИЗА ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- КЛАВИАТУРА ---
def main_keyboard():
    kb = [
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="📝 Регистрация / Профиль")],
        [KeyboardButton(text="🏆 Топ Игроков"), KeyboardButton(text="🛡 Топ Кланов")],
        [KeyboardButton(text="⚔️ Найти Прак (В разработке)")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "⚔️ **ДОБРО ПОЖАЛОВАТЬ НА АРЕНУ «БИТВА»** ⚔️\n\n"
        "🏛 *Официальная киберспортивная система праков и рейтингов Standoff 2.*\n\n"
        "Здесь вы можете:\n"
        "▫️ Зарегистрировать свой профиль и клан\n"
        "▫️ Находить праки и участвовать в турнирах\n"
        "▫️ Банить карты в Veto-режиме\n"
        "▫️ Поднимать личный и клановый **Elo рейтинг**\n\n"
        "👇 *Воспользуйтесь меню ниже для навигации:*"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_keyboard())

@dp.message(F.text == "📝 Регистрация / Профиль")
async def start_registration(message: types.Message, state: FSMContext):
    await message.answer(
        "📝 **Шаг 1/3:** Введите ваш игровой **Никнейм**:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Registration.waiting_for_nickname)
async def process_nickname(message: types.Message, state: FSMContext):
    await state.update_data(nickname=message.text.strip())
    await message.answer("🆔 **Шаг 2/3:** Введите ваш **Игровой ID** (только цифры):", parse_mode="Markdown")
    await state.set_state(Registration.waiting_for_game_id)

@dp.message(Registration.waiting_for_game_id)
async def process_game_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ **ID должен состоять только из цифр!** Попробуйте еще раз:")
        return
    await state.update_data(game_id=message.text.strip())

    skip_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True
    )
    await message.answer(
        "🏷 **Шаг 3/3:** Введите ваш **Клан-тег** (например, `[ABC]`) или нажмите **Пропустить**:",
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
        f"🎉 **РЕГИСТРАЦИЯ УСПЕШНО ЗАВЕРШЕНА!**\n\n"
        f"👤 **Ник:** `{data['nickname']}`\n"
        f"🆔 **ID:** `{data['game_id']}`\n"
        f"🏷 **Клан:** `{clan_tag}`\n"
        f"⚡️ **Стартовый Elo:** `1000`\n\n"
        f"🔥 Вы внесены в единую систему рейтинга!",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message):
    player = get_player(message.from_user.id)
    if player:
        nickname, game_id, clan_tag, elo = player
        await message.answer(
            f"📊 **ЛИЧНЫЙ ПРОФИЛЬ ИГРОКА**\n\n"
            f"👤 **Никнейм:** `{nickname}`\n"
            f"🆔 **Игровой ID:** `{game_id}`\n"
            f"🏷 **Клан:** `{clan_tag}`\n"
            f"⚡️ **Рейтинг Elo:** `{elo}`",
            parse_mode="Markdown"
        )
    else:
        await message.answer("⚠️ Вы еще не зарегистрированы. Нажмите **📝 Регистрация / Профиль**.")

@dp.message(F.text == "🏆 Топ Игроков")
async def show_top_players(message: types.Message):
    top = get_top_players()
    if not top:
        await message.answer("🏆 **Топ игроков пока пуст. Будь первым!**", parse_mode="Markdown")
        return
    
    text = "🏆 **ТОП-10 ИГРОКОВ ПО ELO:**\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, (nick, clan, elo) in enumerate(top):
        clan_str = f"[{clan}] " if clan != "Нет" else ""
        text += f"{medals[i]} **{clan_str}{nick}** — `{elo} Elo`\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🛡 Топ Кланов")
async def show_top_clans(message: types.Message):
    top = get_top_clans()
    if not top:
        await message.answer("🛡 **Зарегистрированных кланов пока нет.**", parse_mode="Markdown")
        return
    
    text = "🛡 **ТОП КЛАНОВ ПО ELO:**\n\n"
    for i, (clan_tag, elo) in enumerate(top, 1):
        text += f"**{i}. {clan_tag}** — `{elo} Elo`\n"
    
    await message.answer(text, parse_mode="Markdown")

# --- СЕРВЕР ---
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


