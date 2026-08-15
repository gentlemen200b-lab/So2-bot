import asyncio
import logging
import os
import random
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# --- СПИСОК КАРТ ДЛЯ VETO ---
MAPS_LIST = ["Sandstone", "Province", "Rust", "Dune", "Hanami", "Prison", "Breeze"]

queues = {}     # { time_slot: user_id }
matches = {}    # { match_id: data }

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT NOT NULL,
            game_id TEXT NOT NULL,
            clan_tag TEXT DEFAULT 'Нет',
            elo INTEGER DEFAULT 1000
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clans (
            clan_tag TEXT PRIMARY KEY,
            captain_id INTEGER NOT NULL,
            captain_game_id TEXT NOT NULL,
            roster TEXT NOT NULL,
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

def add_clan(clan_tag, captain_id, captain_game_id, roster):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO clans (clan_tag, captain_id, captain_game_id, roster, elo)
        VALUES (?, ?, ?, ?, COALESCE((SELECT elo FROM clans WHERE clan_tag = ?), 1000))
    """, (clan_tag, captain_id, captain_game_id, roster, clan_tag))
    conn.commit()
    conn.close()

def update_elo(user_id, delta):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE players SET elo = elo + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def get_player(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, game_id, clan_tag, elo FROM players WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_top_players():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, clan_tag, elo FROM players ORDER BY elo DESC LIMIT 10")
    res = cursor.fetchall()
    conn.close()
    return res

def get_top_clans():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT clan_tag, elo FROM clans ORDER BY elo DESC LIMIT 10")
    res = cursor.fetchall()
    conn.close()
    return res

# --- FSM СОСТОЯНИЯ ---
class Registration(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_game_id = State()
    waiting_for_clan_tag = State()

class ClanRegistration(StatesGroup):
    waiting_for_tag = State()
    waiting_for_captain_id = State()
    waiting_for_roster = State()

class PracticeSearch(StatesGroup):
    waiting_for_time = State()

# --- ИНИЦИАЛИЗА ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def main_keyboard():
    kb = [
        [KeyboardButton(text="⚔️ Найти Прак"), KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="📝 Регистрация / Профиль"), KeyboardButton(text="🛡 Регистрация Клана")],
        [KeyboardButton(text="🏆 Топ Игроков"), KeyboardButton(text="🛡 Топ Кланов")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- START ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "⚔️ **КИБЕРСПОРТИВНАЯ АРЕНА «БИТВА»** ⚔️\n\n"
        "Добро пожаловать в систему поиска праков, бана карт (Veto) и расчета Elo Standoff 2!\n\n"
        "Воспользуйтесь меню ниже для навигации:"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_keyboard())

# --- РЕГИСТРАЦИЯ ИГРОКА ---
@dp.message(F.text == "📝 Регистрация / Профиль")
async def start_registration(message: types.Message, state: FSMContext):
    await message.answer("📝 **Шаг 1/3:** Введите ваш **Никнейм**:", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Registration.waiting_for_nickname)
async def process_nickname(message: types.Message, state: FSMContext):
    await state.update_data(nickname=message.text.strip())
    await message.answer("🆔 **Шаг 2/3:** Введите ваш **Игровой ID** (числа):", parse_mode="Markdown")
    await state.set_state(Registration.waiting_for_game_id)

@dp.message(Registration.waiting_for_game_id)
async def process_game_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ ID должен состоять только из цифр!")
        return
    await state.update_data(game_id=message.text.strip())
    skip_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True)
    await message.answer("🏷 **Шаг 3/3:** Введите ваш **Клан-тег** или нажмите **Пропустить**:", parse_mode="Markdown", reply_markup=skip_kb)
    await state.set_state(Registration.waiting_for_clan_tag)

@dp.message(Registration.waiting_for_clan_tag)
async def process_clan_tag(message: types.Message, state: FSMContext):
    clan_tag = "Нет" if message.text == "Пропустить" else message.text.strip()
    data = await state.get_data()
    add_player(message.from_user.id, data["nickname"], data["game_id"], clan_tag)
    await state.clear()
    await message.answer("🎉 **Регистрация успешно завершена!** Стартовый Elo: 1000", parse_mode="Markdown", reply_markup=main_keyboard())

# --- РЕГИСТРАЦИЯ КЛАНА ---
@dp.message(F.text == "🛡 Регистрация Клана")
async def start_clan_reg(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if not p:
        await message.answer("⚠️ Сначала зарегистрируйте личный профиль!")
        return
    await message.answer("🛡 **Шаг 1/3:** Введите **Клан-тег** (например, `[ABC]`):", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await state.set_state(ClanRegistration.waiting_for_tag)

@dp.message(ClanRegistration.waiting_for_tag)
async def process_c_tag(message: types.Message, state: FSMContext):
    await state.update_data(clan_tag=message.text.strip())
    await message.answer("🆔 **Шаг 2/3:** Введите **Игровой ID Капитана**:", parse_mode="Markdown")
    await state.set_state(ClanRegistration.waiting_for_captain_id)

@dp.message(ClanRegistration.waiting_for_captain_id)
async def process_c_cap(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ ID должен состоять из цифр!")
        return
    await state.update_data(captain_game_id=message.text.strip())
    await message.answer("📜 **Шаг 3/3:** Отправьте список состава игроков:", parse_mode="Markdown")
    await state.set_state(ClanRegistration.waiting_for_roster)

@dp.message(ClanRegistration.waiting_for_roster)
async def process_c_roster(message: types.Message, state: FSMContext):
    data = await state.get_data()
    add_clan(data["clan_tag"], message.from_user.id, data["captain_game_id"], message.text.strip())
    await state.clear()
    await message.answer("✅ **Клан успешно внесен в реестр турниров!**", parse_mode="Markdown", reply_markup=main_keyboard())

# --- ПОИСК ПРАКОВ ---
@dp.message(F.text == "⚔️ Найти Прак")
async def search_practice(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if not p:
        await message.answer("⚠️ Зарегистрируйтесь перед поиском праков!")
        return
    await message.answer("⏳ Укажите **время прака** (например: `18:00`, `20:00` или `21:30`):", parse_mode="Markdown")
    await state.set_state(PracticeSearch.waiting_for_time)

@dp.message(PracticeSearch.waiting_for_time)
async def process_practice_time(message: types.Message, state: FSMContext):
    time_slot = message.text.strip()
    user_id = message.from_user.id
    await state.clear()

    if time_slot in queues and queues[time_slot] != user_id:
        opponent_id = queues.pop(time_slot)
        match_id = f"{user_id}_{opponent_id}_{time_slot}"
        
        matches[match_id] = {
            "p1": opponent_id,
            "p2": user_id,
            "ready": {opponent_id: False, user_id: False},
            "turn": opponent_id,
            "maps": MAPS_LIST.copy(),
            "time": time_slot
        }

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ READY", callback_data=f"ready_yes_{match_id}"),
            InlineKeyboardButton(text="❌ CANCEL", callback_data=f"ready_no_{match_id}")
        ]])

        p1_data = get_player(opponent_id)
        p2_data = get_player(user_id)

        msg_p1 = f"🎯 **Соперник найден на {time_slot}!**\n\n👑 Капитан соперников: **{p2_data[0]}** (`ID: {p2_data[1]}`)\n🏷 Клан: `{p2_data[2]}` | Elo: `{p2_data[3]}`\n\nПодтвердите готовность:"
        msg_p2 = f"🎯 **Соперник найден на {time_slot}!**\n\n👑 Капитан соперников: **{p1_data[0]}** (`ID: {p1_data[1]}`)\n🏷 Клан: `{p1_data[2]}` | Elo: `{p1_data[3]}`\n\nПодтвердите готовность:"

        await bot.send_message(opponent_id, msg_p1, parse_mode="Markdown", reply_markup=kb)
        await message.answer(msg_p2, parse_mode="Markdown", reply_markup=kb)
    else:
        queues[time_slot] = user_id
        await message.answer(f"🔍 Вы встали в очередь поиска прака на **{time_slot}**.\nОжидайте подключения соперников!", parse_mode="Markdown")

# --- ГОТОВНОСТЬ (READY) ---
@dp.callback_query(F.data.startswith("ready_"))
async def handle_ready(call: types.CallbackQuery):
    action, choice, match_id = call.data.split("_")
    if match_id not in matches:
        await call.answer("Матч не найден или отменен.", show_alert=True)
        return

    m = matches[match_id]
    user_id = call.from_user.id

    if choice == "no":
        await bot.send_message(m["p1"], "❌ Прак был отменен одним из капитанов.")
        await bot.send_message(m["p2"], "❌ Прак был отменен одним из капитанов.")
        del matches[match_id]
        return

    m["ready"][user_id] = True
    await call.answer("Готовность принята!")

    if m["ready"][m["p1"]] and m["ready"][m["p2"]]:
        await start_veto(match_id)

# --- VETO / БАН КАРТ С ВЫБОРОМ ХОСТА ---
async def start_veto(match_id):
    m = matches[match_id]
    p1_data = get_player(m["p1"])
    p2_data = get_player(m["p2"])

    # Случайный выбор хоста лобби
    host_id = random.choice([m["p1"], m["p2"]])
    host_data = p1_data if host_id == m["p1"] else p2_data
    m["host_data"] = host_data

    turn_player = p1_data[0]

    buttons = [[InlineKeyboardButton(text=f"🚫 {mp}", callback_data=f"ban_{mp}_{match_id}")] for mp in m["maps"]]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = (
        f"⚔️ **ФАЗА БАНА КАРТ (VETO)**\n\n"
        f"👑 **Капитан 1:** {p1_data[0]} (`ID: {p1_data[1]}`)\n"
        f"👑 **Капитан 2:** {p2_data[0]} (`ID: {p2_data[1]}`)\n\n"
        f"🏠 **ХОСТ ЛОББИ:** **{host_data[0]}** (Игровой ID: `{host_data[1]}`)\n"
        f"📌 *Отправьте ему точку / приглашение в игре!*\n\n"
        f"📊 **Регламент:** Игра до **13 раундов** (MR12)\n"
        f"Остались карты: {', '.join(m['maps'])}\n\n"
        f"👉 Первым банит: **{turn_player}**"
    )
    
    await bot.send_message(m["p1"], text, parse_mode="Markdown", reply_markup=kb)
    await bot.send_message(m["p2"], text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("ban_"))
async def handle_ban(call: types.CallbackQuery):
    _, map_name, match_id = call.data.split("_")
    if match_id not in matches:
        return

    m = matches[match_id]
    user_id = call.from_user.id

    if user_id != m["turn"]:
        await call.answer("Сейчас очередь банить у капитана соперников!", show_alert=True)
        return

    if map_name in m["maps"]:
        m["maps"].remove(map_name)

    if len(m["maps"]) == 1:
        final_map = m["maps"][0]
        m["final_map"] = final_map
        host_data = m["host_data"]
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏁 Завершить матч и сдать результат", callback_data=f"finish_{match_id}")]])
        
        text = (
            f"🔥 **КАРТА МАТЧА ОПРЕДЕЛЕНА!**\n\n"
            f"📍 Карта: **{final_map}**\n"
            f"⏱ Формат: **До 13 раундов (MR12)**\n\n"
            f"🏠 **Хост лобби:** **{host_data[0]}**\n"
            f"🆔 **ID Хоста:** `{host_data[1]}` (кидайте заявку/точку сюда)\n\n"
            f"Удачи в игре! После завершения матча нажимите кнопку ниже:"
        )
        await bot.send_message(m["p1"], text, parse_mode="Markdown", reply_markup=kb)
        await bot.send_message(m["p2"], text, parse_mode="Markdown", reply_markup=kb)
    else:
        m["turn"] = m["p2"] if m["turn"] == m["p1"] else m["p1"]
        next_player = get_player(m["turn"])[0]

        buttons = [[InlineKeyboardButton(text=f"🚫 {mp}", callback_data=f"ban_{mp}_{match_id}")] for mp in m["maps"]]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        text = f"⚔️ Карта **{map_name}** забанена!\n\nОстались карты: {', '.join(m['maps'])}\n👉 Ход бана: **{next_player}**"
        await bot.send_message(m["p1"], text, parse_mode="Markdown", reply_markup=kb)
        await bot.send_message(m["p2"], text, parse_mode="Markdown", reply_markup=kb)

# --- ФИНАЛ И СКРИНШОТЫ ---
@dp.callback_query(F.data.startswith("finish_"))
async def handle_finish(call: types.CallbackQuery):
    _, match_id = call.data.split("_")
    await call.message.answer("🤝 Отличная игра! Пожалуйста, отправьте скриншот с итоговым счетом матча (до 13 раундов):")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Победил Я", callback_data=f"win_me_{match_id}")],
        [InlineKeyboardButton(text="💀 Победил СОПЕРНИК", callback_data=f"win_op_{match_id}")]
    ])
    await call.message.answer("Кто одержал победу в этом матче?", reply_markup=kb)

@dp.callback_query(F.data.startswith("win_"))
async def process_match_winner(call: types.CallbackQuery):
    _, result, match_id = call.data.split("_")
    if match_id not in matches:
        await call.answer("Результат уже зарегистрирован!")
        return

    m = matches[match_id]
    sender_id = call.from_user.id
    winner_id = sender_id if result == "me" else (m["p2"] if sender_id == m["p1"] else m["p1"])
    loser_id = m["p2"] if winner_id == m["p1"] else m["p1"]

    update_elo(winner_id, 25)
    update_elo(loser_id, -25)

    w_p = get_player(winner_id)
    l_p = get_player(loser_id)

    res_text = (
        f"📊 **МАТЧ УСПЕШНО ЗАРЕГИСТРИРОВАН!**\n\n"
        f"🏆 Победитель: **{w_p[0]}** (+25 Elo) 📈\n"
        f"💀 Поражение: **{l_p[0]}** (-25 Elo) 📉\n\n"
        f"Рейтинги обновлены!"
    )

    await bot.send_message(m["p1"], res_text, parse_mode="Markdown")
    await bot.send_message(m["p2"], res_text, parse_mode="Markdown")
    del matches[match_id]

# --- ПРОФИЛЬ И ТОПЫ ---
@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message):
    player = get_player(message.from_user.id)
    if player:
        await message.answer(f"📊 **ПРОФИЛЬ**\n\nНик: `{player[0]}`\nID: `{player[1]}`\nКлан: `{player[2]}`\n⚡️ Elo: `{player[3]}`", parse_mode="Markdown")
    else:
        await message.answer("⚠️ Вы еще не зарегистрированы!")

@dp.message(F.text == "🏆 Топ Игроков")
async def show_top_players(message: types.Message):
    top = get_top_players()
    text = "🏆 **ТОП-10 ИГРОКОВ PO ELO:**\n\n"
    for i, (nick, clan, elo) in enumerate(top, 1):
        text += f"**{i}. {nick}** — `{elo} Elo`\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🛡 Топ Кланов")
async def show_top_clans(message: types.Message):
    top = get_top_clans()
    text = "🛡 **ТОП КЛАНОВ PO ELO:**\n\n"
    for i, (tag, elo) in enumerate(top, 1):
        text += f"**{i}. {tag}** — `{elo} Elo`\n"
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
