import asyncio
import logging
import sys
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from aiohttp import web

# --- SOZLAMALAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL") # MongoDB Atlas ulanish kodi
admin_ids_str = os.getenv("ADMIN_ID", "")
ADMIN_IDS = [int(i.strip()) for i in admin_ids_str.split(",") if i.strip().isdigit()]
BOT_USERNAME = os.getenv("BOT_USERNAME", "uz_filtr_fayl_bot")
PORT = int(os.getenv("PORT", 8080))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# MongoDB Baza boshqaruvi
class MongoDatabase:
    def __init__(self, url):
        self.client = AsyncIOMotorClient(url)
        self.db = self.client['tg_bot_db']
        self.collection = self.db['games']

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
    return web.Response(text="Bot is running (JSON DB Mode)!")

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
    waiting_for_delete = State()

async def get_main_menu():
    games = await db.find_all()
    if not games: return ReplyKeyboardRemove()
    buttons = [[KeyboardButton(text=game['name'])] for game in games]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- START BUYRUQ ---
@dp.message(CommandStart(), StateFilter("*"))
async def command_start_handler(message: Message, state: FSMContext):
    logging.info(f"User {message.from_user.id} /start bosdi.")
    await state.clear()
    args = message.text.split()
    
    if len(args) > 1:
        game_key = args[1].lower()
        game = await db.find_one({"key": game_key})
        if game:
            await message.answer(f"📦 <b>{game['name']}</b> (ID: {game['id']}) yuborilmoqda...", parse_mode="HTML")
            for file_id in game['files']:
                try:
                    await bot.send_document(chat_id=message.chat.id, document=file_id)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logging.error(f"Fayl yuborishda xato: {e}")
            return
        else:
            await message.answer("❌ O'yin topilmadi yoki link eskirgan.")
            return
    
    if is_admin(message.from_user.id):
        menu = await get_main_menu()
        await message.answer(f"Xush kelibsiz, Admin {message.from_user.full_name}!", reply_markup=menu)
    else:
        await message.answer(f"Salom {message.from_user.full_name}! O'yinlarni olish uchun maxsus linkdan foydalaning.", reply_markup=ReplyKeyboardRemove())

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
    fid = message.document.file_id if message.document else (message.video.file_id if message.video else message.audio.file_id)
    files = data.get('files', [])
    files.append(fid)
    await state.update_data(files=files)
    await message.answer(f"✅ {len(files)}-fayl qo'shildi.")

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

# --- RO'YXAT ---
@dp.message(Command("list"), StateFilter("*"))
async def list_games(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    games = await db.find_all()
    if not games:
        await message.answer("O'yinlar ro'yxati bo'sh.")
        return
    
    text = "🎮 <b>O'yinlar ro'yxati:</b>\n\n"
    for i, game in enumerate(games, 1):
        link = f"https://t.me/{BOT_USERNAME}?start={game['key']}"
        text += f"{i}. <b>{game['name']}</b> (ID: {game['id']})\n🔗 <code>{link}</code>\n\n"
    
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

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
        return

    game = await db.find_one({"name": message.text})
    if game:
        await message.answer(f"🚀 {game['name']} (ID: {game['id']}) yuborilmoqda...")
        for fid in game['files']:
            try:
                await bot.send_document(chat_id=message.chat.id, document=fid)
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"Xato: {e}")

async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    await start_web_server()
    if not TOKEN:
        logging.error("BOT_TOKEN o'rnatilmagan!")
        return
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Pollingda xato: {e}")

if __name__ == "__main__":
    asyncio.run(main())
