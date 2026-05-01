import io
import logging

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot_registry import parse_tokens_blob
from bulk_import_service import process_bulk_import
from config import settings

logger = logging.getLogger(__name__)

bulk_router = Router()


class BulkImportState(StatesGroup):
    waiting_for_text = State()
    waiting_for_file = State()


def bulk_method_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📄 Fayl yuklash (.txt)")],
            [KeyboardButton(text="📝 Matn yuborish")],
            [KeyboardButton(text="🔙 Asosiy menyu")],
        ],
        resize_keyboard=True,
    )


async def _check_owner(event: types.Message | CallbackQuery) -> bool:
    user = getattr(event, "from_user", None)
    if user is None or user.id not in settings.admin_ids_list:
        if isinstance(event, CallbackQuery):
            await event.answer("Sizda ruxsat yo'q.", show_alert=True)
        else:
            await event.answer("Sizda ruxsat yo'q.")
        return False
    return True


@bulk_router.message(F.text == "📥 Bulk import")
@bulk_router.message(Command("bulk_add"))
async def start_bulk_import(event: types.Message, state: FSMContext):
    if not await _check_owner(event):
        return
    text = (
        "Ommaviy bot import.\n\n"
        "Tokenlarni 2 usulda yuborishingiz mumkin:\n"
        "1. .txt fayl ichida\n"
        "2. Xabar ichida har qatorda bittadan\n\n"
        "Faylda `nomi = token` yoki faqat token ko'rinishi ham ishlaydi."
    )
    await event.answer(text, reply_markup=bulk_method_kb())


@bulk_router.message(F.text == "📄 Fayl yuklash (.txt)")
async def ask_for_file(message: types.Message, state: FSMContext):
    if not await _check_owner(message):
        return
    await message.answer(
        ".txt fayl yuboring. Har qatorda bitta token bo'lsin.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(BulkImportState.waiting_for_file)


@bulk_router.message(F.text == "📝 Matn yuborish")
async def ask_for_text(message: types.Message, state: FSMContext):
    if not await _check_owner(message):
        return
    await message.answer(
        "Tokenlarni yuboring. Har qatorda bittadan bo'lsin.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(BulkImportState.waiting_for_text)


@bulk_router.message(BulkImportState.waiting_for_text, F.text)
async def handle_bulk_text(message: types.Message, state: FSMContext, bot: Bot):
    if not await _check_owner(message):
        return
    tokens = parse_tokens_blob(message.text)
    if not tokens:
        await message.answer("Token topilmadi. Qayta yuboring.")
        return

    await state.clear()
    status_msg = await message.answer(f"{len(tokens)} ta token qabul qilindi. Import boshlandi.")
    import asyncio

    asyncio.create_task(process_bulk_import(bot, message.from_user.id, tokens, status_msg))


@bulk_router.message(BulkImportState.waiting_for_file, F.document)
async def handle_bulk_file(message: types.Message, state: FSMContext, bot: Bot):
    if not await _check_owner(message):
        return
    if not message.document.file_name.endswith(".txt"):
        await message.answer("Faqat .txt fayl yuboring.")
        return

    file = await bot.get_file(message.document.file_id)
    file_bytes = io.BytesIO()
    await bot.download_file(file.file_path, destination=file_bytes)

    try:
        text_content = file_bytes.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        await message.answer("Fayl UTF-8 formatda bo'lishi kerak.")
        return

    tokens = parse_tokens_blob(text_content)
    if not tokens:
        await message.answer("Fayl ichida token topilmadi.")
        return

    await state.clear()
    status_msg = await message.answer(f"Fayldan {len(tokens)} ta token o'qildi. Import boshlandi.")
    import asyncio

    asyncio.create_task(process_bulk_import(bot, message.from_user.id, tokens, status_msg))
