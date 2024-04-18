import asyncio
from sqlalchemy.schema import CreateSchema, DropSchema

from bunker import schemas
from bunker.constants import DISCORD_BOT_TOKEN
from bunker.crud import communities, reports, responses
from bunker.db import session_factory, engine, create_tables
from bunker.discord import bot
from bunker.enums import ReportReasonFlag

async def main():
    await bot.login(DISCORD_BOT_TOKEN)
    asyncio.create_task(bot.connect(reconnect=True))
    await bot.wait_until_ready()

    schema_name = "public"
    async with session_factory() as db:
        input("Are you sure you want to drop all current data?")
        await db.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        await db.execute(CreateSchema(schema_name))
        await db.commit()

    await create_tables()
    async with session_factory() as db:
        c1 = await communities.create_new_community(db, schemas.CommunityCreateParams(
            name="Wolves of War",
            tag="(WTH)",
            contact_url="discord.gg/WTH",
            owner_id=425249228185534485,
            forward_guild_id=695232527123742742,
            forward_channel_id=729998051288285256,
            owner_name="Abu"
        ))
        c2 = await communities.create_new_community(db, schemas.CommunityCreateParams(
            name="Community 2",
            tag="[C2]",
            contact_url="C2 url",
            owner_id=999254478274441277,
            forward_guild_id=None,
            forward_channel_id=None,
            owner_name="C2 owner"
        ))
        c3 = await communities.create_new_community(db, schemas.CommunityCreateParams(
            name="Community 3",
            tag="[C3]",
            contact_url="C3 url",
            owner_id=1018259047947960320,
            forward_guild_id=None,
            forward_channel_id=None,
            owner_name="C3 owner"
        ))

    async with session_factory() as db:
        await communities.create_new_admin(db, schemas.AdminCreateParams(
            discord_id=446731539611648001,
            community_id=c1.id,
            name="Bunkerer"
        ))

        t1 = await reports.create_token(db, schemas.ReportTokenCreateParams(
            community_id=c1.id,
            admin_id=c1.owner_id,
        ))

        t2 = await reports.create_token(db, schemas.ReportTokenCreateParams(
            community_id=c2.id,
            admin_id=c2.owner_id,
        ))

        await reports.create_report(db, schemas.ReportCreateParams(
            body="These guys need to be removed. They are a danger to society.",
            reasons_bitflag=ReportReasonFlag.TEAMKILLING_GRIEFING,
            reasons_custom=None,
            token=t1,
            players=[
                schemas.PlayerReportCreateParams(
                    player_id="11111111111111111",
                    player_name="Player 1",
                    bm_rcon_url=None,
                ),
                schemas.PlayerReportCreateParams(
                    player_id="22222222222222222",
                    player_name="Player 2",
                    bm_rcon_url=None,
                ),
            ]
        ))
        
        await reports.create_report(db, schemas.ReportCreateParams(
            body="Lorem ipsum dolor sit amet, consectetur adipiscing elit. Ut velit ante, vulputate non fringilla cursus, commodo ut risus. Vestibulum id eros cursus orci euismod hendrerit a et urna. Donec vel nisl sed lectus posuere tincidunt. Donec in nisl blandit, facilisis sem molestie, lobortis urna.\nCras egestas feugiat lectus, id ultrices odio luctus eget. In hac habitasse platea dictumst. Suspendisse potenti.",
            reasons_bitflag=ReportReasonFlag.HACKING | ReportReasonFlag.CUSTOM,
            reasons_custom="Ipsum lorem",
            token=t2,
            players=[
                schemas.PlayerReportCreateParams(
                    player_id="76561199023367826",
                    player_name="Abu",
                    bm_rcon_url=None,
                ),
            ]
        ))

    await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())