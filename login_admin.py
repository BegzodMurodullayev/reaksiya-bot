"""
Avtomatik botlarni ulash uchun kerak bo'ladigan Admin Session'ni yaratuvchi script.
Ushbu script ishga tushirilganda telefon raqam va kod so'raladi.
Muvaffaqiyatli login qilingandan so'ng, session_string to'g'ridan-to'g'ri bazaga saqlanadi.
"""

import asyncio
import os
import sys

# Python 3.10+ event loop fix for Pyrogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Force utf-8 encoding for standard output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from pyrogram import Client
from sqlalchemy import select
from config import settings
from database import get_session
from models import AppSetting

API_ID = settings.API_ID
API_HASH = settings.API_HASH

if not API_ID or not API_HASH:
    print("❌ Xato: .env faylidan API_ID yoki API_HASH topilmadi!")
    sys.exit(1)

TEMP_SESSION_NAME = "temp_admin_login"

async def save_session_to_db(session_string: str, username: str):
    async with get_session() as db_session:
        # Check if already exists
        setting = await db_session.get(AppSetting, "auto_admin_session")
        if setting is None:
            setting = AppSetting(key="auto_admin_session", value={"session": session_string, "username": username})
            db_session.add(setting)
        else:
            setting.value = {"session": session_string, "username": username}
    print("✅ Session muvaffaqiyatli bazaga saqlandi!")

async def main():
    print("=" * 60)
    print("   Auto-Admin uchun Session yaratish")
    print("=" * 60)
    print("\n📱 Telegram hisobingizga kirish kerak (bu akkaunt botlarni kanalga qo'sha oladigan admin bo'lishi kerak):")
    
    phone = input("📞 Telefon raqamingizni kiriting (+998901234567): ").strip()
    if not phone:
        print("❌ Telefon raqam kiritilmadi!")
        return

    temp_app = Client(
        name=TEMP_SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        phone_number=phone,
        in_memory=True
    )

    try:
        print("\n🔐 Telegram ga ulanmoqda...")
        await temp_app.connect()
        
        sent_code = await temp_app.send_code(phone)
        print(f"✅ SMS kod yuborildi!")
        
        code = input("\n📩 SMS kodni kiriting: ").strip()
        if not code:
            print("❌ SMS kod kiritilmadi!")
            await temp_app.disconnect()
            return
        
        try:
            await temp_app.sign_in(phone, sent_code.phone_code_hash, code)
        except Exception as e:
            if "password" in str(e).lower() or "2fa" in str(e).lower():
                password = input("\n🔒 2FA parolni kiriting: ").strip()
                if not password:
                    print("❌ 2FA parol kiritilmadi!")
                    await temp_app.disconnect()
                    return
                await temp_app.check_password(password)
            else:
                raise
        
        session_string = await temp_app.export_session_string()
        me = await temp_app.get_me()
        
        print(f"\n✅ Login muvaffaqiyatli!")
        username = me.username if me.username else me.first_name
        print(f"👤 Ism: {me.first_name}")
        print(f"📱 Username: @{username}")
        
        await save_session_to_db(session_string, username)
        
        await temp_app.disconnect()
        
    except Exception as e:
        print(f"\n❌ Xato: {e}")
        try:
            await temp_app.disconnect()
        except:
            pass

if __name__ == "__main__":
    asyncio.run(main())
