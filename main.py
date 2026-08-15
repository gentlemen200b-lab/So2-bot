import asyncio
import io
import logging
import os
import random
import sqlite3
import string
from datetime import datetime
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

ADMIN_ID = 8088201524
BOT_TOKEN = os.getenv("BOT_TOKEN")

MAPS_LIST = ["Sandstone", "Province", "Rust", "Dune", "Hanami", "Prison", "Breeze"]

queues = {"fast": []}
time_matches = {}  
matches = {}

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
        CREATE TABLE IF NOT EXISTS clan_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_id INTEGER,
            user_id INTEGER
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
            try:
                img = Image.open(path).convert('RGB')
                return img.resize((200, 200))
            except Exception:
                pass
        return Image.new('RGB', (200, 200), color=(40, 40, 50))

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
    waiting_for_date = State()
    waiting_for_time = State()

class ClanJoinState(StatesGroup):
    waiting_for_tag = State()

class AvatarState(StatesGroup):
    waiting_for_photo = State()

class MatchResultState(StatesGroup):
    waiting_for_screenshot = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_ban_id = State()
    waiting_for_elo_data = State()

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
        "Добро пожаловать на профессиональную арену праков! Выбирай фаст-прак для игры прямо сейчас или ставь бронь по времени.",
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

# --- МЕХАНИКА КЛАНОВ ---
@dp.message(F.text == "🛡 Мой Клан")
async def show_my_clan(message: types.Message):
    p = get_player(message.from_user.id)
    if not p or p[2] == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Найти клан / Вступить", callback_data="find_clan_menu")]
        ])
        await message.answer("⚠️ Вы не состоите ни в одном клане. Вы можете создать свой или подать заявку в существующий.", reply_markup=kb)
        return
    
    c = get_clan(p[2])
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, nickname, game_id, role FROM players WHERE clan_id = ?", (c[0],))
    members = cur.fetchall()
    conn.close()

    members_str = "\n".join([f"• `{m[1]}` (ID: `{m[2]}`) — _{m[3]}_" for m in members])

    text = (
        f"🛡 **ИНФОРМАЦИЯ О КЛАНЕ**\n\n"
        f"🏷 Название: **{c[2]}** `[{c[1]}]`\n"
        f"📊 Elo Рейтинг: `{c[4]}`\n"
        f"🔥 Винстрик: `{c[7]}` матчей подряд\n"
        f"🏆 Побед: `{c[5]}` | ❌ Поражений: `{c[6]}`\n\n"
        f"👥 **Состав ростера ({len(members)} игроков):**\n{members_str}"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⚙️ Управление Кланом")
async def clan_management(message: types.Message, state: FSMContext):
    p = get_player(message.from_user.id)
    if not p:
        await message.answer("⚠️ Сначала зарегистрируйтесь!")
        return

    clan = get_clan_by_leader(message.from_user.id)
    if not clan:
        if p[2] != 0:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚪 Покинуть клан", callback_data="leave_clan")]
            ])
            await message.answer("⚙️ Вы участник клана. Вы можете покинуть его:", reply_markup=kb)
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛠 Создать новый клан", callback_data="create_clan_start")],
                [InlineKeyboardButton(text="🔍 Вступить в клан", callback_data="find_clan_menu")]
            ])
            await message.answer("⚙️ Вы не состоите в клане.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Участники ростера", callback_data="clan_roster")],
            [InlineKeyboardButton(text="📩 Заявки в клан", callback_data="clan_applications")],
            [InlineKeyboardButton(text="🖼 Изменить аватарку", callback_data="clan_avatar")],
            [InlineKeyboardButton(text="❌ Распустить клан", callback_data="clan_disband")]
        ])
        await message.answer(f"⚙️ **Панель лидера клана [{clan[1]}]**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "create_clan_start")
async def create_clan_callback(call: types.CallbackQuery, state: FSMContext):
    p = get_player(call.from_user.id)
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

# --- ПОИСК И ВСТУПЛЕНИЕ В КЛАН ---
@dp.callback_query(F.data == "find_clan_menu")
async def find_clan_menu_cb(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("🔍 Введите **тег клана**, в который хотите вступить:", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(ClanJoinState.waiting_for_tag)
    await call.answer()

@dp.message(ClanJoinState.waiting_for_tag)
async def process_join_clan(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    tag = message.text.strip().upper()
    
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT clan_id, clan_name, leader_id FROM clans WHERE clan_tag = ?", (tag,))
    clan = cur.fetchone()
    
    if not clan:
        conn.close()
        await message.answer("⚠️ Клан с таким тегом не найден. Попробуйте еще раз:")
        return

    clan_id, clan_name, leader_id = clan
    
    cur.execute("SELECT 1 FROM clan_applications WHERE clan_id = ? AND user_id = ?", (clan_id, message.from_user.id))
    if cur.fetchone():
        conn.close()
        await state.clear()
        await message.answer("⚠️ Вы уже подали заявку в этот клан. Ожидайте ответа капитана.", reply_markup=main_keyboard(message.from_user.id))
        return

    cur.execute("INSERT INTO clan_applications (clan_id, user_id) VALUES (?, ?)", (clan_id, message.from_user.id))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(f"✅ Заявка на вступление в клан **{clan_name}** `[{tag}]` успешно отправлена капитану!", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Посмотреть заявки", callback_data="clan_applications")]
    ])
    try:
        await bot.send_message(leader_id, f"🔔 Новый игрок (`{message.from_user.full_name}`) хочет вступить в ваш клан!", reply_markup=kb)
    except Exception:
        pass

@dp.callback_query(F.data == "clan_roster")
async def clan_roster_cb(call: types.CallbackQuery):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Вы не лидер клана!", show_alert=True)
        return

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, nickname, game_id, role FROM players WHERE clan_id = ?", (clan[0],))
    members = cur.fetchall()
    conn.close()

    kb_list = []
    for m in members:
        if m[0] != call.from_user.id:  
            kb_list.append([InlineKeyboardButton(text=f"❌ Исключить {m[1]}", callback_data=f"kick_{m[0]}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list) if kb_list else None
    members_str = "\n".join([f"• `{m[1]}` (ID: `{m[2]}`) — _{m[3]}_" for m in members])

    await call.message.edit_text(
        f"👥 **Управление ростером клана [{clan[1]}]**\n\n{members_str}",
        reply_markup=kb, parse_mode="Markdown"
    )
    await call.answer()

@dp.callback_query(F.data.startswith("kick_"))
async def kick_member(call: types.CallbackQuery):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Вы не лидер!", show_alert=True)
        return
    
    target_id = int(call.data.split("_")[1])
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("UPDATE players SET clan_id = 0 WHERE user_id = ? AND clan_id = ?", (target_id, clan[0]))
    conn.commit()
    conn.close()

    await call.answer("Игрок исключен из клана!")
    await clan_roster_cb(call)

@dp.callback_query(F.data == "clan_applications")
async def clan_apps_cb(call: types.CallbackQuery):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Вы не лидер!", show_alert=True)
        return

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT id, user_id FROM clan_applications WHERE clan_id = ?", (clan[0],))
    apps = cur.fetchall()
    conn.close()

    if not apps:
        await call.message.edit_text("📩 Активных заявок в клан нет.")
        await call.answer()
        return

    kb_list = []
    text = "📩 **Заявки на вступление:**\n\n"
    for app_id, u_id in apps:
        p = get_player(u_id)
        if p:
            text += f"• `{p[0]}` (ID: `{p[1]}`)\n"
            kb_list.append([
                InlineKeyboardButton(text=f"✅ Принять {p[0]}", callback_data=f"app_accept_{app_id}_{u_id}"),
                InlineKeyboardButton(text=f"❌ Отклонить", callback_data=f"app_deny_{app_id}")
            ])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await call.answer()

@dp.callback_query(F.data.startswith("app_accept_"))
async def accept_application(call: types.CallbackQuery):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Ошибка доступа", show_alert=True)
        return

    _, _, app_id, user_id = call.data.split("_")
    app_id, user_id = int(app_id), int(user_id)

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("UPDATE players SET clan_id = ? WHERE user_id = ?", (clan[0], user_id))
    cur.execute("DELETE FROM clan_applications WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()

    await call.answer("Игрок принят в клан!")
    try:
        await bot.send_message(user_id, f"🎉 Ваша заявка в клан `[{clan[1]}]` одобрена! Добро пожаловать.")
    except Exception:
        pass
    await clan_apps_cb(call)

@dp.callback_query(F.data.startswith("app_deny_"))
async def deny_application(call: types.CallbackQuery):
    app_id = int(call.data.split("_")[2])
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM clan_applications WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()

    await call.answer("Заявка отклонена.")
    await clan_apps_cb(call)

@dp.callback_query(F.data == "clan_avatar")
async def clan_avatar_cb(call: types.CallbackQuery, state: FSMContext):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Вы не лидер клана!", show_alert=True)
        return

    await call.message.answer("🖼 Отправьте новую **картинку (фото)** для аватара вашего клана:", reply_markup=cancel_keyboard())
    await state.set_state(AvatarState.waiting_for_photo)
    await call.answer()

@dp.message(AvatarState.waiting_for_photo, F.photo)
async def process_clan_avatar(message: types.Message, state: FSMContext):
    clan = get_clan_by_leader(message.from_user.id)
    if not clan:
        await state.clear()
        return

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    
    os.makedirs("avatars", exist_ok=True)
    avatar_path = f"avatars/clan_{clan[0]}.jpg"
    await bot.download_file(file_info.file_path, avatar_path)

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("UPDATE clans SET avatar_path = ? WHERE clan_id = ?", (avatar_path, clan[0]))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("✅ Аватарка клана успешно обновлена!", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "clan_disband")
async def clan_disband_cb(call: types.CallbackQuery):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Вы не лидер!", show_alert=True)
        return

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("UPDATE players SET clan_id = 0 WHERE clan_id = ?", (clan[0],))
    cur.execute("DELETE FROM clans WHERE clan_id = ?", (clan[0],))
    conn.commit()
    conn.close()

    await call.message.edit_text("❌ Клан был полностью распущен.", reply_markup=None)
    await call.answer()

@dp.callback_query(F.data == "leave_clan")
async def leave_clan_cb(call: types.CallbackQuery):
    p = get_player(call.from_user.id)
    if not p or p[2] == 0:
        await call.answer("⚠️ Вы не в клане", show_alert=True)
        return

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("UPDATE players SET clan_id = 0 WHERE user_id = ?", (call.from_user.id,))
    conn.commit()
    conn.close()

    await call.message.edit_text("🚪 Вы успешно покинули клан.")
    await call.answer()

# --- ПОИСК ПРАКА И VETO ---
@dp.message(F.text == "⚔️ Найти Прак (Кланы)")
async def search_clan_practice(message: types.Message):
    clan = get_clan_by_leader(message.from_user.id)
    if not clan:
        await message.answer("⚠️ Искать прак от имени клана может только Капитан!")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Фаст-прак (Прямо сейчас)", callback_data="search_fast")],
        [InlineKeyboardButton(text="⏰ Прак по времени", callback_data="search_time")]
    ])
    await message.answer("🎮 Выберите режим поиска прака:", reply_markup=kb)

@dp.callback_query(F.data == "search_fast")
async def fast_pracc_handler(call: types.CallbackQuery):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Вы не капитан клана!", show_alert=True)
        return
    
    clan_id = clan[0]
    if queues["fast"] and queues["fast"][0] != clan_id:
        opponent_id = queues["fast"].pop(0)
        await call.answer("Соперник найден!")
        await start_match_logic(opponent_id, clan_id, "Фаст-прак")
    elif queues["fast"] and queues["fast"][0] == clan_id:
        await call.answer("Вы уже в очереди!", show_alert=True)
    else:
        queues["fast"].append(clan_id)
        await call.message.edit_text(f"⚡ Клан **[{clan[1]}]** добавлен в очередь **Фаст-прак**. Ожидаем соперника...", parse_mode="Markdown")

@dp.callback_query(F.data == "search_time")
async def time_pracc_menu(call: types.CallbackQuery, state: FSMContext):
    clan = get_clan_by_leader(call.from_user.id)
    if not clan:
        await call.answer("⚠️ Вы не капитан клана!", show_alert=True)
        return
    await call.message.answer("📅 Введите дату прака (например: `17 августа`):", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(PracticeSearch.waiting_for_date)
    await call.answer()

@dp.message(PracticeSearch.waiting_for_date)
async def process_practice_date(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    date_str = message.text.strip()
    await state.update_data(practice_date=date_str)
    await message.answer("⏰ Введите время прака в формате `ЧЧ:ММ` (например: `20:00`):", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(PracticeSearch.waiting_for_time)

@dp.message(PracticeSearch.waiting_for_time)
async def process_practice_time(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    time_str = message.text.strip()
    data = await state.get_data()
    date_str = data.get("practice_date")
    await state.clear()

    clan = get_clan_by_leader(message.from_user.id)
    clan_id = clan[0]

    matched_time_key = None
    for tm_id, tm_data in time_matches.items():
        if tm_data["date"] == date_str and tm_data["time"] == time_str and tm_data["c2_id"] is None:
            if tm_data["c1_id"] != clan_id:
                matched_time_key = tm_id
                break

    if matched_time_key:
        tm = time_matches[matched_time_key]
        tm["c2_id"] = clan_id
        tm["c2_leader"] = message.from_user.id

        c1 = get_clan(tm["c1_id"])
        c2 = get_clan(tm["c2_id"])

        text = (
            f"⏰ **НАЙДЕН СОПЕРНИК НА ПРАК!**\n\n"
            f"📅 Дата: `{date_str}` | Время: `{time_str}`\n"
            f"🛡 **{c1[2]}** `[{c1[1]}]` vs **{c2[2]}** `[{c2[1]}]`\n\n"
            f"Пойдете ли вы играть этот матч?"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Готов", callback_data=f"tm_ready_{matched_time_key}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"tm_cancel_{matched_time_key}")
            ]
        ])
        try:
            await bot.send_message(c1[3], text, reply_markup=kb, parse_mode="Markdown")
            await bot.send_message(c2[3], text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass
        await message.answer(f"✅ Соперник найден на `{date_str} в {time_str}`! Обоим капитанам отправлен запрос на подтверждение.", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))
    else:
        tm_id = generate_short_id()
        time_matches[tm_id] = {
            "date": date_str,
            "time": time_str,
            "c1_id": clan_id,
            "c1_leader": message.from_user.id,
            "c2_id": None,
            "c2_leader": None,
            "c1_confirmed": False,
            "c2_confirmed": False
        }
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить заявку", callback_data=f"tm_delete_{tm_id}")]
        ])
        await message.answer(
            f"⏰ Клан **[{clan[1]}]** зарегистрирован на прак:\n📅 Дата: `{date_str}`\n⏰ Время: `{time_str}`\n\n"
            f"Вы можете продолжать искать другие праки, пока бот ищет вам соперника на этот слот.",
            parse_mode="Markdown", reply_markup=kb
        )
        await message.answer("Главное меню:", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data.startswith("tm_delete_"))
async def cancel_time_pracc(call: types.CallbackQuery):
    tm_id = call.data.split("_")[2]
    if tm_id in time_matches:
        del time_matches[tm_id]
        await call.message.edit_text("❌ Заявка на прак по времени отменена.")
    else:
        await call.answer("Заявка уже неактуальна.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("tm_ready_"))
async def time_pracc_ready(call: types.CallbackQuery):
    tm_id = call.data.split("_")[2]
    tm = time_matches.get(tm_id)
    if not tm:
        await call.answer("Матч не найден.", show_alert=True)
        return

    user_id = call.from_user.id
    if user_id == tm["c1_leader"]:
        tm["c1_confirmed"] = True
    elif user_id == tm["c2_leader"]:
        tm["c2_confirmed"] = True

    await call.answer("Статус «Готов» принят!")

    if tm["c1_confirmed"] and tm["c2_confirmed"]:
        c1 = get_clan(tm["c1_id"])
        c2 = get_clan(tm["c2_id"])
        
        success_text = (
            f"✅ **Оба капитана подтвердили участие!**\n"
            f"📅 Дата: `{tm['date']}` | Время: `{tm['time']}`\n"
            f"Матч зафиксирован. Когда наступит назначенное время, бот пришлет кнопку «Начать матч»!"
        )
        try:
            await bot.send_message(c1[3], success_text, parse_mode="Markdown")
            await bot.send_message(c2[3], success_text, parse_mode="Markdown")
        except Exception:
            pass
    else:
        await call.message.edit_text("✅ Вы подтвердили готовность. Ожидаем подтверждения от соперника...")

@dp.callback_query(F.data.startswith("tm_cancel_"))
async def time_pracc_decline(call: types.CallbackQuery):
    tm_id = call.data.split("_")[2]
    tm = time_matches.get(tm_id)
    if tm:
        c1 = get_clan(tm["c1_id"])
        c2 = get_clan(tm["c2_id"])
        try:
            await bot.send_message(c1[3], "❌ Соперник отменил участие в праке по времени.")
            await bot.send_message(c2[3], "❌ Соперник отменил участие в праке по времени.")
        except Exception:
            pass
        del time_matches[tm_id]
    await call.message.edit_text("❌ Матч отменен.")
    await call.answer()

async def pracc_scheduler():
    while True:
        now = datetime.now()
        current_time_minutes = now.hour * 60 + now.minute

        for tm_id, tm in list(time_matches.items()):
            if tm.get("c1_confirmed") and tm.get("c2_confirmed") and not tm.get("started", False):
                try:
                    parts = tm["time"].split(":")
                    pracc_minutes = int(parts[0]) * 60 + int(parts[1])
                    
                    if current_time_minutes >= pracc_minutes:
                        tm["started"] = True
                        
                        c1 = get_clan(tm["c1_id"])
                        c2 = get_clan(tm["c2_id"])
                        
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⚔️ Начать матч", callback_data=f"start_timed_match_{tm_id}")]
                        ])
                        try:
                            await bot.send_message(c1[3], f"⏰ Время прака (`{tm['time']}`) наступило! Нажмите кнопку для старта матча:", reply_markup=kb, parse_mode="Markdown")
                            await bot.send_message(c2[3], f"⏰ Время прака (`{tm['time']}`) наступило! Нажмите кнопку для старта матча:", reply_markup=kb, parse_mode="Markdown")
                        except Exception:
                            pass
                except Exception:
                    pass
        await asyncio.sleep(10)

@dp.callback_query(F.data.startswith("start_timed_match_"))
async def start_timed_match_cb(call: types.CallbackQuery):
    tm_id = call.data.split("_")[3]
    tm = time_matches.get(tm_id)
    if not tm:
        await call.answer("Матч не найден или уже запущен.", show_alert=True)
        return

    await start_match_logic(tm["c1_id"], tm["c2_id"], f"Прак на {tm['time']}")
    time_matches.pop(tm_id, None)
    await call.answer()

async def start_match_logic(c1_id, c2_id, mode):
    c1 = get_clan(c1_id)
    c2 = get_clan(c2_id)
    
    if is_blocked(c1_id, c2_id) or is_blocked(c2_id, c1_id):
        return

    match_id = generate_short_id()
    
    p1_data = get_player(c1[3])
    p2_data = get_player(c2[3])
    p1_game_id = p1_data[1] if p1_data else "Не указан"
    p2_game_id = p2_data[1] if p2_data else "Не указан"

    host_clan = random.choice([c1, c2])

    matches[match_id] = {
        "c1_id": c1[0], "c2_id": c2[0],
        "c1_tag": c1[1], "c2_tag": c2[1],
        "p1_leader": c1[3], "p2_leader": c2[3],
        "mode": mode, "maps": MAPS_LIST.copy(), "turn": c1[3],
        "host_tag": host_clan[1],
        "host_name": host_clan[2],
        "host_game_id": p2_data[1] if host_clan[0] == c2[0] else p1_game_id
    }

    poster_bio = create_vs_poster(c1[8], c2[8], c1[1], c2[1])

    caption = (
        f"🔥 **КЛАНОВЫЙ ПРАК НАЙДЕН! [{mode}]** 🔥\n\n"
        f"🛡 **{c1[2]}** `[{c1[1]}]` (Elo: {c1[4]})\n"
        f"👤 Капитан: `{c1[2]}` | ID: `🔒 {p1_game_id}`\n"
        f"⚔️ **VS** ⚔️\n"
        f"🛡 **{c2[2]}** `[{c2[1]}]` (Elo: {c2[4]})\n"
        f"👤 Капитан: `{c2[2]}` | ID: `🔒 {p2_game_id}`\n\n"
        f"🏰 **Хост лобби (создает комнату):** `[{host_clan[1]}] {host_clan[2]}`\n\n"
        f"🎯 Начинаем фазу банов карт (Veto)!"
    )

    try:
        await bot.send_photo(c1[3], photo=types.BufferedInputFile(poster_bio.getvalue(), filename="vs.png"), caption=caption, parse_mode="Markdown")
        await bot.send_photo(c2[3], photo=types.BufferedInputFile(poster_bio.getvalue(), filename="vs.png"), caption=caption, parse_mode="Markdown")
    except Exception:
        pass
    
    await send_veto_turn(match_id)

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
            f"🗺 Игровая карта: **{final_map}**\n"
            f"🏰 Создает лобби (Хостер): **[{m['host_tag']}] {m['host_name']}**\n"
            f"🆔 **Игровой ID хостера:** `{m['host_game_id']}`\n\n"
            f"Заходите в лобби Standoff 2. После игры нажмите кнопку ниже и пришлите скриншот результатов!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📸 Отправить скриншот", callback_data=f"upscreen_{match_id}")],
            [InlineKeyboardButton(text="⚠️ Пожаловаться", callback_data=f"rep_{match_id}")]
        ])
        try:
            await bot.send_message(m["p1_leader"], res_text, reply_markup=kb, parse_mode="Markdown")
            await bot.send_message(m["p2_leader"], res_text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass
        return

    kb_list = []
    for mp in m["maps"]:
        kb_list.append([InlineKeyboardButton(text=f"❌ Банить карту {mp}", callback_data=f"ban_{match_id}_{mp}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)

    try:
        await bot.send_message(turn_user, f"🎯 **Ваша очередь банить карту!**\nОстались: `{', '.join(m['maps'])}`", reply_markup=kb, parse_mode="Markdown")
        await bot.send_message(other_user, f"⏳ Соперник выбирает карту для бана...\nОстались: `{', '.join(m['maps'])}`", parse_mode="Markdown")
    except Exception:
        pass

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

# --- ОТПРАВКА СКРИНШОТА НАПРЯМУЮ АДМИНУ ---
@dp.callback_query(F.data.startswith("upscreen_"))
async def trigger_screen_upload(call: types.CallbackQuery, state: FSMContext):
    match_id = call.data.split("_")[1]
    m = matches.get(match_id)
    if not m:
        await call.answer("⚠️ Матч не найден или уже завершен.", show_alert=True)
        return

    await state.update_data(active_match_id=match_id)
    await call.message.answer("📸 Пришлите **скриншот таблицы результатов** из Standoff 2 ответным сообщением:", reply_markup=cancel_keyboard())
    await state.set_state(MatchResultState.waiting_for_screenshot)
    await call.answer()

@dp.message(MatchResultState.waiting_for_screenshot, F.photo)
async def receive_match_screenshot(message: types.Message, state: FSMContext):
    data = await state.get_data()
    match_id = data.get("active_match_id")
    await state.clear()

    m = matches.get(match_id)
    if not m:
        await message.answer("⚠️ Ошибка: сессия матча не найдена.", reply_markup=main_keyboard(message.from_user.id))
        return

    c1 = get_clan(m["c1_id"])
    c2 = get_clan(m["c2_id"])
    photo = message.photo[-1]

    caption = (
        f"📸 **НОВЫЙ СКРИНШОТ ИГРОВОГО МАТЧА**\n\n"
        f"🛡 **{c1[2]}** `[{c1[1]}]` vs **{c2[2]}** `[{c2[1]}]`\n"
        f"🗺 Карта: `{m.get('final_map', 'Не указана')}`\n"
        f"👤 Отправил капитан: `{message.from_user.full_name}` (ID: `{message.from_user.id}`)"
    )

    try:
        # Пересылаем скриншот напрямую тебе (админу)
        await bot.send_photo(ADMIN_ID, photo=photo.file_id, caption=caption, parse_mode="Markdown")
        await message.answer("✅ Скриншот успешно отправлен администратору!", reply_markup=main_keyboard(message.from_user.id))
    except Exception as e:
        logging.error(f"Не удалось отправить скриншот админу: {e}")
        await message.answer("⚠️ Ошибка отправки администратору.", reply_markup=main_keyboard(message.from_user.id))

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

# --- АДМИН ПАНЕЛЬ ---
@dp.message(F.text == "👑 Админ Панель")
async def admin_panel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⚠️ У вас нет доступа к этой панели.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔨 Забанить игрока", callback_data="admin_ban")],
        [InlineKeyboardButton(text="⚡ Изменить Elo клана", callback_data="admin_elo")]
    ])
    await message.answer("👑 **Панель Администратора**\nВыберите нужное действие:", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM players")
    players_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM clans")
    clans_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM match_history")
    matches_count = cur.fetchone()[0]
    conn.close()

    text = (
        f"📊 **Статистика бота:**\n\n"
        f"👥 Зарегистрированных игроков: `{players_count}`\n"
        f"🛡 Созданных кланов: `{clans_count}`\n"
        f"⚔️ Всего сыгранных матчей: `{matches_count}`"
    )
    await call.message.edit_text(text, parse_mode="Markdown")
    await call.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_callback(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return

    await call.message.answer("📢 Введите текст для рассылки всем пользователям бота:", reply_markup=cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_broadcast)
    await call.answer()

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    text = message.text
    await state.clear()

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM players")
    users = cur.fetchall()
    conn.close()

    success = 0
    blocked = 0

    status_msg = await message.answer("📢 Рассылка началась...")

    for (u_id,) in users:
        try:
            await bot.send_message(u_id, f"📢 **Объявление от администрации:**\n\n{text}", parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            blocked += 1

    await status_msg.edit_text(f"✅ **Рассылка завершена!**\n\n📬 Успешно доставлено: `{success}`\n🚫 Заблокировали бота: `{blocked}`", parse_mode="Markdown")
    await message.answer("Главное меню:", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "admin_ban")
async def admin_ban_callback(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return

    await call.message.answer("🔨 Введите `user_id` игрока, которого хотите забанить/разбанить:", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_ban_id)
    await call.answer()

@dp.message(AdminStates.waiting_for_ban_id)
async def process_ban_player(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    if not message.text.isdigit():
        await message.answer("⚠️ ID должен состоять только из цифр!")
        return

    target_id = int(message.text)
    await state.clear()

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT is_banned, nickname FROM players WHERE user_id = ?", (target_id,))
    res = cur.fetchone()

    if not res:
        conn.close()
        await message.answer("⚠️ Игрок с таким ID не найден в базе.")
        return

    is_banned, nick = res
    new_status = 0 if is_banned == 1 else 1
    
    cur.execute("UPDATE players SET is_banned = ? WHERE user_id = ?", (new_status, target_id))
    conn.commit()
    conn.close()

    status_text = "разбанен ✅" if new_status == 0 else "забанен 🔨"
    await message.answer(f"👤 Игрок `{nick}` (ID: `{target_id}`) успешно {status_text}.", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "admin_elo")
async def admin_elo_callback(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return

    await call.message.answer("⚡ Введите данные в формате: `ID_клана +/-Количество_Elo`\n(Пример: `1 +150` или `2 -50`)", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_elo_data)
    await call.answer()

@dp.message(AdminStates.waiting_for_elo_data)
async def process_elo_change(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("⚠️ Неверный формат! Пример: `1 +100`")
        return

    clan_id = int(parts[0])
    try:
        elo_change = int(parts[1])
    except ValueError:
        await message.answer("⚠️ Значение Elo должно быть числом (например, `+50` или `-50`)!")
        return

    await state.clear()

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT elo, clan_name, clan_tag FROM clans WHERE clan_id = ?", (clan_id,))
    clan = cur.fetchone()

    if not clan:
        conn.close()
        await message.answer("⚠️ Клан с таким ID не найден.")
        return

    current_elo, name, tag = clan
    new_elo = max(0, current_elo + elo_change)

    cur.execute("UPDATE clans SET elo = ? WHERE clan_id = ?", (new_elo, clan_id))
    conn.commit()
    conn.close()

    await message.answer(f"✅ Баланс клана **{name}** `[{tag}]` изменен!\nСтарый Elo: `{current_elo}` ➡️ Новый Elo: `{new_elo}`", parse_mode="Markdown", reply_markup=main_keyboard(message.from_user.id))

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
    
    asyncio.create_task(pracc_scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
