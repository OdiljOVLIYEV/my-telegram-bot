import asyncio
import logging
import sys
import json
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
from aiohttp import web

# --- SOZLAMALAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DB_FILE = "database.json"
BOT_USERNAME = os.getenv("BOT_USERNAME", "uz_filtr_fayl_bot")
PORT = int(os.getenv("PORT", 8080))

if not TOKEN:
    print("Xatolik: BOT_TOKEN topilmadi!")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- RENDER HEALTH CHECK ---
async def handle_health(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

class AdminStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_files = State()
    waiting_for_delete = State()

def load_db():
    if not os.path.exists(DB_FILE): return {}
    with open(DB_FILE, "r") as f:
        try: return json.load(f)
        except: return {}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_main_menu():
    db = load_db()
    if not db: return ReplyKeyboardRemove()
    buttons = [[KeyboardButton(text=name)] for name in db.keys()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- START BUYRUQ ---
@dp.message(CommandStart(), StateFilter("*"))
async def command_start_handler(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    db = load_db()
    
    if len(args) > 1:
        game_key = args[1].lower()
        for name, files in db.items():
            if name.lower().replace(" ", "") == game_key:
                await message.answer(f"📦 <b>{name}</b> yuborilmoqda...", parse_mode="HTML")
                for file_id in files:
                    await bot.send_document(chat_id=message.chat.id, document=file_id)
                    await asyncio.sleep(0.5)
                return
    
    await message.answer(f"Salom {message.from_user.full_name}!", reply_markup=get_main_menu())

# --- QO'SHISH ---
@dp.message(Command("addgame"), StateFilter("*"))
async def add_game_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Siz admin emassiz!")
        return
    await state.clear()
    await message.answer("📝 Yangi o'yin nomini kiriting:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AdminStates.waiting_for_name)

@dp.message(AdminStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    if message.text.startswith("/"): return
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
    if not data.get('game_name') or not data.get('files'):
        await message.answer("Xatolik: Nom yoki fayllar yetarli emas.")
        await state.clear()
        return
        
    db = load_db()
    db[data['game_name']] = data['files']
    save_db(db)
    
    link = f"https://t.me/{BOT_USERNAME}?start={data['game_name'].lower().replace(' ', '')}"
    await state.clear()
    await message.answer(f"🎉 Saqlandi!\n🔗 Link: <code>{link}</code>", parse_mode="HTML", reply_markup=get_main_menu())

# --- O'CHIRISH ---
@dp.message(Command("delgame"), StateFilter("*"))
async def delete_game_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Siz admin emassiz!")
        return
    
    db = load_db()
    if not db:
        await message.answer("Baza bo'sh!")
        return
        
    await state.clear()
    buttons = [[KeyboardButton(text=name)] for name in db.keys()]
    buttons.append([KeyboardButton(text="❌ Bekor qilish")])
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    
    await message.answer("🗑 O'chirmoqchi bo'lgan o'yiningizni tanlang:", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_for_delete)

@dp.message(AdminStates.waiting_for_delete)
async def process_delete(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Amal bekor qilindi.", reply_markup=get_main_menu())
        return

    db = load_db()
    game_name = message.text
    if game_name in db:
        del db[game_name]
        save_db(db)
        await message.answer(f"✅ '{game_name}' o'chirildi!", reply_markup=get_main_menu())
    else:
        await message.answer("Bunday o'yin topilmadi.")
    
    await state.clear()

@dp.message(Command("clear_db"), StateFilter("*"))
async def clear_database(message: Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await state.clear()
        save_db({})
        await message.answer("🗑 Baza butunlay tozalandi!", reply_markup=ReplyKeyboardRemove())

# --- ODDIIY MATN (Tugmalar uchun) ---
@dp.message(F.text, StateFilter(None))
async def handle_game_buttons(message: Message):
    db = load_db()
    if message.text in db:
        await message.answer(f"🚀 {message.text} yuborilmoqda...")
        for fid in db[message.text]:
            try:
                await bot.send_document(chat_id=message.chat.id, document=fid)
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"Xato: {e}")

async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
