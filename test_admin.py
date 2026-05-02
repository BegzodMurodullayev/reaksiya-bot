import asyncio
from database import get_session
from sqlalchemy import text

async def main():
    async with get_session() as s:
        await s.execute(text('UPDATE channel_workers SET is_admin = TRUE'))
        await s.commit()
        print('Updated all workers to is_admin=True')

if __name__ == "__main__":
    asyncio.run(main())
