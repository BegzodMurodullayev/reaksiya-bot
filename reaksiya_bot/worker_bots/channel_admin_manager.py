"""
Reaksiya botlarini kanalga admin qilish
AVTOMATIK: Session yo'q bo'lsa telefon raqam so'raydi va yaratadi
"""

# ── Python 3.10+ event loop fix ──
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import sys
from pyrogram import Client
from pyrogram.raw import functions, types
from pyrogram.errors import (
    FloodWait,
    RightForbidden,
    ChatAdminRequired,
    FreshChangeAdminsForbidden,
    RPCError,
)

# ─────────────────────────────────────────
# SOZLAMALAR
# ─────────────────────────────────────────
API_ID     = 32003716
API_HASH   = "fa740b9dfde98b4dc6e541d66f665815"

CHANNELS = [
    "@NeoSaleUz",
    # qo'shimcha kanallar...
]

TOKENS_FILE            = "created_tokens.txt"
SESSION_FILE           = "session_string_4.txt"
TEMP_SESSION_NAME      = "temp_login_session"
DELAY_BETWEEN_BOTS     = 3
DELAY_BETWEEN_CHANNELS = 5
DELAY_ON_FLOOD         = 30


# ─────────────────────────────────────────
# SESSION YARATISH (agar mavjud bo'lmasa)
# ─────────────────────────────────────────
async def create_session_if_needed() -> str:
    """
    Session string mavjud bo'lsa - qaytaradi
    Yo'q bo'lsa - telefon raqam so'raydi va yaratadi
    """
    # Agar mavjud bo'lsa
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            session = f.read().strip()
        if session:
            print(f"✅ Session mavjud ({SESSION_FILE})")
            return session
    
    # Mavjud emas - yangi yaratish
    print("\n" + "="*60)
    print("⚠️  SESSION STRING TOPILMADI")
    print("="*60)
    print("\n📱 Telegram hisobingizga kirish kerak:")
    print("   • Telefon raqamingizni kiriting")
    print("   • SMS kod keladi")
    print("   • 2FA parol bor bo'lsa, uni ham kiriting")
    print("\n" + "="*60 + "\n")
    
    # Telefon raqamni so'rash
    phone = input("📞 Telefon raqamingizni kiriting (+998901234567): ").strip()
    if not phone:
        print("❌ Telefon raqam kiritilmadi!")
        sys.exit(1)
    
    # Temporary client yaratish
    temp_app = Client(
        name=TEMP_SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        phone_number=phone,
        workdir="."
    )
    
    try:
        print("\n🔐 Telegram ga ulanmoqda...")
        await temp_app.connect()
        
        # Kod yuborish
        sent_code = await temp_app.send_code(phone)
        print(f"✅ SMS kod yuborildi: {sent_code.phone_code_hash[:10]}...")
        
        # SMS kodni so'rash
        code = input("\n📩 SMS kodni kiriting: ").strip()
        if not code:
            print("❌ SMS kod kiritilmadi!")
            await temp_app.disconnect()
            sys.exit(1)
        
        # Sign in qilish
        try:
            await temp_app.sign_in(phone, sent_code.phone_code_hash, code)
            print("✅ Muvaffaqiyatli login!")
        except Exception as e:
            # 2FA parol kerak bo'lishi mumkin
            if "password" in str(e).lower() or "2fa" in str(e).lower():
                password = input("\n🔒 2FA parolni kiriting: ").strip()
                if not password:
                    print("❌ 2FA parol kiritilmadi!")
                    await temp_app.disconnect()
                    sys.exit(1)
                await temp_app.check_password(password)
                print("✅ 2FA parol to'g'ri!")
            else:
                raise
        
        # Session string olish
        session_string = await temp_app.export_session_string()
        
        # User ma'lumotini olish
        me = await temp_app.get_me()
        print(f"\n✅ Login muvaffaqiyatli!")
        print(f"👤 Ism: {me.first_name}")
        print(f"📱 Username: @{me.username if me.username else 'yo\'q'}")
        print(f"🆔 ID: {me.id}")
        
        # Session stringni saqlash
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(session_string)
        print(f"\n💾 Session saqlandi: {SESSION_FILE}")
        
        # Disconnect
        await temp_app.disconnect()
        
        # Temporary session faylni o'chirish
        try:
            os.remove(f"{TEMP_SESSION_NAME}.session")
        except:
            pass
        
        print("\n" + "="*60)
        print("✅ Session muvaffaqiyatli yaratildi!")
        print("="*60 + "\n")
        
        return session_string
        
    except Exception as e:
        print(f"\n❌ Xato: {e}")
        await temp_app.disconnect()
        sys.exit(1)


# ─────────────────────────────────────────
# BOT USERNAME LARI O'QISH
# ─────────────────────────────────────────
def load_bots(filepath: str) -> list:
    bots = []
    if not os.path.exists(filepath):
        print(f"❌ Fayl topilmadi: {filepath}")
        return bots
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue
            username = line.split("=")[0].strip()
            if username:
                bots.append(username)
    print(f"✅ {len(bots)} ta bot o'qildi: {filepath}")
    return bots


