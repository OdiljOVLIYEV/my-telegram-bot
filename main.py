import asyncio
import logging
import sys
import os
import json
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import aiohttp
from aiohttp import web

# --- SOZLAMALAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL", "").strip() # Bo'sh joylarni tozalash
admin_ids_str = os.getenv("ADMIN_ID", "")
ADMIN_IDS = [int(i.strip()) for i in admin_ids_str.split(",") if i.strip().isdigit()]
BOT_USERNAME = os.getenv("BOT_USERNAME", "uz_filtr_fayl_bot")
PORT = int(os.getenv("PORT", 8080))
UZGAMECORE_API_URL = os.getenv("UZGAMECORE_API_URL", "https://uzgamecore.uz").rstrip("/")
DOWNLOAD_TICKET_BOT_SECRET = os.getenv("DOWNLOAD_TICKET_BOT_SECRET", "").strip()
TELEGRAM_BOT_VERIFY_SECRET = os.getenv("TELEGRAM_BOT_VERIFY_SECRET", DOWNLOAD_TICKET_BOT_SECRET).strip()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# MongoDB Baza boshqaruvi
class MongoDatabase:
    def __init__(self, url):
        try:
            self.client = AsyncIOMotorClient(url)
            self.db = self.client['tg_bot_db']
            self.collection = self.db['games']
        except Exception as e:
            logging.error(f"MongoDB Client yaratishda xato: {e}")
            raise e

    async def find_one(self, query):
        return await self.collection.find_one(query)

    async def find_all(self):
        cursor = self.collection.find({})
        return await cursor.to_list(length=None)

    async def update_one(self, filter_query, update_data, upsert=False):
        if upsert:
            # Yangi ID yaratish (agar yangi o'yin qo'shilayotgan bo'lsa)
            existing = await self.collection.find_one(filter_query)
            if not existing:
                last_game = await self.collection.find_one(sort=[("id", -1)])
                new_id = (last_game["id"] + 1) if last_game else 1
                update_data["id"] = new_id
        
        await self.collection.update_one(filter_query, {"$set": update_data}, upsert=upsert)
        return True

    async def delete_one(self, query):
        await self.collection.delete_one(query)
        return True

    async def delete_many(self, query):
        await self.collection.delete_many(query)
        return True

if not MONGO_URL:
    logging.error("MONGO_URL o'rnatilmagan! Iltimos .env faylini tekshiring.")
    sys.exit(1)

db = MongoDatabase(MONGO_URL)

bot = Bot(token=TOKEN) if TOKEN else None
dp = Dispatcher()

# --- HEALTH CHECK ---
async def handle_health(request):
    try:
        await db.client.admin.command('ping')
        return web.Response(text="Bot is running and MongoDB is connected!", status=200)
    except Exception as e:
        return web.Response(text=f"MongoDB Error: {e}", status=500)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Veb-server {PORT}-portda ishga tushdi.")

class AdminStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_files = State()
    waiting_for_addfile_game = State()
    waiting_for_addfile_files = State()
    waiting_for_removefile_game = State()
    waiting_for_removefile_index = State()
    waiting_for_renamefile_game = State()
    waiting_for_renamefile_index = State()
    waiting_for_renamefile_name = State()
    waiting_for_delete = State()

