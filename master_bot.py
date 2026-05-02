import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot_registry import (
    DEFAULT_REACTIONS,
    get_default_reactions,
    inspect_target_chat,
    parse_reactions_text,
    register_worker,
    set_default_reactions,
    sync_workers_for_channel,
)
from bulk_import_service import process_bulk_import_from_file
from config import settings
from database import get_session
from handlers.bulk_add_handler import bulk_router
from models import BulkImportLog, Channel, ChannelWorker, TaskLog, Worker
from worker_manager import schedule_reactions

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
CREATED_TOKENS_FILE = BASE_DIR / "created_tokens.txt"

dp = Dispatcher()
dp.include_router(bulk_router)


class AddChatStates(StatesGroup):
    waiting_for_reference = State()
    waiting_for_reactions = State()


class AddBotStates(StatesGroup):
    waiting_for_token = State()


class EditStates(StatesGroup):
    waiting_for_channel_reactions = State()
    waiting_for_default_reactions = State()


def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika")],
            [
                KeyboardButton(text="📢 Kanallar/Guruhlar"),
                KeyboardButton(text="🤖 Botlar"),
            ],
            [
                KeyboardButton(text="⚙️ Default reaksiyalar"),
                KeyboardButton(text="📥 Bulk import"),
            ],
        ],
        resize_keyboard=True,
    )


def channels_menu_kb(channels: list[Channel]):
    keyboard = [
        [InlineKeyboardButton(text="Yangi kanal/guruh ulash", callback_data="add_channel")],
    ]
    for channel in channels:
        prefix = "ON" if channel.is_active else "OFF"
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix} | {channel.title[:35]}",
                    callback_data=f"channel:{channel.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def channel_detail_kb(channel_id: int, is_active: bool):
    toggle_text = "Pauza qilish" if is_active else "Aktiv qilish"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Reaksiyani o'zgartirish", callback_data=f"edit_reactions:{channel_id}"),
                InlineKeyboardButton(text="Worker sync", callback_data=f"sync_channel:{channel_id}"),
            ],
            [InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_channel:{channel_id}")],
            [InlineKeyboardButton(text="Orqaga", callback_data="channels")],
        ]
    )


def bots_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Bot qo'shish")],
            [KeyboardButton(text="📄 created_tokens.txt import")],
            [KeyboardButton(text="🔙 Asosiy menyu")],
        ],
        resize_keyboard=True,
    )


def settings_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Default reaksiyani o'zgartirish")],
            [KeyboardButton(text="🔙 Asosiy menyu")],
        ],
        resize_keyboard=True,
    )


async def check_owner(message_or_callback) -> bool:
    user = getattr(message_or_callback, "from_user", None)
    if user is None or user.id not in settings.admin_ids_list:
        if isinstance(message_or_callback, types.Message):
            await message_or_callback.answer("Sizda ruxsat yo'q.")
        else:
            await message_or_callback.answer("Sizda ruxsat yo'q.", show_alert=True)
        return False
    return True


async def answer_or_edit(event: types.Message | CallbackQuery, text: str, reply_markup=None):
    try:
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(text, reply_markup=reply_markup)
        else:
            await event.answer(text, reply_markup=reply_markup)
    except Exception:
        if isinstance(event, CallbackQuery):
            await event.message.answer(text, reply_markup=reply_markup)
        else:
            raise


async def render_main_menu(event: types.Message | CallbackQuery, state: FSMContext | None = None):
    if state:
        await state.clear()
    await answer_or_edit(
        event,
        "Boshqaruv paneli.\nKerakli bo'limni tanlang.",
        reply_markup=main_menu_kb(),
    )


async def render_channel_detail(event: types.Message | CallbackQuery, channel_id: int):
    async with get_session() as session:
        channel = await session.get(
            Channel,
            channel_id,
            options=[selectinload(Channel.workers)],
        )
        if channel is None:
            await answer_or_edit(event, "Chat topilmadi.", reply_markup=channels_menu_kb([]))
            return

        active_workers = [worker for worker in channel.workers if worker.is_active]
        text = (
            f"Chat: {channel.title}\n"
            f"Telegram ID: {channel.channel_id}\n"
            f"Holati: {'aktiv' if channel.is_active else 'pauza'}\n"
            f"Aktiv workerlar: {len(active_workers)}\n"
            f"Reaksiyalar: {' '.join(channel.reactions or DEFAULT_REACTIONS)}"
        )
        await answer_or_edit(
            event,
            text,
            reply_markup=channel_detail_kb(channel.id, channel.is_active),
        )