# ─────────────────────────────────────────
# BOTNI ADMIN QILISH
# ─────────────────────────────────────────
async def promote_bot(app: Client, channel: str, bot_username: str) -> dict:
    # Channel peer
    try:
        channel_peer = await app.resolve_peer(channel)
    except Exception as e:
        return {"ok": False, "method": "failed", "error": f"Kanal topilmadi: {e}"}

    # Bot peer
    try:
        bot_peer = await app.resolve_peer(bot_username)
    except Exception as e:
        return {"ok": False, "method": "failed", "error": f"Bot topilmadi: {e}"}

    # 1-urinish: to'liq huquqlar
    try:
        await app.invoke(
            functions.channels.EditAdmin(
                channel=channel_peer,
                user_id=bot_peer,
                admin_rights=types.ChatAdminRights(
                    post_messages=True,
                    delete_messages=True,
                    edit_messages=True,
                ),
                rank="Bot",
            )
        )
        return {"ok": True, "method": "full", "error": None}

    except RightForbidden:
        pass  # 2-urinishga

    except FreshChangeAdminsForbidden:
        return {"ok": False, "method": "failed",
                "error": "Yangi login — 24 soat kutish kerak"}

    except FloodWait as e:
        wait = e.value + DELAY_ON_FLOOD
        print(f"\n    ⏳ FloodWait {wait}s ...", flush=True)
        await asyncio.sleep(wait)
        return await promote_bot(app, channel, bot_username)

    except RPCError as e:
        return {"ok": False, "method": "failed", "error": str(e)}

    # 2-urinish: faqat post_messages
    try:
        await app.invoke(
            functions.channels.EditAdmin(
                channel=channel_peer,
                user_id=bot_peer,
                admin_rights=types.ChatAdminRights(
                    post_messages=True,
                ),
                rank="Bot",
            )
        )
        return {"ok": True, "method": "minimal", "error": None}

    except RightForbidden:
        return {"ok": False, "method": "failed",
                "error": "Kanal egasi admin qo'shishni taqiqlagan"}

    except FreshChangeAdminsForbidden:
        return {"ok": False, "method": "failed",
                "error": "Yangi login — 24 soat kutish kerak"}

    except FloodWait as e:
        wait = e.value + DELAY_ON_FLOOD
        print(f"\n    ⏳ FloodWait {wait}s ...", flush=True)
        await asyncio.sleep(wait)
        return await promote_bot(app, channel, bot_username)

    except RPCError as e:
        return {"ok": False, "method": "failed", "error": str(e)}


# ─────────────────────────────────────────
# NATIJALARNI SAQLASH
# ─────────────────────────────────────────
def save_results(results: list, filename="results.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        for r in results:
            icon = "✅" if r["ok"] else "❌"
            f.write(f"{icon} {r['channel']} | {r['bot']} | {r['method']} | {r['error'] or 'OK'}\n")
    print(f"📄 Natijalar saqlandi: {filename}")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
async def main():
    print("=" * 60)
    print("   Reaksiya Bot Admin Qilish (Avtomatik)")
    print("=" * 60)
    print()

    # Session yaratish yoki olish
    session = await create_session_if_needed()

    # Botlarni yuklash
    BOTS = load_bots(TOKENS_FILE)
    if not BOTS:
        print("❌ Botlar topilmadi.")
        sys.exit(1)

    print(f"\n📢 Kanallar : {len(CHANNELS)} ta")
    print(f"🤖 Botlar   : {len(BOTS)} ta")
    print(f"📊 Jami     : {len(CHANNELS) * len(BOTS)} ta amal\n")

    # Client
    app = Client(
        name="reaction_manager",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session,
        in_memory=True,
    )

    results = []

    try:
        async with app:
            me = await app.get_me()
            print(f"👤 Hozirgi akkaunt: @{me.username or me.first_name}")
            print(f"🆔 ID: {me.id}\n")

            for ch_idx, channel in enumerate(CHANNELS, 1):
                print(f"\n{'─'*50}")
                print(f"[{ch_idx}/{len(CHANNELS)}] Kanal: {channel}")
                print(f"{'─'*50}")

                ok_count = fail_count = 0
                fresh_error = False

                for bot_idx, bot in enumerate(BOTS, 1):
                    print(
                        f"  [{bot_idx:02d}/{len(BOTS):02d}] {bot:<32} → {channel} ... ",
                        end="", flush=True
                    )

                    result = await promote_bot(app, channel, bot)
                    result["channel"] = channel
                    result["bot"] = bot
                    results.append(result)

                    if result["ok"]:
                        label = "(to'liq)" if result["method"] == "full" else "(minimal)"
                        print(f"✅ {label}")
                        ok_count += 1
                    else:
                        print(f"❌ {result['error']}")
                        fail_count += 1

                        if "24 soat" in result["error"]:
                            fresh_error = True
                            break

                    if bot_idx < len(BOTS):
                        await asyncio.sleep(DELAY_BETWEEN_BOTS)

                print(f"\n  📊 {channel}: ✅ {ok_count} | ❌ {fail_count}")

                if fresh_error:
                    print("\n⛔ 24 soat o'tgach qayta ishga tushiring!")
                    break

                if ch_idx < len(CHANNELS):
                    print(f"\n⏸  {DELAY_BETWEEN_CHANNELS}s kutish...")
                    await asyncio.sleep(DELAY_BETWEEN_CHANNELS)

    except Exception as e:
        print(f"\n❌ XATO: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    total_ok   = sum(1 for r in results if r["ok"])
    total_fail = len(results) - total_ok

    print(f"\n{'='*60}")
    print(f"  YAKUNIY NATIJA")
    print(f"{'='*60}")
    print(f"  ✅ Muvaffaqiyatli : {total_ok}")
    print(f"  ❌ Xato           : {total_fail}")
    print(f"  📊 Jami           : {len(results)}")
    print(f"{'='*60}")

    save_results(results)


if __name__ == "__main__":
    asyncio.run(main())