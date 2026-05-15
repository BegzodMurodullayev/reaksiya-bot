"""
Bazadagi noto'g'ri saqlangan reaksiyalarni tuzatish.
channel.reactions = ["❤👍🔥👏🎉🤩👌❤🔥💯⚡🏆"] (bitta katta string)
=> ["❤", "👍", "🔥", "👏", "🎉", "🤩", "👌", "❤‍🔥", "💯", "⚡", "🏆"]
"""
import asyncio
import sys
import io
# Windows terminal encoding muammosini hal qilish
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, ".")

from database import get_session
from models import Channel
from sqlalchemy import select
from bot_registry import _split_emoji_string, DEFAULT_REACTIONS


def is_custom_emoji_id(value: str) -> bool:
    stripped = value.strip()
    return stripped.isdigit() and len(stripped) > 10


def sanitize(raw_list: list[str] | None) -> tuple[list[str], bool]:
    """Returns (sanitized_list, was_changed)"""
    if not raw_list:
        return DEFAULT_REACTIONS.copy(), True

    result = []
    changed = False
    for item in raw_list:
        item = str(item).strip()
        if not item:
            continue
        if is_custom_emoji_id(item):
            result.append(item)
        elif len(item) <= 5:
            result.append(item)
        else:
            # Uzun string — bo'sh joysiz emoji ketma-ketligi
            parts = _split_emoji_string(item)
            if parts and parts != [item]:
                print(f"  Splitting: '{item}' → {parts}")
                result.extend(parts)
                changed = True
            else:
                result.append(item)

    return (result if result else DEFAULT_REACTIONS.copy()), changed


async def main():
    async with get_session() as session:
        channels = (await session.execute(select(Channel))).scalars().all()
        fixed = 0
        for ch in channels:
            sanitized, changed = sanitize(ch.reactions)
            if changed:
                print(f"\n[FIX] {ch.title} ({ch.channel_id})")
                print(f"  Oldin ({len(ch.reactions or [])} ta): {ch.reactions}")
                print(f"  Keyin ({len(sanitized)} ta): {sanitized}")
                ch.reactions = sanitized
                fixed += 1

        if fixed:
            print(f"\n✅ Jami {fixed} ta kanal tuzatildi va saqlandi.")
        else:
            print("\n✅ Barcha kanallar to'g'ri formatda. Tuzatish kerak emas.")


if __name__ == "__main__":
    asyncio.run(main())