async def _resolve_chat_reference(bot: Bot, message: types.Message):
    forward_origin = getattr(message, "forward_origin", None)
    if forward_origin:
        origin_chat = getattr(forward_origin, "chat", None) or getattr(forward_origin, "sender_chat", None)
        if origin_chat:
            return {
                "chat_id": origin_chat.id,
                "title": getattr(origin_chat, "title", None) or str(origin_chat.id),
            }

    forward_chat = getattr(message, "forward_from_chat", None)
    if forward_chat:
        return {
            "chat_id": forward_chat.id,
            "title": forward_chat.title or str(forward_chat.id),
        }

    text = (message.text or "").strip()
    if not text:
        return None

    if text.startswith("@"):
        chat_ref = text
    else:
        try:
            chat_ref = int(text)
        except ValueError:
            return None

    try:
        chat = await bot.get_chat(chat_ref)
    except Exception as exc:
        logger.warning("Could not resolve chat reference %s: %s", text, exc)
        return None

    return {
        "chat_id": chat.id,
        "title": getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat.id),
    }


async def _track_chat(bot: Bot, state: FSMContext, message: types.Message, reactions: list[str]):
    data = await state.get_data()
    telegram_chat_id = data["chat_id"]
    chat_info = await inspect_target_chat(bot, telegram_chat_id)
    if not chat_info["ok"]:
        await message.answer(f"Chat ulanmaydi: {chat_info['reason']}", reply_markup=main_menu_kb())
        await state.clear()
        return

    title = chat_info["title"]
    warning = chat_info.get("warning")

    async with get_session() as session:
        result = await session.execute(select(Channel).where(Channel.channel_id == telegram_chat_id))
        channel = result.scalar_one_or_none()

        if channel is None:
            channel = Channel(
                channel_id=telegram_chat_id,
                title=title,
                reactions=reactions,
                is_active=True,
            )
            session.add(channel)
            await session.flush()
            channel_db_id = channel.id
            created = True
        else:
            channel.title = title
            channel.reactions = reactions
            channel.is_active = True
            channel_db_id = channel.id
            created = False

    sync_result = await sync_workers_for_channel(bot, channel_db_id)
    pending = sync_result.get("pending_usernames", [])
    
    # Auto-admin tekshiruvi olib tashlandi

    text = (
        f"{'Yangi chat ulandi.' if created else 'Chat yangilandi.'}\n"
        f"Nomi: {title}\n"
        f"ID: {telegram_chat_id}\n"
        f"Reaksiyalar: {' '.join(reactions)}\n"
        f"Yangi linklar: {sync_result.get('linked_count', 0)}\n"
        f"Admin bo'lgan workerlar: {sync_result.get('promoted_count', 0)}"
    )
    if pending:
        text += "\n\nQuyidagi workerlar hali chatga qo'shilmagan yoki admin qilib bo'lmadi:"
        text += "\n" + "\n".join(pending[:20])
    if sync_result.get("warning"):
        text += f"\n\nEslatma: {sync_result['warning']}"
    elif warning:
        text += f"\n\nEslatma: {warning}"
        


    await message.answer(text, reply_markup=main_menu_kb())
    await state.clear()


async def _schedule_for_tracked_chat(telegram_chat_id: int, message_id: int):
    async with get_session() as session:
        result = await session.execute(select(Channel).where(Channel.channel_id == telegram_chat_id))
        channel = result.scalar_one_or_none()
        if channel is None or not channel.is_active:
            return
        await schedule_reactions(channel.id, telegram_chat_id, message_id)


@dp.message(F.text == "🔙 Asosiy menyu")
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if not await check_owner(message):
        return
    await render_main_menu(message, state)


