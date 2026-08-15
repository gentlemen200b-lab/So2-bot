import asyncio
import logging
import os
import random
import sqlite3
import string
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

# Укажите ваш Telegram ID для доступа к админ-панели
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MAPS_LIST = ["Sandstone", "Province", "Rust", "Dune", "Hanami", "Prison", "Breeze"]

queues = {}     # { "5x5_18:00": user_id }
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
            elo INTEGER DEFAULT 1000,
            device TEXT DEFAULT 'Не указано',
            role TEXT DEFAULT 'Универсал',
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            winstreak INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
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

def add_player(user_id, nickname, game_id, clan_tag, device, role):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO players (user_id, nickname, game_id, clan_tag, device, role)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            nickname=excluded.nickname,
            game_id=excluded.game_id,
            clan_tag=excluded.clan_tag,
            device=excluded.device,
            role=excluded.role
    """, (user_id, nickname, game_id, clan_tag, device, role))
    conn.commit()
    conn.close()

def get_player(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, game_id, clan_tag, elo, device, role, wins, losses, winstreak, is_banned FROM players WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def update_player_field(user_id, field, value):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(f"UPDATE players SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def record_match_result(winner_id, loser_id, map_name):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
    # Победитель
    cursor.execute("UPDATE players SET elo = elo + 25, wins = wins + 1, winstreak = winstreak + 1 WHERE user_id = ?", (winner_id,))
    # Проигравший
    cursor.execute("UPDATE players SET elo = MAX(0, elo - 25), losses = losses + 1, winstreak = 0 WHERE user_id = ?", (loser_id,))
    
    # Получаем ники для истории
    cursor.execute("SELECT nickname FROM players WHERE user_id = ?", (winner_id,))
    w_nick = cursor.fetchone()[0]
    cursor.execute("SELECT nickname FROM players WHERE user_id = ?", (loser_id,))
    l_nick = cursor.fetchone()[0]
    
    cursor.execute("INSERT INTO match_history (user_id, opponent_nick, map_name, result, elo_change) VALUES (?, ?, ?, 'ПОБЕДА', 25)", (winner_id, l_nick, map_name))
    cursor.execute("INSERT INTO match_history (user_id, opponent_nick, map_name, result, elo_change) VALUES (?, ?, ?, 'ПОРАЖЕНИЕ', -25)", (loser_id, w_nick, map_name))
    
    conn.commit()
    conn.close()

def get_match_history(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT opponent_nick, map_name, result, elo_change, date FROM match_history WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user_id,))
    res = cursor.fetchall()
    conn.close()
    return res

def get_all_users():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM players")
    res = cursor.fetchall()
    conn.close()
    return [r[0] for r in res]

def get_top_players():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, clan_tag, elo FROM players WHERE is_banned = 0 ORDER BY elo DESC LIMIT 10")
    res = cursor.fetchall()
    conn.close()
    return res

# --- FSM СОСТОЯНИЯ ---
class Registration(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_game_id = State()
    waiting_for_clan_tag = State()
    waiting_for_device = State()
    waiting_for_role = State()

class EditProfile(StatesGroup):
    waiting_for_field = State()
    waiting_for_value = State()

class PracticeSearch(StatesGroup):
    waiting_for_mode = State()
    waiting_for_time = State()

class AdminBroadcast(StatesGroup):
    waiting_for_text = State()

# --- ИНИЦИАЛИЗА ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def generate_short_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def main_keyboard(user_id):
    kb = [
        [KeyboardButton(text="⚔️ Найти Прак"), KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="⚙️ Редактировать профиль"), KeyboardButton(text="📜 История матчей")],
        [KeyboardButton(text="🏆 Топ Игроков"), KeyboardButton(text="🛡 Регистрация Клана")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="👑 Админ Панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- START ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    p = get_player(message.from_user.id)
    if p and p[9] == 1:
        await message.answer("❌ Вы заблокированы в системе за нарушение правил!")
        return

    text = (
        "⚔️ **КИБЕРСПОРТИВНАЯ АРЕНА «БИТВА» (Standoff 2)** ⚔️\n\n"
        "Платформа поиска праков, проведения Veto, банов карт и автоматического расчета Elo рейтинга.\n\n"
        "Для начала зарегистрируйтесь или воспользуйтесь меню ниже:"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))

# --- РЕГИСТРАЦИЯ ИГРОКА ---
@dp.message(F.text == "📝 Регистрация")
@dp.message(Command("register"))
async def start_registration(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if p:
        await message.answer("⚠️ **Вы уже зарегистрированы!** Для изменения данных используйте «⚙️ Редактировать профиль».", reply_markup=main_keyboard(message.from_user.id))
        return

    await message.answer("📝 **Шаг 1/5:** Введите ваш **Игровой Никнейм**:", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Registration.waiting_for_nickname)
async def process_nickname(message: types.Message, state: FSMContext):
    await state.update_data(nickname=message.text.strip())
    await message.answer("🆔 **Шаг 2/5:** Введите ваш **Игровой ID** (цифрами):", parse_mode="Markdown")
    await state.set_state(Registration.waiting_for_game_id)

@dp.message(Registration.waiting_for_game_id)
async def process_game_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ ID должен состоять только из цифр!")
        return
    await state.update_data(game_id=message.text.strip())
    skip_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True)
    await message.answer("🏷 **Шаг 3/5:** Введите ваш **Клан-тег** или нажмите **Пропустить**:", parse_mode="Markdown", reply_markup=skip_kb)
    await state.set_state(Registration.waiting_for_clan_tag)

@dp.message(Registration.waiting_for_clan_tag)
async def process_clan_tag(message: types.Message, state: FSMContext):
    clan_tag = "Нет" if message.text == "Пропустить" else message.text.strip()
    await state.update_data(clan_tag=clan_tag)
    
    dev_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 Phone (60 FPS)"), KeyboardButton(text="📱 Phone (90 FPS)")],
        [KeyboardButton(text="📱 Phone (120 FPS)"), KeyboardButton(text="📱 iPad (120 FPS)")]
    ], resize_keyboard=True)
    
    await message.answer("📱 **Шаг 4/5:** Выберите ваше **Устройство / FPS**:", parse_mode="Markdown", reply_markup=dev_kb)
    await state.set_state(Registration.waiting_for_device)

@dp.message(Registration.waiting_for_device)
async def process_device(message: types.Message, state: FSMContext):
    await state.update_data(device=message.text.strip())
    
    role_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👑 Captain (IGL)"), KeyboardButton(text="🎯 Sniper (AWP)")],
        [KeyboardButton(text="💥 Entry Fragger"), KeyboardButton(text="🔫 Rifler / Support")]
    ], resize_keyboard=True)
    
    await message.answer("🎯 **Шаг 5/5:** Выберите вашу **Основную роль**:", parse_mode="Markdown", reply_markup=role_kb)
    await state.set_state(Registration.waiting_for_role)

@dp.message(Registration.waiting_for_role)
async def process_role(message: types.Message, state: FSMContext):
    role = message.text.strip()
    data = await state.get_data()
    
    add_player(message.from_user.id, data["nickname"], data["game_id"], data["clan_tag"], data["device"], role)
    await state.clear()
    await message.answer("🎉 **Регистрация успешно завершена!** Ваш стартовый Elo: 1000", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))

# --- РЕДАКТИРОВАНИЕ ПРОФИЛЯ ---
@dp.message(F.text == "⚙️ Редактировать профиль")
async def edit_profile_start(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if not p:
        await message.answer("⚠️ Сначала зарегистрируйтесь!")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить Ник", callback_data="edit_nickname")],
        [InlineKeyboardButton(text="Изменить ID", callback_data="edit_game_id")],
        [InlineKeyboardButton(text="Изменить Клан", callback_data="edit_clan_tag")],
        [InlineKeyboardButton(text="Изменить Девайс", callback_data="edit_device")],
        [InlineKeyboardButton(text="Изменить Роль", callback_data="edit_role")]
    ])
    await message.answer("⚙️ Выберите, какой параметр профиля вы хотите изменить:", reply_markup=kb)

@dp.callback_query(F.data.startswith("edit_"))
async def process_edit_choice(call: types.CallbackQuery, state: FSMContext):
    field = call.data.replace("edit_", "")
    await state.update_data(edit_field=field)
    
    names = {"nickname": "Новый Ник", "game_id": "Новый ID", "clan_tag": "Новый Клан-тег", "device": "Устройство / FPS", "role": "Новую Роль"}
    await call.message.answer(f"Введите **{names[field]}**:", parse_mode="Markdown")
    await state.set_state(EditProfile.waiting_for_value)
    await call.answer()

@dp.message(EditProfile.waiting_for_value)
async def process_edit_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    field = data["edit_field"]
    val = message.text.strip()
    
    if field == "game_id" and not val.isdigit():
        await message.answer("⚠️ ID должен состоять только из цифр!")
        return
        
    update_player_field(message.from_user.id, field, val)
    await state.clear()
    await message.answer("✅ **Данные профиля успешно обновлены!**", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))

# --- ПОИСК ПРАКОВ И VETO ---
@dp.message(F.text == "⚔️ Найти Прак")
async def search_practice_start(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if not p:
        await message.answer("⚠️ Зарегистрируйтесь перед поиском праков! Нажмите /start")
        return

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="5x5"), KeyboardButton(text="2x2"), KeyboardButton(text="1x1")]
    ], resize_keyboard=True)
    await message.answer("🎮 Выберите **формат прака**:", reply_markup=kb)
    await state.set_state(PracticeSearch.waiting_for_mode)

@dp.message(PracticeSearch.waiting_for_mode)
async def process_practice_mode(message: types.Message, state: FSMContext):
    mode = message.text.strip()
    if mode not in ["5x5", "2x2", "1x1"]:
        await message.answer("Выберите вариант из меню!")
        return
    await state.update_data(mode=mode)
    await message.answer("⏳ Укажите **время прака** (например: `18:00`, `20:00` или `Прямо сейчас`):", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await state.set_state(PracticeSearch.waiting_for_time)

@dp.message(PracticeSearch.waiting_for_time)
async def process_practice_time(message: types.Message, state: FSMContext):
    time_slot = message.text.strip()
    data = await state.get_data()
    mode = data["mode"]
    user_id = message.from_user.id
    await state.clear()

    queue_key = f"{mode}_{time_slot}"

    if queue_key in queues and queues[queue_key] != user_id:
        opponent_id = queues.pop(queue_key)
        match_id = generate_short_id()
        
        matches[match_id] = {
            "p1": opponent_id,
            "p2": user_id,
            "mode": mode,
            "ready": {opponent_id: False, user_id: False},
            "claims": {},
            "turn": opponent_id,
            "maps": MAPS_LIST.copy(),
            "time": time_slot
        }

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ READY", callback_data=f"r_y_{match_id}"),
            InlineKeyboardButton(text="❌ CANCEL", callback_data=f"r_n_{match_id}")
        ]])

        p1_data = get_player(opponent_id)
        p2_data = get_player(user_id)

        msg_p1 = f"🎯 **Соперник найден!** ({mode} | {time_slot})\n\n👑 Капитан: **{p2_data[0]}** (`ID: {p2_data[1]}`)\n🏷 Клан: `{p2_data[2]}` | Elo: `{p2_data[3]}`\n📱 Device: {p2_data[4]}\n\nПодтвердите готовность:"
        msg_p2 = f"🎯 **Соперник найден!** ({mode} | {time_slot})\n\n👑 Капитан: **{p1_data[0]}** (`ID: {p1_data[1]}`)\n🏷 Клан: `{p1_data[2]}` | Elo: `{p1_data[3]}`\n📱 Device: {p1_data[4]}\n\nПодтвердите готовность:"

        await bot.send_message(opponent_id, msg_p1, parse_mode="Markdown", reply_markup=kb)
        await message.answer(msg_p2, parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))
    else:
        queues[queue_key] = user_id
        await message.answer(f"🔍 Вы встали в очередь поиска прака **{mode}** на **{time_slot}**.\nОжидайте соперника!", reply_markup=main_keyboard(message.from_user.id))

# --- ГОТОВНОСТЬ (READY) ---
@dp.callback_query(F.data.startswith("r_"))
async def handle_ready(call: types.CallbackQuery):
    parts = call.data.split("_")
    choice = parts[1]
    match_id = parts[2]

    if match_id not in matches:
        await call.answer("Матч устарел или отменен.", show_alert=True)
        return

    m = matches[match_id]
    user_id = call.from_user.id

    if choice == "n":
        await bot.send_message(m["p1"], "❌ Прак отменен одним из капитанов.")
        await bot.send_message(m["p2"], "❌ Прак отменен одним из капитанов.")
        del matches[match_id]
        await call.answer()
        return

    m["ready"][user_id] = True
    await call.answer("Готовность принята!")

    if m["ready"][m["p1"]] and m["ready"][m["p2"]]:
        await start_veto(match_id)

# --- VETO / COINFLIP / БАН КАРТ ---
async def start_veto(match_id):
    m = matches[match_id]
    p1_data = get_player(m["p1"])
    p2_data = get_player(m["p2"])

    # Розыгрыш монетки (Coinflip) для хоста
    coin_winner_id = random.choice([m["p1"], m["p2"]])
    host_data = p1_data if coin_winner_id == m["p1"] else p2_data
    m["host_data"] = host_data

    buttons = [[InlineKeyboardButton(text=f"🚫 {mp}", callback_data=f"b_{mp}_{match_id}")] for mp in m["maps"]]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = (
        f"⚔️ **ФАЗА БАНА КАРТ (VETO) — {m['mode']}**\n\n"
        f"🪙 **Результат монетки:** Победа **{host_data[0]}**!\n"
        f"🏠 **ХОСТ ЛОББИ:** **{host_data[0]}** (ID: `{host_data[1]}`)\n"
        f"📌 *Отправьте хосту заявку/точку в игре Standoff 2!*\n\n"
        f"📊 **Регламент:** Игра до **13 раундов** (MR12)\n"
        f"Остались карты: {', '.join(m['maps'])}\n\n"
        f"👉 Первым банит: **{p1_data[0]}**"
    )
    
    await bot.send_message(m["p1"], text, parse_mode="Markdown", reply_markup=kb)
    await bot.send_message(m["p2"], text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("b_"))
async def handle_ban(call: types.CallbackQuery):
    parts = call.data.split("_")
    map_name = parts[1]
    match_id = parts[2]

    if match_id not in matches:
        return

    m = matches[match_id]
    user_id = call.from_user.id

    if user_id != m["turn"]:
        await call.answer("Сейчас очередь банить у соперника!", show_alert=True)
        return

    if map_name in m["maps"]:
        m["maps"].remove(map_name)

    if len(m["maps"]) == 1:
        final_map = m["maps"][0]
        m["final_map"] = final_map
        host_data = m["host_data"]
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏁 Ввести результат матча", callback_data=f"fin_{match_id}")]])
        
        text = (
            f"🔥 **КАРТА МАТЧА ОПРЕДЕЛЕНА!**\n\n"
            f"📍 Карта: **{final_map}**\n"
            f"⏱ Формат: **До 13 раундов (MR12)** | {m['mode']}\n\n"
            f"🏠 **Хост лобби:** **{host_data[0]}** (ID: `{host_data[1]}`)\n\n"
            f"Удачи в игре! После завершения матча нажимите кнопку ниже:"
        )
        await bot.send_message(m["p1"], text, parse_mode="Markdown", reply_markup=kb)
        await bot.send_message(m["p2"], text, parse_mode="Markdown", reply_markup=kb)
    else:
        m["turn"] = m["p2"] if m["turn"] == m["p1"] else m["p1"]
        next_player = get_player(m["turn"])[0]

        buttons = [[InlineKeyboardButton(text=f"🚫 {mp}", callback_data=f"b_{mp}_{match_id}")] for mp in m["maps"]]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        text = f"⚔️ Карта **{map_name}** забанена!\n\nОстались карты: {', '.join(m['maps'])}\n👉 Ход бана: **{next_player}**"
        await bot.send_message(m["p1"], text, parse_mode="Markdown", reply_markup=kb)
        await bot.send_message(m["p2"], text, parse_mode="Markdown", reply_markup=kb)

# --- ЗАВЕРШЕНИЕ МАТЧА И ПРОВЕРКА ОШИБОК ---
@dp.callback_query(F.data.startswith("fin_"))
async def handle_finish(call: types.CallbackQuery):
    match_id = call.data.split("_")[1]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Победа (Мы выиграли)", callback_data=f"w_win_{match_id}")],
        [InlineKeyboardButton(text="💀 Поражение (Мы проиграли)", callback_data=f"w_loss_{match_id}")]
    ])
    await call.message.answer("Выберите итоговый результат вашего матча (игра до 13 раундов):", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("w_"))
async def process_match_claim(call: types.CallbackQuery):
    parts = call.data.split("_")
    claim = parts[1]
    match_id = parts[2]

    if match_id not in matches:
        await call.answer("Матч уже подведен!", show_alert=True)
        return

    m = matches[match_id]
    user_id = call.from_user.id
    m["claims"][user_id] = claim

    await call.answer("Ваш ответ принят!")

    # Проверка, ответили ли оба
    if len(m["claims"]) == 2:
        c1 = m["claims"][m["p1"]]
        c2 = m["claims"][m["p2"]]

        # Совпадение: один заявляет WIN, другой LOSS
        if (c1 == "win" and c2 == "loss") or (c1 == "loss" and c2 == "win"):
            winner_id = m["p1"] if c1 == "win" else m["p2"]
            loser_id = m["p2"] if c1 == "win" else m["p1"]

            record_match_result(winner_id, loser_id, m.get("final_map", "Unknown"))

            w_p = get_player(winner_id)
            l_p = get_player(loser_id)

            res_text = (
                f"📊 **МАТЧ УСПЕШНО ЗАРЕГИСТРИРОВАН!**\n\n"
                f"🏆 Победитель: **{w_p[0]}** (+25 Elo) 📈\n"
                f"💀 Поражение: **{l_p[0]}** (-25 Elo) 📉\n\n"
                f"Рейтинг игроков и история обновлены!"
            )
            await bot.send_message(m["p1"], res_text, parse_mode="Markdown")
            await bot.send_message(m["p2"], res_text, parse_mode="Markdown")
            del matches[match_id]
        else:
            # Расхождение в данных
            err_text = "⚠️ **Внимание:** Введенные результаты не совпадают!\nМатч отправлен на проверку администратору. Приготовьте скриншот счета."
            await bot.send_message(m["p1"], err_text, parse_mode="Markdown")
            await bot.send_message(m["p2"], err_text, parse_mode="Markdown")
            if ADMIN_ID != 0:
                await bot.send_message(ADMIN_ID, f"🚨 **Спорный матч!**\nMatch ID: `{match_id}`\nИгрок 1: `{m['p1']}`\nИгрок 2: `{m['p2']}`")

# --- ПРОФИЛЬ И ИСТОРИЯ ---
@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message):
    p = get_player(message.from_user.id)
    if p:
        total = p[6] + p[7]
        wr = round((p[6] / total * 100), 1) if total > 0 else 0
        text = (
            f"👤 **ПРОФИЛЬ ИГРОКА**\n\n"
            f"Ник: `{p[0]}` | ID: `{p[1]}`\n"
            f"🏷 Клан: `{p[2]}`\n"
            f"📱 Device: `{p[4]}`\n"
            f"🎯 Роль: `{p[5]}`\n\n"
            f"⚡️ **Elo рейтинг:** `{p[3]}`\n"
            f"📊 **Статистика:** Побед: `{p[6]}` | Поражений: `{p[7]}`\n"
            f"🔥 **Винрейт:** `{wr}%` | **Стрик:** `{p[8]}`"
        )
        await message.answer(text, parse_mode="Markdown")
    else:
        await message.answer("⚠️ Вы еще не зарегистрированы! Нажмите /start")

@dp.message(F.text == "📜 История матчей")
async def show_history(message: types.Message):
    history = get_match_history(message.from_user.id)
    if not history:
        await message.answer("📜 Ваша история матчей пока пуста.")
        return

    text = "📜 **ПОСЛЕДНИЕ 5 МАТЧЕЙ:**\n\n"
    for opp, map_n, res, elo, dt in history:
        icon = "🟢" if res == "ПОБЕДА" else "🔴"
        text += f"{icon} **vs {opp}** | Карта: `{map_n}`\nИтог: {res} ({elo:+d} Elo) | `{dt}`\n\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🏆 Топ Игроков")
async def show_top_players(message: types.Message):
    top = get_top_players()
    text = "🏆 **ТОП-10 ИГРОКОВ PO ELO:**\n\n"
    for i, (nick, clan, elo) in enumerate(top, 1):
        text += f"**{i}. {nick}** `[{clan}]` — `{elo} Elo`\n"
    await message.answer(text, parse_mode="Markdown")

# --- АДМИН ПАНЕЛЬ ---
@dp.message(F.text == "👑 Админ Панель")
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = (
        "👑 **АДМИН-ПАНЕЛЬ УПРАВЛЕНИЯ**\n\n"
        "Доступные команды:\n"
        "🔹 `/give_elo <user_id> <elo>` — Изменить Elo игроку (напр. `/give_elo 12345678 50`)\n"
        "🔹 `/ban_player <user_id>` — Заблокировать нарушителя\n"
        "🔹 `/unban_player <user_id>` — Разблокировать\n"
        "🔹 `/broadcast` — Запустить рассылку сообщения всем"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("give_elo"))
async def cmd_give_elo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, target_id, delta = message.text.split()
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("UPDATE players SET elo = elo + ? WHERE user_id = ?", (int(delta), int(target_id)))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Игроку `{target_id}` изменено Elo на `{delta}`", parse_mode="Markdown")
    except Exception as e:
        await message.answer("⚠️ Ошибка. Пример использования: `/give_elo 123456789 50`")

@dp.message(Command("ban_player"))
async def cmd_ban_player(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target_id = int(message.text.split()[1])
        update_player_field(target_id, "is_banned", 1)
        await message.answer(f"🚫 Игрок `{target_id}` заблокирован.")
    except:
        await message.answer("Пример: `/ban_player 123456789`")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Введите текст сообщения для рассылки всем пользователям:")
    await state.set_state(AdminBroadcast.waiting_for_text)

@dp.message(AdminBroadcast.waiting_for_text)
async def process_broadcast(message: types.Message, state: FSMContext):
    text = message.text
    users = get_all_users()
    count = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 **ОПОВЕЩЕНИЕ ОТ АДМИНИСТРАЦИИ:**\n\n{text}", parse_mode="Markdown")
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await state.clear()
    await message.answer(f"✅ Рассылка завершена! Получили сообщений: {count}")

# --- ВЕБ-СЕРВЕР ---
async def handle(request):
    return web.Response(text="Standoff 2 Bot is Active!")

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
