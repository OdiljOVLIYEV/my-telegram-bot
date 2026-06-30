import os
import json
import asyncio
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from bson import ObjectId

# .env faylidan MONGO_URL ni yuklash
load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

async def restore_db(force=False):
    if not MONGO_URL:
        print("Xatolik: .env faylida MONGO_URL topilmadi!")
        return

    filename = 'database_backup.json'
    if not os.path.exists(filename):
        print(f"Xatolik: '{filename}' fayli topilmadi! Avval download_db.py orqali yuklab oling.")
        return

    try:
        print("MongoDB-ga ulanish o'rnatilmoqda...")
        client = AsyncIOMotorClient(MONGO_URL)
        db = client['tg_bot_db']
        collection = db['games']

        print(f"'{filename}' fayli o'qilmoqda...")
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not data:
            print("Fayl bo'sh, tiklash uchun ma'lumot yo'q.")
            return

        print(f"Jami {len(data)} ta element topildi. Tiklash boshlanmoqda...")

        # Ma'lumotlarni tayyorlash
        for item in data:
            if '_id' in item:
                # String ID ni ObjectId ga aylantirish (agar u ObjectId formatida bo'lsa)
                try:
                    item['_id'] = ObjectId(item['_id'])
                except:
                    # Agar ObjectId bo'lmasa (masalan, custom string ID), o'z holicha qoladi
                    pass

        # Eskisini o'chirib, yangisini yozish (to'liq tiklash)
        if not force:
            confirm = input("DIQQAT: Bazadagi mavjud ma'lumotlar o'chiriladi va backupdan tiklanadi. Davom etamizmi? (ha/yo'q): ")
            if confirm.lower() != 'ha':
                print("Jarayon bekor qilindi.")
                return

        print("Eski ma'lumotlar tozalanmoqda...")
        await collection.delete_many({})

        print("Yangi ma'lumotlar yuklanmoqda...")
        await collection.insert_many(data)

        print(f"\nTabriklaymiz! Baza muvaffaqiyatli '{filename}' faylidan tiklandi.")
        print(f"Jami tiklangan elementlar: {len(data)} ta.")

    except Exception as e:
        print(f"\nXatolik yuz berdi: {e}")

if __name__ == "__main__":
    force_mode = "--force" in sys.argv
    asyncio.run(restore_db(force=force_mode))