@dp.message(Command("sync"))
async def cmd_sync_all(message: types.Message, bot: Bot):
    if not await check_owner(message):
        return
        
    await message.answer("Barcha kanallar uchun botlar holati yangilanmoqda. Iltimos, kuting...")
    
    async with get_session() as session:
        channels = (
            await session.execute(select(Channel).where(Channel.is_active.is_(True)))
        ).scalars().all()
        
    if not channels:
        await message.answer("Aktiv kanallar topilmadi.")
        return
        
    total_promoted = 0
    total_linked = 0
    errors = []
    
    for channel in channels:
        try:
            result = await sync_workers_for_channel(bot, channel.id)
            total_promoted += result.get("promoted_count", 0)
            total_linked += result.get("linked_count", 0)
            if result.get("warning"):
                errors.append(f"{channel.title}: {result['warning']}")
        except Exception as e:
            errors.append(f"{channel.title}: xatolik - {e}")
            
    text = (
        f"✅ Sinxronizatsiya tugadi!\n"
        f"Yangi linklar: {total_linked}\n"
        f"Admin bo'lgan workerlar: {total_promoted}\n"
    )
    if errors:
        text += "\nMuammoli kanallar:\n" + "\n".join(errors[:10])
        
    await message.answer(text)


@dp.message(Command("force_sync"))
async def cmd_force_sync(message: types.Message):
    if not await check_owner(message):
        return
        
    await message.answer("Barcha botlar bazada majburiy ravishda 'Admin' qilib belgilanmoqda...")
    
    async with get_session() as session:
        channels = (await session.execute(select(Channel).where(Channel.is_active.is_(True)))).scalars().all()
        workers = (await session.execute(select(Worker).where(Worker.is_active.is_(True)))).scalars().all()
        
        count = 0
        for channel in channels:
            for worker in workers:
                cw = (await session.execute(
                    select(ChannelWorker).where(
                        ChannelWorker.channel_id == channel.id,
                        ChannelWorker.worker_id == worker.id
                    )
                )).scalar_one_or_none()
                
                if not cw:
                    cw = ChannelWorker(channel_id=channel.id, worker_id=worker.id)
                    session.add(cw)
                
                if not cw.is_admin:
                    cw.is_admin = True
                    count += 1
                    
        await session.commit()
        
    await message.answer(f"✅ Barcha aktiv {count} ta bot ulanishi majburiy admin qilib belgilandi! Ular endi shu zaxoti reaksiya bosishda davom etadi.")


@dp.message(F.text == "📊 Statistika")
@dp.message(Command("statistika"))
async def show_stats(event: types.Message):
    if not await check_owner(event):
        return

    async with get_session() as session:
        total_channels = await session.scalar(select(func.count(Channel.id)))
        total_workers = await session.scalar(select(func.count(Worker.id)))
        active_workers = await session.scalar(
            select(func.count(Worker.id)).where(Worker.is_active.is_(True))
        )
        success_logs = await session.scalar(
            select(func.count(TaskLog.id)).where(TaskLog.status == "success")
        )
        failed_logs = await session.scalar(
            select(func.count(TaskLog.id)).where(TaskLog.status.in_(["failed", "invalid_token"]))
        )
        bulk_stmt = select(BulkImportLog).order_by(BulkImportLog.id.desc()).limit(3)
        bulk_logs = (await session.execute(bulk_stmt)).scalars().all()

    text = (
        "Umumiy statistika\n\n"
        f"Chatlar: {total_channels or 0}\n"
        f"Jami botlar: {total_workers or 0}\n"
        f"Aktiv botlar: {active_workers or 0}\n"
        f"Muvaffaqiyatli reaksiyalar: {success_logs or 0}\n"
        f"Xatoliklar: {failed_logs or 0}"
    )
    if bulk_logs:
        text += "\n\nOxirgi importlar:"
        for log in bulk_logs:
            text += (
                f"\n- #{log.id}: jami {log.total_tokens}, yangi {log.success_count}, "
                f"takror {log.duplicate_count}, xato {log.failed_count}"
            )

    await answer_or_edit(event, text, reply_markup=main_menu_kb())


