import asyncio
import logging
import sys
import os
import certifi # SSL xatolarini tuzatish uchun
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

# --- SOZLAMALAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
admin_ids_str = os.getenv("ADMIN_ID", "")
ADMIN_IDS = [int(i.strip()) for i in admin_ids_str.split(",") if i.strip().isdigit()]
MONGO_URL = os.getenv("MONGO_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME", "uz_filtr_fayl_bot")
PORT = int(os.getenv("PORT", 8080))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# MongoDB ulanishi
db = None
collection = None
if MONGO_URL:
    try:
        # SSL sertifikatlarini to'g'ri o'qish uchun certifi ishlatamiz
        cluster = AsyncIOMotorClient(
            MONGO_URL, 
            serverSelectionTimeoutMS=5000,
            tlsCAFile=certifi.where(),
            tlsAllowInvalidCertificates=True,
            tls=True
        )
        db = cluster["tg_bot_db"]
        collection = db["games"]
        logging.info("MongoDB-ga ulanish sozlandi.")
    except Exception as e:
        logging.error(f"MongoDB ulanishini sozlashda xato: {e}")

bot = Bot(token=TOKEN) if TOKEN else None
dp = Dispatcher()

# --- HEALTH CHECK ---
async def handle_health(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_head("/", handle_health) # HEAD so'rovlarini ham qabul qilish
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Veb-server {PORT}-portda ishga tushdi.")

async def test_mongodb():
    global collection
    if collection is not None:
        try:
            # Ulanishni tekshirish uchun oddiy amal
            await cluster.admin.command('ping')
            logging.info("MongoDB-ga muvaffaqiyatli ulanish tasdiqlandi.")
            return True
        except Exception as e:
            logging.error(f"MongoDB ulanishini tekshirishda xato: {e}")
            return False
    return False

class AdminStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_files = State()
    waiting_for_delete = State()

async def get_main_menu():
    if collection is None:
        logging.error("Xatolik: MongoDB-ga ulanmagan!")
        return ReplyKeyboardRemove()
    try:
        logging.info("Bazadan o'yinlar ro'yxati olinmoqda...")
        cursor = collection.find({})
        games = await cursor.to_list(length=100)
        if not games: return ReplyKeyboardRemove()
        buttons = [[KeyboardButton(text=game['name'])] for game in games]
        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    except Exception as e:
        logging.error(f"Bazadan ma'lumot olishda xato: {e}")
        return ReplyKeyboardRemove()

# --- START BUYRUQ ---
@dp.message(CommandStart(), StateFilter("*"))
async def command_start_handler(message: Message, state: FSMContext):
    logging.info(f"User {message.from_user.id} /start bosdi.")
    await state.clear()
    args = message.text.split()
    
    # Link orqali kirilganda (masalan /start gamekey)
    if len(args) > 1:
        if collection is None:
            await message.answer("❌ Xatolik: Baza bilan aloqa yo'q!")
            return
            
        game_key = args[1].lower()
        try:
            game = await collection.find_one({"key": game_key})
            if game:
                await message.answer(f"📦 <b>{game['name']}</b> yuborilmoqda...", parse_mode="HTML")
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
        except Exception as e:
            logging.error(f"MongoDB-dan o'yin qidirishda xato: {e}")
            await message.answer("❌ Bazaga ulanishda xatolik yuz berdi.")
            return
    
    # Oddiy /start bosilganda
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
    if collection is None:
        await message.answer("❌ Xatolik: Baza bilan aloqa o'rnatilmagan (MONGO_URL xato bo'lishi mumkin).")
        return
    await state.clear()
    await message.answer("📝 Yangi o'yin nomini kiriting:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AdminStates.waiting_for_name)

@dp.message(AdminStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    if message.text.startswith("/"): return
    
    try:
        existing_game = await collection.find_one({"name": message.text})
        if existing_game:
            await message.answer(f"⚠️ '{message.text}' nomli o'yin allaqachon mavjud. Fayllarni yuborsangiz, eski fayllar yangisiga almashtiriladi.")
    except Exception as e:
        logging.error(f"process_name ichida xato: {e}")
        await message.answer("⚠️ Baza bilan aloqa sekin yoki xato, lekin davom ettirishingiz mumkin.")
    
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
        
    game_key = name.lower().replace(" ", "")
    # Nom bir xil bo'lsa eski fayllarni saqlab qolish yoki yangilashni tanlash mumkin.
    # Hozircha mavjudini yangilaydi (upsert).
    await collection.update_one(
        {"name": name},
        {"$set": {"name": name, "key": game_key, "files": files}},
        upsert=True
    )
    
    link = f"https://t.me/{BOT_USERNAME}?start={game_key}"
    await state.clear()
    menu = await get_main_menu()
    await message.answer(f"🎉 Saqlandi!\n🔗 Link: <code>{link}</code>", parse_mode="HTML", reply_markup=menu)

# --- O'CHIRISH ---
@dp.message(Command("delgame"), StateFilter("*"))
async def delete_game_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz!")
        return
    
    cursor = collection.find({})
    games = await cursor.to_list(length=100)
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

    res = await collection.delete_one({"name": message.text})
    if res.deleted_count > 0:
        menu = await get_main_menu()
        await message.answer(f"✅ '{message.text}' o'chirildi!", reply_markup=menu)
    else:
        await message.answer("Bunday o'yin topilmadi.")
    
    await state.clear()

@dp.message(Command("clear_db"), StateFilter("*"))
async def clear_database(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await state.clear()
        await collection.delete_many({})
        await message.answer("🗑 Baza butunlay tozalandi!", reply_markup=ReplyKeyboardRemove())

# --- ODDIIY MATN (Tugmalar uchun) ---
@dp.message(F.text, StateFilter(None))
async def handle_game_buttons(message: Message):
    # Faqat adminlar tugmalardan foydalana oladi
    if not is_admin(message.from_user.id):
        return

    game = await collection.find_one({"name": message.text})
    if game:
        await message.answer(f"🚀 {game['name']} yuborilmoqda...")
        for fid in game['files']:
            try:
                await bot.send_document(chat_id=message.chat.id, document=fid)
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"Xato: {e}")

async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    # Avval veb-serverni ishga tushiramiz (Render uchun)
    await start_web_server()
    
    # Keyin o'zgaruvchilarni tekshiramiz
    if not TOKEN or not MONGO_URL:
        logging.error("BOT_TOKEN yoki MONGO_URL o'rnatilmagan!")
        return

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Pollingda xato: {e}")

if __name__ == "__main__":
    asyncio.run(main())
