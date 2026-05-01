"""
Auto-Admin Service
Bu xizmat bazada saqlangan auto_admin_session orqali Pyrogram yordamida kanalga ulanib,
barcha aktiv worker botlarni kanalga qo'shadi va ularga admin (reaksiya/post) huquqini beradi.
"""

import asyncio
import logging

# Python 3.10+ event loop fix for Pyrogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from pyrogram.raw import functions, types
from pyrogram.errors import (
    FloodWait,
    RightForbidden,
    FreshChangeAdminsForbidden,
    RPCError,
    PeerIdInvalid
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import get_session
from models import AppSetting, Channel, ChannelWorker, Worker
from config import settings

logger = logging.getLogger(__name__)

DELAY_BETWEEN_BOTS = 3
DELAY_ON_FLOOD = 30

async def promote_bot(app: Client, channel_peer, bot_username_or_id) -> dict:
    # Bot peer
    try:
        bot_peer = await app.resolve_peer(bot_username_or_id)
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
                rank="Reaksiya",
            )
        )
        return {"ok": True, "method": "full", "error": None}

    except RightForbidden:
        pass  # 2-urinishga o'tadi

    except FreshChangeAdminsForbidden:
        return {"ok": False, "method": "failed", "error": "Yangi login — 24 soat kutish kerak"}

    except FloodWait as e:
        wait = e.value + DELAY_ON_FLOOD
        logger.warning("FloodWait %ss in promote_bot", wait)
        await asyncio.sleep(wait)
        return await promote_bot(app, channel_peer, bot_username_or_id)

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
                rank="Reaksiya",
            )
        )
        return {"ok": True, "method": "minimal", "error": None}

    except RightForbidden:
        return {"ok": False, "method": "failed", "error": "Kanal egasi admin qo'shishni taqiqlagan yoki huquq yetarli emas"}

    except FreshChangeAdminsForbidden:
        return {"ok": False, "method": "failed", "error": "Yangi login — 24 soat kutish kerak"}

    except FloodWait as e:
        wait = e.value + DELAY_ON_FLOOD
        logger.warning("FloodWait %ss in promote_bot", wait)
        await asyncio.sleep(wait)
        return await promote_bot(app, channel_peer, bot_username_or_id)

    except RPCError as e:
        return {"ok": False, "method": "failed", "error": str(e)}

async def auto_promote_workers_for_channel(channel_db_id: int) -> dict:
    """
    Kanalga barcha aktiv workerlarni qo'shib, admin qiladi.
    """
    async with get_session() as db_session:
        setting = await db_session.get(AppSetting, "auto_admin_session")
        if not setting or not setting.value or "session" not in setting.value:
            return {"ok": False, "error": "Avto-Admin sessiyasi topilmadi. Avval login_admin.py ni ishga tushiring."}
        
        session_string = setting.value["session"]
        
        channel = await db_session.get(Channel, channel_db_id)
        if not channel:
            return {"ok": False, "error": "Kanal bazadan topilmadi."}
            
        telegram_channel_id = channel.channel_id
        
        # Barcha aktiv workerlarni olamiz
        workers = (await db_session.execute(
            select(Worker).where(Worker.is_active.is_(True))
        )).scalars().all()
        
        if not workers:
            return {"ok": False, "error": "Aktiv worker botlar yo'q."}

    app = Client(
        name="reaction_manager_temp",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    
    ok_count = 0
    fail_count = 0
    errors = []
    
    try:
        await app.connect()
        
        try:
            channel_peer = await app.resolve_peer(telegram_channel_id)
        except PeerIdInvalid:
            # Agar ID orqali topolmasa, username bilan harakat qilish kerak bo'lishi mumkin
            # Yoki akkaunt bu kanalga a'zo bo'lmagan
            await app.disconnect()
            return {"ok": False, "error": "Auto-admin akkaunti bu kanalga a'zo emas yoki topolmadi."}
        except Exception as e:
            await app.disconnect()
            return {"ok": False, "error": f"Kanalni aniqlashda xato: {e}"}

        for i, worker in enumerate(workers):
            target_bot = worker.username if worker.username else worker.token.split(":")[0]
            
            result = await promote_bot(app, channel_peer, target_bot)
            
            async with get_session() as db_session:
                # Update ChannelWorker status
                cw = (await db_session.execute(
                    select(ChannelWorker)
                    .where(ChannelWorker.channel_id == channel_db_id)
                    .where(ChannelWorker.worker_id == worker.id)
                )).scalar_one_or_none()
                
                if not cw:
                    cw = ChannelWorker(channel_id=channel_db_id, worker_id=worker.id)
                    db_session.add(cw)
                
                if result["ok"]:
                    cw.is_admin = True
                    ok_count += 1
                else:
                    cw.is_admin = False
                    fail_count += 1
                    error_msg = f"@{target_bot}: {result['error']}"
                    if error_msg not in errors:
                        errors.append(error_msg)
                        
            if "24 soat" in str(result.get("error", "")):
                break
                
            if i < len(workers) - 1:
                await asyncio.sleep(DELAY_BETWEEN_BOTS)
                
        await app.disconnect()
        
    except Exception as e:
        logger.error("Auto promote error: %s", e, exc_info=True)
        try:
            await app.disconnect()
        except:
            pass
        return {"ok": False, "error": f"Tizim xatosi: {e}"}
        
    return {
        "ok": True,
        "total": len(workers),
        "ok_count": ok_count,
        "fail_count": fail_count,
        "errors": errors[:5] # faqat 5 ta xatoni qaytaramiz
    }