@dp.message(F.text == "📢 Kanallar/Guruhlar")
@dp.message(Command("kanallar"))
async def show_channels(event: types.Message):
    if not await check_owner(event):
        return

    async with get_session() as session:
        channels = (
            await session.execute(select(Channel).order_by(Channel.is_active.desc(), Channel.id.desc()))
        ).scalars().all()

    if not channels:
        text = (
            "Hali chat ulanmagan.\n\n"
            "Yangi chat ulash uchun tugmani bosing va kanal/guruhdan xabar forward qiling "
            "yoki chat ID/@username yuboring."
        )
    else:
        text = "Ulangan chatlar:\n\n"
        for index, channel in enumerate(channels, start=1):
            status = "aktiv" if channel.is_active else "pauza"
            text += f"{index}. {channel.title} | {status} | {channel.channel_id}\n"

    await answer_or_edit(event, text, reply_markup=channels_menu_kb(channels))


@dp.callback_query(F.data == "add_channel")
async def add_channel_start(callback: CallbackQuery, state: FSMContext):
    if not await check_owner(callback):
        return
    await state.clear()
    await callback.message.edit_text(
        "Kanal yoki guruh ulash.\n\n"
        "1. Kanal/guruhdan bitta post/xabarni forward qiling\n"
        "2. Yoki chat ID yuboring: -100...\n"
        "3. Yoki public @username yuboring\n\n"
        "Muhim: private chatlar uchun main bot o'sha chatda allaqachon admin bo'lishi kerak."
    )
    await state.set_state(AddChatStates.waiting_for_reference)


@dp.message(AddChatStates.waiting_for_reference)
async def process_channel_reference(message: types.Message, state: FSMContext, bot: Bot):
    if not await check_owner(message):
        return

    chat_info = await _resolve_chat_reference(bot, message)
    if chat_info is None:
        await message.answer(
            "Chatni aniqlab bo'lmadi. Postni forward qiling yoki to'g'ri chat ID/@username yuboring."
        )
        return

    await state.update_data(chat_id=chat_info["chat_id"], title=chat_info["title"])
    async with get_session() as session:
        default_reactions = await get_default_reactions(session)

    await message.answer(
        f"Chat topildi: {chat_info['title']} ({chat_info['chat_id']})\n\n"
        "Endi reaksiyalarni yuboring.\n"
        "Masalan: ❤ 👍 🔥 👏 🎉 🤩 👌 ❤‍🔥 💯 ⚡ 🏆\n"
        "Bo'sh qoldirmoqchi bo'lsangiz `default` deb yuboring."
        f"\n\nHozirgi default: {' '.join(default_reactions)}"
    )
    await state.set_state(AddChatStates.waiting_for_reactions)


@dp.message(AddChatStates.waiting_for_reactions, F.text)
async def process_channel_reactions(message: types.Message, state: FSMContext, bot: Bot):
    if not await check_owner(message):
        return

    text = (message.text or "").strip()
    if text.lower() == "default":
        async with get_session() as session:
            reactions = await get_default_reactions(session)
    else:
        reactions = parse_reactions_text(text) or DEFAULT_REACTIONS.copy()

    await _track_chat(bot, state, message, reactions)


@dp.callback_query(F.data.startswith("channel:"))
async def channel_detail(callback: CallbackQuery):
    if not await check_owner(callback):
        return
    channel_id = int(callback.data.split(":", 1)[1])
    await render_channel_detail(callback, channel_id)


@dp.callback_query(F.data.startswith("edit_reactions:"))
async def edit_channel_reactions_start(callback: CallbackQuery, state: FSMContext):
    if not await check_owner(callback):
        return
    channel_id = int(callback.data.split(":", 1)[1])
    await state.clear()
    await state.update_data(channel_id=channel_id)
    await callback.message.edit_text(
        "Yangi reaksiyalarni yuboring.\nMasalan: ❤ 👍 🔥 👏 🎉"
    )
    await state.set_state(EditStates.waiting_for_channel_reactions)


