import asyncio
import os
import sys

# Windows utf-8 fix
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from aiogram import Bot
from bot_registry import register_worker, read_tokens_file
from dotenv import load_dotenv

load_dotenv(".env")
MASTER_TOKEN = os.getenv("MASTER_TOKEN")

async def main():
    bot = Bot(token=MASTER_TOKEN)
    tokens = read_tokens_file("new_recovered_tokens.txt")
    print(f"Fayldan o'qildi: {len(tokens)} ta token.")
    
    success = 0
    duplicate = 0
    failed = 0
    
    for i, t in enumerate(tokens, 1):
        print(f"[{i}/{len(tokens)}] Kiritilmoqda...")
        res = await register_worker(bot, t)
        status = res.get("status")
        if status in ("added", "reactivated"):
            success += 1
            print(f" ✅ Muvaffaqiyatli: {res.get('username')}")
        elif status == "existing":
            duplicate += 1
            print(f" ⚠️ Allaqachon mavjud: {res.get('username')}")
        else:
            failed += 1
            print(f" ❌ Xato: {res}")
            
    print("\n" + "="*40)
    print(f"Bajarildi! Yangi/Tiklandi: {success}, Takror: {duplicate}, Xato: {failed}")
    
    # Yangi tokenlarni asosiy created_tokens.txt fayliga qoshib qoyish
    with open("new_recovered_tokens.txt", "r", encoding="utf-8") as nf:
        new_lines = nf.read()
    
    with open("created_tokens.txt", "a", encoding="utf-8") as cf:
        cf.write(new_lines)
        
    print("Yangi tokenlar asosiy 'created_tokens.txt' fayliga ham biriktirildi!")
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
