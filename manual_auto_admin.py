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
from pyrogram.raw import functions, types
from sqlalchemy import select
from database import get_session
from models import Worker, AppSetting
from config import settings

async def promote_bot(app, channel_peer, bot_username_or_id):
    try:
        bot_peer = await app.resolve_peer(bot_username_or_id)
    except Exception as e:
        return False, f"Topilmadi: {e}"

    try:
        # Full rights
        await app.invoke(
            functions.channels.EditAdmin(
                channel=channel_peer,
                user_id=bot_peer,
                admin_rights=types.ChatAdminRights(
                    post_messages=True,
                    delete_messages=True,
                    edit_messages=True,
                ),
                rank="Reaksiya",
            )
        )
        return True, "To'liq huquq"
    except Exception as e:
        # Minimal rights
        try:
            await app.invoke(
                functions.channels.EditAdmin(
                    channel=channel_peer,
                    user_id=bot_peer,
                    admin_rights=types.ChatAdminRights(
                        post_messages=True,
                    ),
                    rank="Reaksiya",
                )
            )
            return True, "Faqat post huquqi"
        except Exception as e2:
            return False, f"Xatolik: {e2}"

async def main():
    print("="*50)
    print("QO'LDA AVTO-ADMIN QILISH SCRIPT")
    print("="*50)

    async with get_session() as db_session:
        setting = await db_session.get(AppSetting, "auto_admin_session")
        if not setting or not setting.value or "session" not in setting.value:
            print("❌ Avto-Admin sessiyasi bazada yo'q! Oldin login_admin.py ni ishga tushiring.")
            return

        session_string = setting.value["session"]

        workers = (await db_session.execute(
            select(Worker).where(Worker.is_active == True)
        )).scalars().all()

        if not workers:
            print("❌ Bazada aktiv worker botlar yo'q!")
            return

    print(f"✅ Bazada {len(workers)} ta aktiv worker topildi.")
    
    channel_input = input("📢 Kanal ID si yoki username kiriting (masalan: -100123456789 yoki @kanal_nomi): ").strip()
    if not channel_input:
        print("❌ Kanal manzili kiritilmadi.")
        return

    # ID bo'lsa int ga o'giramiz
    try:
        channel_id_or_username = int(channel_input)
    except ValueError:
        channel_id_or_username = channel_input

    app = Client(
        name="manual_admin_temp",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=session_string,
        in_memory=True
    )

    print("\n⏳ Telegramga ulanmoqda...")
    try:
        await app.connect()
        
        print("🔍 Kanal qidirilmoqda...")
        try:
            channel_peer = await app.resolve_peer(channel_id_or_username)
        except Exception as e:
            print(f"❌ Kanalni topib bo'lmadi! Xatolik: {e}")
            print("❗ Eslatma: Admin akkaunt shu kanalga a'zo bo'lishi kerak!")
            await app.disconnect()
            return
            
        print("✅ Kanal topildi! Botlarni admin qilish boshlandi...\n")
        
        ok_count = 0
        fail_count = 0
        
        for i, worker in enumerate(workers, 1):
            target = worker.username if worker.username else worker.token.split(":")[0]
            print(f"[{i}/{len(workers)}] @{target} admin qilinmoqda... ", end="")
            
            success, msg = await promote_bot(app, channel_peer, target)
            if success:
                print(f"✅ ({msg})")
                ok_count += 1
            else:
                print(f"❌ ({msg})")
                fail_count += 1
                
            await asyncio.sleep(2) # Flood kutish
            
        print("\n" + "="*50)
        print(f"Tugadi! Muvaffaqiyatli: {ok_count}, Xato: {fail_count}")
        print("="*50)
        
        await app.disconnect()
        
    except Exception as e:
        print(f"Tizim xatosi: {e}")
        try:
            await app.disconnect()
        except:
            pass

if __name__ == "__main__":
    asyncio.run(main())