@dp.message(EditStates.waiting_for_channel_reactions, F.text)
async def edit_channel_reactions_finish(message: types.Message, state: FSMContext):
    if not await check_owner(message):
        return

    data = await state.get_data()
    reactions = parse_reactions_text(message.text) or DEFAULT_REACTIONS.copy()
    async with get_session() as session:
        channel = await session.get(Channel, data["channel_id"])
        if channel is None:
            await message.answer("Chat topilmadi.", reply_markup=main_menu_kb())
            await state.clear()
            return
        channel.reactions = reactions
        channel_id = channel.id

    await state.clear()
    await message.answer(
        f"Reaksiyalar yangilandi: {' '.join(reactions)}",
        reply_markup=main_menu_kb(),
    )
    await render_channel_detail(message, channel_id)


@dp.callback_query(F.data.startswith("toggle_channel:"))
async def toggle_channel(callback: CallbackQuery):
    if not await check_owner(callback):
        return
    channel_id = int(callback.data.split(":", 1)[1])

    async with get_session() as session:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            await callback.answer("Chat topilmadi.", show_alert=True)
            return
        channel.is_active = not channel.is_active

    await render_channel_detail(callback, channel_id)


@dp.callback_query(F.data.startswith("sync_channel:"))
async def sync_channel_workers_handler(callback: CallbackQuery, bot: Bot):
    if not await check_owner(callback):
        return
    channel_id = int(callback.data.split(":", 1)[1])
    await callback.answer("Workerlar sync qilinmoqda...")
    result = await sync_workers_for_channel(bot, channel_id)
    text = (
        f"Sync tugadi.\nYangi linklar: {result.get('linked_count', 0)}\n"
        f"Admin bo'lgan workerlar: {result.get('promoted_count', 0)}"
    )
    if result.get("warning"):
        text += f"\n\nEslatma: {result['warning']}"
    await callback.message.answer(text)
    await render_channel_detail(callback, channel_id)


@dp.message(F.text == "🤖 Botlar")
@dp.message(Command("botlar"))
async def show_bots(event: types.Message):
    if not await check_owner(event):
        return

    async with get_session() as session:
        workers = (await session.execute(select(Worker).order_by(Worker.id.desc()))).scalars().all()

    if not workers:
        text = "Hali worker bot qo'shilmagan."
    else:
        text = "Worker botlar:\n\n"
        for index, worker in enumerate(workers, start=1):
            status = "aktiv" if worker.is_active else "off"
            text += f"{index}. @{worker.username or 'unknown'} | {status}\n"

    await answer_or_edit(event, text, reply_markup=bots_menu_kb())


@dp.message(F.text == "➕ Bot qo'shish")
async def add_bot_start(message: types.Message, state: FSMContext):
    if not await check_owner(message):
        return
    await state.clear()
    await message.answer("Worker bot tokenini yuboring.", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AddBotStates.waiting_for_token)


@dp.message(AddBotStates.waiting_for_token, F.text)
async def process_bot_token(message: types.Message, state: FSMContext, bot: Bot):
    if not await check_owner(message):
        return

    result = await register_worker(bot, message.text.strip())
    status = result.get("status")

    if status == "invalid":
        await message.answer(f"Token xato: {result.get('reason', 'noma`lum xato')}")
        return

    action_text = {
        "added": "Yangi bot qo'shildi.",
        "reactivated": "Bot qayta aktiv qilindi.",
        "existing": "Bu bot allaqachon bazada bor edi.",
    }.get(status, "Bot saqlandi.")

    text = (
        f"{action_text}\n"
        f"Username: @{result.get('username') or 'unknown'}\n"
        f"Yangi linklar: {result.get('linked_count', 0)}\n"
        f"Admin bo'lgan chatlar: {result.get('promoted_count', 0)}"
    )
    pending = result.get("pending_titles") or []
    skipped = result.get("skipped_titles") or []
    if pending:
        text += "\n\nQuyidagi chatlarda worker hali member emas yoki admin qilib bo'lmadi:"
        text += "\n" + "\n".join(pending[:20])
    if skipped:
        text += "\n\nQuyidagi chatlarda avtomatik admin qilish cheklangan:"
        text += "\n" + "\n".join(skipped[:20])

    await state.clear()
    await message.answer(text, reply_markup=main_menu_kb())


