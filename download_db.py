import os
import json
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# .env faylidan MONGO_URL ni yuklash
load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

async def download_db():
    if not MONGO_URL:
        print("Xatolik: .env faylida MONGO_URL topilmadi!")
        print("Iltimos, .env faylida MONGO_URL=mongodb+srv://... ekanligini tekshiring.")
        return

    try:
        print("MongoDB-ga ulanish o'rnatilmoqda...")
        client = AsyncIOMotorClient(MONGO_URL)
        
        # main.py dagi baza va kolleksiya nomi
        db = client['tg_bot_db']
        collection = db['games']
        
        print("Ma'lumotlar yuklab olinmoqda...")
        cursor = collection.find({})
        data = await cursor.to_list(length=None)
        
        if not data:
            print("\nBaza bo'sh! Yuklash uchun ma'lumot topilmadi.")
            return

        # MongoDB ID obyektini stringga aylantirish (JSON formatiga moslash uchun)
        for item in data:
            if '_id' in item:
                item['_id'] = str(item['_id'])
        
        # Faylga saqlash (chiroyli formatda)
        filename = 'database_backup.json'
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        
        print(f"\nTabriklaymiz! Baza muvaffaqiyatli '{filename}' fayliga yuklandi.")
        print(f"Jami yuklangan elementlar: {len(data)} ta.")
        
    except Exception as e:
        print(f"\nXatolik yuz berdi: {e}")

if __name__ == "__main__":
    asyncio.run(download_db())
