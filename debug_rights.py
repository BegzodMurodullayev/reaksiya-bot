import asyncio
from aiogram import Bot
from aiogram import Bot
from config import settings

async def main():
    bot = Bot(token=settings.MASTER_TOKEN)
    chat_id = -1003987667947
    worker_id = 7261453305 # or any bot id, let's just get the bot's own member status first
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        print(f"Master bot status: {member.status}")
    except Exception as e:
        print(f"Master bot member check error: {e}")
        
    try:
        # Check one worker bot
        # Let's say worker 17 is @begzod_reaksiya_17_bot. Let's resolve its ID or just get chat administrators
        admins = await bot.get_chat_administrators(chat_id)
        print("Admins:")
        for admin in admins:
            print(f"- {admin.user.username} (ID: {admin.user.id}) | status: {admin.status}")
    except Exception as e:
        print(f"Get admins error: {e}")

    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
