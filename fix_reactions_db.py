"""
Bazadagi noto'g'ri saqlangan reaksiyalarni tuzatish.
Ba'zan channel.reactions = ["❤👍🔥👏🎉🤩👌❤🔥💯⚡🏆"] kabi bitta katta
string bo'lib qolishi mumkin. Bu script ularni to'g'ri alohida emojilarga ajratadi.
"""
import asyncio
import sys
sys.path.insert(0, ".")

from database import get_session
from models import Channel
from sqlalchemy import select
from bot_registry import parse_reactions_text, DEFAULT_REACTIONS


def is_custom_emoji_id(value: str) -> bool:
    stripped = value.strip()
    return stripped.isdigit() and len(stripped) > 10


def sanitize(raw_list: list[str] | None) -> list[str] | None:
    if not raw_list:
        return None
    result = []
    changed = False
    for item in raw_list:
        item = str(item).strip()
        if not item:
            continue
        if is_custom_emoji_id(item) or len(item) <= 8:
            result.append(item)
        else:
            # Uzun string - parse qilish kerak
            parsed = parse_reactions_text(item)
            if parsed:
                result.extend(parsed)
                changed = True
            else:
                result.append(item)
    return result if result else None


async def main():
    async with get_session() as session:
        channels = (await session.execute(select(Channel))).scalars().all()
        fixed = 0
        for ch in channels:
            if not ch.reactions:
                continue
            original = ch.reactions[:]
            sanitized = sanitize(ch.reactions)
            if sanitized != original:
                print(f"[FIX] {ch.title} ({ch.channel_id})")
                print(f"  Oldin: {original}")
                print(f"  Keyin: {sanitized}")
                ch.reactions = sanitized
                fixed += 1
        print(f"\nJami {fixed} ta kanal tuzatildi.")


if __name__ == "__main__":
    asyncio.run(main())