async def get_main_menu():
    games = await db.find_all()
    buttons = []
    if games:
        # Har bir qatorda 2 tadan o'yin nomi
        row = []
        for i, game in enumerate(games):
            row.append(KeyboardButton(text=game['name']))
            if (i + 1) % 2 == 0:
                buttons.append(row)
                row = []
        if row: buttons.append(row)
    
    buttons.append([KeyboardButton(text="🔗 Barcha linklar")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

async def get_user_menu():
    # Oddiy foydalanuvchilar uchun maxsus tugmalar kerak bo'lmasa, bo'sh qaytaramiz
    return ReplyKeyboardRemove()

async def redeem_download_ticket(ticket: str):
    if not DOWNLOAD_TICKET_BOT_SECRET:
        logging.error("DOWNLOAD_TICKET_BOT_SECRET o'rnatilmagan.")
        return None

    timeout = aiohttp.ClientTimeout(total=10)
    headers = {
        "Authorization": f"Bearer {DOWNLOAD_TICKET_BOT_SECRET}",
        "Content-Type": "application/json"
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{UZGAMECORE_API_URL}/api/download-tickets/redeem",
                headers=headers,
                json={"ticket": ticket}
            ) as response:
                if response.status != 200:
                    logging.warning("Download ticket rad etildi: status=%s", response.status)
                    return None
                payload = await response.json()
                game_key = payload.get("gameKey")
                return game_key.strip().lower() if isinstance(game_key, str) else None
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        logging.error("UZGameCore ticket API bilan aloqa xatosi: %s", error)
        return None

def is_download_ticket_payload(payload: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{32}", (payload or "").strip()))

def is_registration_ticket_payload(payload: str) -> bool:
    return bool(re.fullmatch(r"reg_[A-Za-z0-9_-]{20,}", (payload or "").strip()))

def is_pre_registration_ticket_payload(payload: str) -> bool:
    return bool(re.fullmatch(r"tgs_[A-Za-z0-9_-]{20,}", (payload or "").strip()))

async def verify_register_session(token: str, message: Message):
    if not TELEGRAM_BOT_VERIFY_SECRET:
        logging.error("TELEGRAM_BOT_VERIFY_SECRET o'rnatilmagan.")
        return None

    timeout = aiohttp.ClientTimeout(total=10)
    headers = {
        "Authorization": f"Bearer {TELEGRAM_BOT_VERIFY_SECRET}",
        "Content-Type": "application/json"
    }
    payload = {
        "token": token,
        "telegram_chat_id": str(message.chat.id),
        "telegram_user_id": str(message.from_user.id),
        "telegram_username": message.from_user.username or ""
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{UZGAMECORE_API_URL}/api/auth/telegram/verify-register-session",
                headers=headers,
                json=payload
            ) as response:
                data = await response.json(content_type=None)
                if response.status != 200:
                    logging.warning("Telegram pre-registration verify failed: status=%s payload=%s", response.status, data)
                    return {"success": False, "payload": data, "status": response.status}
                return {"success": True, "payload": data, "status": response.status}
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        logging.error("Telegram pre-registration API bilan aloqa xatosi: %s", error)
        return None

async def complete_telegram_registration(token: str, message: Message):
    if not TELEGRAM_BOT_VERIFY_SECRET:
        logging.error("TELEGRAM_BOT_VERIFY_SECRET o'rnatilmagan.")
        return None

    timeout = aiohttp.ClientTimeout(total=10)
    headers = {
        "Authorization": f"Bearer {TELEGRAM_BOT_VERIFY_SECRET}",
        "Content-Type": "application/json"
    }
    payload = {
        "token": token,
        "telegram_chat_id": str(message.chat.id),
        "telegram_user_id": str(message.from_user.id),
        "telegram_username": message.from_user.username or ""
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{UZGAMECORE_API_URL}/api/auth/telegram/complete-registration",
                headers=headers,
                json=payload
            ) as response:
                data = await response.json(content_type=None)
                if response.status != 200:
                    logging.warning("Telegram registration verify failed: status=%s payload=%s", response.status, data)
                    return {"success": False, "payload": data, "status": response.status}
                return {"success": True, "payload": data, "status": response.status}
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        logging.error("Telegram registration API bilan aloqa xatosi: %s", error)
        return None

def extract_file_entry(message: Message):
    if message.document:
        return {
            "file_id": message.document.file_id,
            "file_name": message.document.file_name or "document",
            "kind": "document"
        }
    if message.video:
        return {
            "file_id": message.video.file_id,
            "file_name": message.video.file_name or f"video_{message.video.file_unique_id}.mp4",
            "kind": "video"
        }
    if message.audio:
        title = message.audio.title or message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"
        return {
            "file_id": message.audio.file_id,
            "file_name": title,
            "kind": "audio"
        }
    return None

def normalize_file_entries(files):
    normalized = []
    for index, item in enumerate(files or []):
        if isinstance(item, dict):
            file_id = item.get("file_id")
            if not file_id:
                continue
            normalized.append({
                "file_id": file_id,
                "file_name": item.get("file_name") or f"file_{index + 1}",
                "kind": item.get("kind") or "document"
            })
        elif isinstance(item, str):
            normalized.append({
                "file_id": item,
                "file_name": f"file_{index + 1}",
                "kind": "document"
            })
    return normalized

async def send_stored_file(chat_id: int, file_entry):
    normalized = normalize_file_entries([file_entry])
    if not normalized:
        return
    item = normalized[0]
    if item["kind"] == "video":
        await bot.send_video(chat_id=chat_id, video=item["file_id"])
    elif item["kind"] == "audio":
        await bot.send_audio(chat_id=chat_id, audio=item["file_id"])
    else:
        await bot.send_document(chat_id=chat_id, document=item["file_id"])

# --- START BUYRUQ ---
@dp.message(CommandStart(), StateFilter("*"))
async def command_start_handler(message: Message, state: FSMContext):
    logging.info(f"User {message.from_user.id} /start bosdi.")
    await state.clear()
    args = message.text.split()
    
    if len(args) > 1:
        start_payload = args[1].strip()
        if is_pre_registration_ticket_payload(start_payload):
            verification = await verify_register_session(start_payload, message)
            if verification and verification.get("success"):
                await message.answer(
                    "Telegram tasdiqlandi. Endi saytga qayting: agar akkauntingiz bo'lsa login qiling, bo'lmasa yangi akkaunt oching."
                )
                return
            if verification and verification.get("payload"):
                payload = verification["payload"]
                await message.answer(
                    payload.get("message")
                    or payload.get("error")
                    or "TG tasdiqlashni yakunlab bo'lmadi."
                )
                return
            await message.answer("TG tasdiqlash servisiga ulanib bo'lmadi. Keyinroq urinib ko'ring.")
            return
        if is_registration_ticket_payload(start_payload):
            verification = await complete_telegram_registration(start_payload, message)
            if verification and verification.get("success"):
                await message.answer(
                    "Registratsiya tasdiqlandi. Endi saytga qaytib email va parolingiz bilan login qiling."
                )
                return

            error_code = verification.get("payload", {}).get("error") if verification else None
            if error_code == "REGISTRATION_TOKEN_EXPIRED":
                await message.answer("Registratsiya linki eskirgan. Saytda qayta ro'yxatdan o'ting.")
            elif error_code == "ACCOUNT_ALREADY_EXISTS":
                await message.answer("Bu registratsiya allaqachon ishlatilgan yoki akkaunt yaratilgan. Saytda login qilib ko'ring.")
            else:
                await message.answer("Registratsiyani tasdiqlab bo'lmadi. Saytdan qayta urinib ko'ring.")
            return

        if not is_download_ticket_payload(start_payload):
            await message.answer(
                "Bu link ishlamaydi."
            )
            return

        game_key = await redeem_download_ticket(start_payload)

        if not game_key:
            await message.answer(
                "Link eskirgan yoki avval ishlatilgan. Saytdan qayta yuklashni bosing."
            )
            return

        game = await db.find_one({"key": game_key})
        if game:
            await message.answer(f"<b>{game['name']}</b> yuborilmoqda...", parse_mode="HTML")
            for file_entry in normalize_file_entries(game.get('files', [])):
                try:
                    await send_stored_file(message.chat.id, file_entry)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logging.error(f"Fayl yuborishda xato: {e}")
            return
        else:
            await message.answer("O'yin topilmadi yoki link eskirgan.")
            return
    
    if is_admin(message.from_user.id):
        menu = await get_main_menu()
        await message.answer(f"Xush kelibsiz, Admin {message.from_user.full_name}!", reply_markup=menu)
    else:
        menu = await get_user_menu()
        await message.answer(f"Salom {message.from_user.full_name}! O'yinlarni olish uchun quyidagi tugmani bosing yoki maxsus linkdan foydalaning.", reply_markup=menu)

# --- QO'SHISH ---
@dp.message(Command("addgame"), StateFilter("*"))
async def add_game_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz!")
        return
    await state.clear()
    await message.answer("📝 Yangi o'yin nomini kiriting:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AdminStates.waiting_for_name)

@dp.message(AdminStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"): return
    
    existing_game = await db.find_one({"name": message.text})
    if existing_game:
        await message.answer(f"⚠️ '{message.text}' nomli o'yin allaqachon mavjud. Fayllarni yuborsangiz, eski fayllar yangisiga almashtiriladi.")
    
    await state.update_data(game_name=message.text, files=[])
    await message.answer(f"📥 '{message.text}' uchun fayllarni yuboring. Tugatgach /done deb yozing.")
    await state.set_state(AdminStates.waiting_for_files)

@dp.message(AdminStates.waiting_for_files, F.document | F.video | F.audio)
async def collect_files(message: Message, state: FSMContext):
    data = await state.get_data()
    file_entry = extract_file_entry(message)
    if not file_entry:
        await message.answer("Fayl aniqlanmadi.")
        return
    files = data.get('files', [])
    files.append(file_entry)
    await state.update_data(files=files)
    await message.answer(f"Fayl qo'shildi: {file_entry['file_name']}")

@dp.message(AdminStates.waiting_for_files, Command("done"))
async def save_game(message: Message, state: FSMContext):
    data = await state.get_data()
    name = data.get('game_name')
    files = data.get('files')
    
    if not name or not files:
        await message.answer("Xatolik: Nom yoki fayllar yetarli emas.")
        await state.clear()
        return
        
    msg = await message.answer("💾 Saqlanmoqda, iltimos kuting...")
    game_key = name.lower().replace(" ", "")
    
    await db.update_one({"name": name}, {"key": game_key, "files": files}, upsert=True)
    
    game = await db.find_one({"name": name})
    link = f"https://t.me/{BOT_USERNAME}?start={game['key']}"
    await state.clear()
    menu = await get_main_menu()
    await msg.edit_text(f"🎉 Saqlandi! (ID: {game['id']})\n🔗 Link: <code>{link}</code>", parse_mode="HTML")
    await message.answer("Asosiy menyu:", reply_markup=menu)

# --- QO'SHIMCHA FAYL QO'SHISH ---
@dp.message(Command("addfile"), StateFilter("*"))
async def add_file_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz!")
        return

    games = await db.find_all()
    if not games:
        await message.answer("Baza bo'sh! Avval /addgame bilan o'yin qo'shing.")
        return

    await state.clear()
    buttons = [[KeyboardButton(text=game['name'])] for game in games]
    buttons.append([KeyboardButton(text="вќЊ Bekor qilish")])
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    await message.answer("Qaysi o'yinga qo'shimcha fayl qo'shilsin?", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_for_addfile_game)

@dp.message(AdminStates.waiting_for_addfile_game)
async def process_addfile_game(message: Message, state: FSMContext):
    if message.text == "вќЊ Bekor qilish":
        menu = await get_main_menu()
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=menu)
        return

    game = await db.find_one({"name": message.text})
    if not game:
        await message.answer("Bunday o'yin topilmadi. Ro'yxatdan tanlang.")
        return

    await state.update_data(
        addfile_game_name=game["name"],
        addfile_existing_files=normalize_file_entries(game.get("files", [])),
        addfile_new_files=[]
    )
    await message.answer(
        f"рџ“Ґ '{game['name']}' uchun qo'shimcha fayllarni yuboring. Tugatgach /done_addfile deb yozing.",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_addfile_files)

@dp.message(AdminStates.waiting_for_addfile_files, F.document | F.video | F.audio)
async def collect_addfile_files(message: Message, state: FSMContext):
    data = await state.get_data()
    file_entry = extract_file_entry(message)
    if not file_entry:
        await message.answer("Fayl aniqlanmadi.")
        return
    files = data.get('addfile_new_files', [])
    files.append(file_entry)
    await state.update_data(addfile_new_files=files)
    await message.answer(f"Qo'shimcha fayl yig'ildi: {file_entry['file_name']}")

@dp.message(AdminStates.waiting_for_addfile_files, Command("done_addfile"))
async def save_addfile_files(message: Message, state: FSMContext):
    data = await state.get_data()
    game_name = data.get('addfile_game_name')
    existing_files = data.get('addfile_existing_files', [])
    new_files = data.get('addfile_new_files', [])

    if not game_name or not new_files:
        await message.answer("Xatolik: yangi qo'shiladigan fayllar topilmadi.")
        await state.clear()
        return

    merged_files = existing_files + new_files
    await db.update_one({"name": game_name}, {"files": merged_files}, upsert=False)

    menu = await get_main_menu()
    await state.clear()
    await message.answer(
        f"вњ… '{game_name}' ga {len(new_files)} ta qo'shimcha fayl qo'shildi.\nJami fayllar: {len(merged_files)} ta.",
        reply_markup=menu
    )

# --- FAYLNI OLIB TASHLASH ---
@dp.message(Command("removefile"), StateFilter("*"))
async def remove_file_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz!")
        return

    games = await db.find_all()
    if not games:
        await message.answer("Baza bo'sh!")
        return

    await state.clear()
    buttons = [[KeyboardButton(text=game['name'])] for game in games]
    buttons.append([KeyboardButton(text="Bekor qilish")])
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    await message.answer("Qaysi o'yindan fayl o'chirilsin?", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_for_removefile_game)

@dp.message(AdminStates.waiting_for_removefile_game)
async def process_removefile_game(message: Message, state: FSMContext):
    if message.text == "Bekor qilish":
        menu = await get_main_menu()
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=menu)
        return

    game = await db.find_one({"name": message.text})
    if not game:
        await message.answer("Bunday o'yin topilmadi. Ro'yxatdan tanlang.")
        return

    files = normalize_file_entries(game.get("files", []))
    if not files:
        menu = await get_main_menu()
        await state.clear()
        await message.answer(f"'{game['name']}' uchun fayl topilmadi.", reply_markup=menu)
        return

    await state.update_data(removefile_game_name=game["name"], removefile_files=files)
    file_lines = [f"{index + 1}. {file_entry.get('file_name', f'file_{index + 1}')}" for index, file_entry in enumerate(files)]
    await message.answer(
        f"'{game['name']}' fayllari:\n\n" + "\n".join(file_lines) + "\n\nO'chirish uchun fayl raqamini kiriting:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_removefile_index)

@dp.message(AdminStates.waiting_for_removefile_index)
async def process_removefile_index(message: Message, state: FSMContext):
    raw_value = (message.text or '').strip()
    if raw_value.lower() in {"bekor qilish", "bekor", "cancel"}:
        menu = await get_main_menu()
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=menu)
        return

    if not raw_value.isdigit():
        await message.answer("Faqat fayl raqamini kiriting. Masalan: 1")
        return

    data = await state.get_data()
    game_name = data.get("removefile_game_name")
    files = list(data.get("removefile_files", []))
    file_index = int(raw_value) - 1

    if file_index < 0 or file_index >= len(files):
        await message.answer("Bunday fayl raqami yo'q. Qayta kiriting.")
        return

    removed_file = files.pop(file_index)
    await db.update_one({"name": game_name}, {"files": files}, upsert=False)

    menu = await get_main_menu()
    await state.clear()
    await message.answer(
        f"'{game_name}' dan {file_index + 1}-fayl olib tashlandi.\nQolgan fayllar: {len(files)} ta.\n\nO'chirilgan fayl:\n{removed_file.get('file_name', 'noma?lum fayl')}",
        reply_markup=menu
    )


# --- FAYL NOMINI O'ZGARTIRISH ---
@dp.message(Command("renamefile"), StateFilter("*"))
async def rename_file_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz!")
        return

    games = await db.find_all()
    if not games:
        await message.answer("Baza bo'sh!")
        return

    await state.clear()
    buttons = [[KeyboardButton(text=game['name'])] for game in games]
    buttons.append([KeyboardButton(text="Bekor qilish")])
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    await message.answer("Qaysi o'yindagi fayl nomini o'zgartirasiz?", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_for_renamefile_game)

@dp.message(AdminStates.waiting_for_renamefile_game)
async def process_renamefile_game(message: Message, state: FSMContext):
    if message.text == "Bekor qilish":
        menu = await get_main_menu()
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=menu)
        return

    game = await db.find_one({"name": message.text})
    if not game:
        await message.answer("Bunday o'yin topilmadi. Ro'yxatdan tanlang.")
        return

    files = normalize_file_entries(game.get("files", []))
    if not files:
        menu = await get_main_menu()
        await state.clear()
        await message.answer(f"'{game['name']}' uchun fayl topilmadi.", reply_markup=menu)
        return

    await state.update_data(renamefile_game_name=game["name"], renamefile_files=files)
    file_lines = [f"{index + 1}. {file_entry.get('file_name', f'file_{index + 1}')}" for index, file_entry in enumerate(files)]
    await message.answer(
        f"'{game['name']}' fayllari:\n\n" + "\n".join(file_lines) + "\n\nNomini o'zgartirish uchun fayl raqamini kiriting:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_renamefile_index)

@dp.message(AdminStates.waiting_for_renamefile_index)
async def process_renamefile_index(message: Message, state: FSMContext):
    raw_value = (message.text or '').strip()
    if raw_value.lower() in {"bekor qilish", "bekor", "cancel"}:
        menu = await get_main_menu()
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=menu)
        return

    if not raw_value.isdigit():
        await message.answer("Faqat fayl raqamini kiriting. Masalan: 1")
        return

    data = await state.get_data()
    files = list(data.get("renamefile_files", []))
    file_index = int(raw_value) - 1

    if file_index < 0 or file_index >= len(files):
        await message.answer("Bunday fayl raqami yo'q. Qayta kiriting.")
        return

    selected_file = files[file_index]
    await state.update_data(renamefile_selected_index=file_index)
    await message.answer(
        f"Yangi nomni kiriting:\n\nEski nom: {selected_file.get('file_name', f'file_{file_index + 1}')}",

        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_renamefile_name)

@dp.message(AdminStates.waiting_for_renamefile_name)
async def process_renamefile_name(message: Message, state: FSMContext):
    new_name = (message.text or '').strip()
    if not new_name:
        await message.answer("Yangi nom bo'sh bo'lmasin.")
        return

    data = await state.get_data()
    game_name = data.get("renamefile_game_name")
    files = list(data.get("renamefile_files", []))
    file_index = data.get("renamefile_selected_index")

    if game_name is None or file_index is None or file_index < 0 or file_index >= len(files):
        menu = await get_main_menu()
        await state.clear()
        await message.answer("Holat topilmadi. Qaytadan urinib ko'ring.", reply_markup=menu)
        return

    old_name = files[file_index].get('file_name', f'file_{file_index + 1}')
    files[file_index]['file_name'] = new_name
    await db.update_one({"name": game_name}, {"files": files}, upsert=False)

    menu = await get_main_menu()
    await state.clear()
    await message.answer(
        f"Fayl nomi yangilandi.\n\nEski nom: {old_name}\nYangi nom: {new_name}",
        reply_markup=menu
    )

# --- RO'YXAT ---
@dp.message(Command("list", "links"), StateFilter("*"))
@dp.message(F.text == "🔗 Barcha linklar")
async def list_games(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz!")
        return

    games = await db.find_all()
    if not games:
        await message.answer("Hozircha o'yinlar ro'yxati bo'sh.")
        return
    
    text = "🎮 <b>Tayyor linklar ro'yxati:</b>\n\n"
    for i, game in enumerate(games, 1):
        link = f"https://t.me/{BOT_USERNAME}?start={game['key']}"
        text += f"{i}. <b>{game['name']}</b>\n🔗 <code>{link}</code>\n\n"
    
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

# --- HELP BUYRUQ ---
@dp.message(Command("help"), StateFilter("*"))
async def command_help_handler(message: Message):
    if is_admin(message.from_user.id):
        help_text = (
            "🛠 <b>Admin yordami:</b>\n\n"
            "/start - Botni ishga tushirish\n"
            "/addgame - Yangi o'yin qo'shish\n"
            "/list - Barcha o'yinlar ro'yxatini ko'rish\n"
            "/delgame - O'yinni o'chirish\n"
            "/clear_db - Bazani butunlay tozalash\n"
            "/help - Ushbu yordam xabarini ko'rish"
        )
    else:
        help_text = (
            "ℹ️ <b>Yordam:</b>\n\n"
            "/start - Botni ishga tushirish\n"
            "/help - Ushbu yordam xabarini ko'rish"
        )
    
    await message.answer(help_text, parse_mode="HTML")

# --- O'CHIRISH ---
@dp.message(Command("delgame"), StateFilter("*"))
async def delete_game_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz!")
        return
    
    games = await db.find_all()
    if not games:
        await message.answer("Baza bo'sh!")
        return
        
    await state.clear()
    buttons = [[KeyboardButton(text=game['name'])] for game in games]
    buttons.append([KeyboardButton(text="❌ Bekor qilish")])
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    
    await message.answer("🗑 O'chirmoqchi bo'lgan o'yiningizni tanlang:", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_for_delete)

@dp.message(AdminStates.waiting_for_delete)
async def process_delete(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        menu = await get_main_menu()
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=menu)
        return

    deleted = await db.delete_one({"name": message.text})
    if deleted:
        menu = await get_main_menu()
        await message.answer(f"✅ '{message.text}' o'chirildi!", reply_markup=menu)
    else:
        await message.answer("Bunday o'yin topilmadi.")
    
    await state.clear()

@dp.message(Command("clear_db"), StateFilter("*"))
async def clear_database(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await state.clear()
        await db.delete_many({})
        await message.answer("🗑 Baza butunlay tozalandi!", reply_markup=ReplyKeyboardRemove())

# --- ODDIIY MATN (Tugmalar uchun) ---
@dp.message(F.text, StateFilter(None))
async def handle_game_buttons(message: Message):
    if not is_admin(message.from_user.id):
        # Admin bo'lmagan foydalanuvchilar matn yozsa javob bermaymiz
        return

    game = await db.find_one({"name": message.text})
    if game:
        await message.answer(f"O'yin yuborilmoqda: {game['name']} (ID: {game['id']})")
        for file_entry in normalize_file_entries(game.get('files', [])):
            try:
                await send_stored_file(message.chat.id, file_entry)
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"Xato: {e}")

async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    # MongoDB ulanishini tekshirish
    try:
        logging.info("MongoDB-ga ulanish tekshirilmoqda...")
        await db.client.admin.command('ping')
        logging.info("✅ MongoDB ulanishi muvaffaqiyatli!")
    except Exception as e:
        logging.error(f"❌ MongoDB-ga ulanishda xato: {e}")
        logging.error("Iltimos, MONGO_URL, login va parolni tekshiring.")
        # To'xtatib qo'yamiz, chunki bazasiz bot ishlamaydi
        return

    await start_web_server()
    if not TOKEN:
        logging.error("BOT_TOKEN o'rnatilmagan!")
        return
    if not DOWNLOAD_TICKET_BOT_SECRET:
        logging.error("DOWNLOAD_TICKET_BOT_SECRET o'rnatilmagan!")
        return
    
    try:
        # Eski ulanishlarni va kutilayotgan xabarlarni tozalash
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Pollingda xato: {e}")

if __name__ == "__main__":
    asyncio.run(main())
