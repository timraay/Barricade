import asyncio
from barricade.bans import revoke_dangling_bans
from barricade.crud.bans import get_player_bans_without_responses
from barricade.db import session_factory

async def main() -> None:
    async with session_factory.begin() as db:
        db_bans = await get_player_bans_without_responses(db)
        print(f"Found {len(db_bans)} dangling bans.")
        input("Are you sure you want to revoke all? Press Enter to confirm...")

        result = await revoke_dangling_bans(db)
        print(f"Revoked {result} dangling bans.")
        input("Press Enter to exit...")

if __name__ == "__main__":
    asyncio.run(main())
