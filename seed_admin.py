import asyncio
from app.db.session import async_session_maker
from app.models.user import User
from app.services.security import hash_password
from sqlalchemy import select

async def main():
    async with async_session_maker() as session:
        # Check if admin already exists
        query = select(User).where(User.email == "admin@example.com")
        result = await session.execute(query)
        existing = result.scalars().first()
        
        if existing:
            print("Admin user 'admin@example.com' already exists.")
            return

        admin = User(
            email="admin@example.com",
            hashed_password=hash_password("admin123"),
            role="admin",
            is_active=True
        )
        session.add(admin)
        await session.commit()
        print("Admin user 'admin@example.com' successfully seeded with password 'admin123'.")

if __name__ == "__main__":
    asyncio.run(main())