@dp.message(F.text == "📄 created_tokens.txt import")
async def import_created_tokens(message: types.Message, bot: Bot):
    if not await check_owner(message):
        return

    if not CREATED_TOKENS_FILE.exists():
        await message.answer("created_tokens.txt topilmadi.")
        return

    status_msg = await message.answer("created_tokens.txt dan import boshlandi...", reply_markup=main_menu_kb())
    asyncio.create_task(
        process_bulk_import_from_file(
            master_bot=bot,
            owner_id=callback.from_user.id,
            file_path=CREATED_TOKENS_FILE,
            status_message=status_msg,
        )
    )


@dp.message(F.text == "⚙️ Default reaksiyalar")
@dp.message(Command("sozlamalar"))
async def show_settings(event: types.Message):
    if not await check_owner(event):
        return

    async with get_session() as session:
        reactions = await get_default_reactions(session)

    text = (
        "Default reaksiyalar\n\n"
        f"Hozirgi set: {' '.join(reactions)}\n\n"
        "Yangi ulangan chatlar shu set bilan ishlaydi."
    )
    await answer_or_edit(event, text, reply_markup=settings_menu_kb())


@dp.message(F.text == "📝 Default reaksiyani o'zgartirish")
async def edit_default_reactions_start(message: types.Message, state: FSMContext):
    if not await check_owner(message):
        return
    await state.clear()
    await message.answer(
        "Yangi default reaksiyalarni yuboring.\nMasalan: ❤ 👍 🔥 👏 🎉 🤩",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(EditStates.waiting_for_default_reactions)


@dp.message(EditStates.waiting_for_default_reactions, F.text)
async def edit_default_reactions_finish(message: types.Message, state: FSMContext):
    if not await check_owner(message):
        return
    reactions = parse_reactions_text(message.text) or DEFAULT_REACTIONS.copy()
    await set_default_reactions(reactions)
    await state.clear()
    await message.answer(
        f"Default reaksiyalar saqlandi: {' '.join(reactions)}",
        reply_markup=main_menu_kb(),
    )


@dp.channel_post()
async def handle_channel_post(message: types.Message):
    await _schedule_for_tracked_chat(message.chat.id, message.message_id)


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: types.Message):
    if message.text and message.text.startswith("/"):
        return
    if message.from_user and message.from_user.is_bot:
        return
    await _schedule_for_tracked_chat(message.chat.id, message.message_id)


@dp.message(F.chat.type == "private")
async def handle_private_manual_reaction(message: types.Message, bot: Bot):
    if not await check_owner(message):
        return
        
    chat_id = None
    message_id = None
    
    # 1. Forward tekshiruvi
    if message.forward_origin and message.forward_origin.type == "channel":
        chat_id = message.forward_origin.chat.id
        message_id = message.forward_origin.message_id
    elif message.forward_from_chat and message.forward_from_chat.type == "channel":
        chat_id = message.forward_from_chat.id
        message_id = getattr(message, "forward_from_message_id", None)
    
    # 2. Matn ichidan link izlash (https://t.me/kanal_nomi/123 yoki t.me/c/123/456)
    if not chat_id and message.text:
        import re
        match = re.search(r"t\.me/(c/)?([^/]+)/(\d+)", message.text)
        if match:
            is_private_c = match.group(1)
            chat_ref = match.group(2)
            message_id = int(match.group(3))
            
            if is_private_c:
                chat_id = int(f"-100{chat_ref}")
            else:
                try:
                    chat = await bot.get_chat(f"@{chat_ref}")
                    chat_id = chat.id
                except Exception:
                    await message.answer("Bunday kanal topilmadi. Kanal @username si to'g'riligiga ishonch hosil qiling.")
                    return

    if chat_id and message_id:
        async with get_session() as session:
            channel = (await session.execute(
                select(Channel).where(Channel.channel_id == chat_id)
            )).scalar_one_or_none()
            
            if not channel or not channel.is_active:
                await message.answer("Bu kanal bazada yo'q yoki aktiv emas. Oldin kanalni ulab qo'ying.")
                return
                
            await schedule_reactions(channel.id, chat_id, message_id)
            await message.answer(f"✅ Ushbu xabarga (ID: {message_id}) reaksiyalar rejalashtirildi!")
