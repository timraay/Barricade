import aiohttp
from cachetools import TTLCache
import discord
from typing import AsyncGenerator, Sequence

from barricade import schemas
from barricade.crud.communities import get_community_by_id
from barricade.crud.reports import is_player_reported
from barricade.crud.responses import get_pending_responses, get_reports_for_player_with_no_community_response
from barricade.db import session_factory
from barricade.discord.communities import get_forward_channel
from barricade.discord.reports import get_alert_embed
from barricade.enums import IntegrationType
from barricade.exceptions import (
    IntegrationBanError, IntegrationCommandError, NotFoundError,
    AlreadyBannedError, IntegrationValidationError
)
from barricade.forwarding import send_or_edit_report_management_message, send_or_edit_report_review_message
from barricade.integrations.integration import Integration, IntegrationMetaData, is_enabled
from barricade.integrations.websocket import (
    BanPlayersRequestConfigPayload, BanPlayersRequestPayload, ClientRequestType, NewReportRequestPayload, NewReportRequestPayloadPlayer, UnbanPlayersRequestConfigPayload,
    UnbanPlayersRequestPayload, Websocket, WebsocketRequestException, WebsocketRequestHandler
)

class IntegrationRequestHandler(WebsocketRequestHandler):
    __is_player_reported = TTLCache[str, bool](maxsize=9999, ttl=60*10)

    def __init__(self, ws: Websocket, integration: 'Integration'):
        super().__init__(ws)
        self.integration = integration
    
    async def scan_players(self, payload: dict | None) -> dict | None:
        reported_player_ids: list[str] = []

        player_ids: list[str] | None = payload.get("player_ids") if payload else None
        if not player_ids:
            raise WebsocketRequestException("Missing player_ids")

        # Go over all players to check whether they have been reported
        async with session_factory() as db:
            for player_id in player_ids:
                # First look for a cached response, otherwise fetch from DB
                cache_hit = self.__is_player_reported.get(player_id)
                if cache_hit is not None:
                    if cache_hit:
                        reported_player_ids.append(player_id)
                else:
                    is_reported = await is_player_reported(db, player_id)
                    self.__is_player_reported[player_id] = is_reported
                    if is_reported:
                        reported_player_ids.append(player_id)
        
            if reported_player_ids:
                # There are one or more players that have reports
                community_id = self.integration.config.community_id
                
                db_community = await get_community_by_id(db, community_id)
                community = schemas.CommunityRef.model_validate(db_community)

                channel = get_forward_channel(community)
                if not channel:
                    # We have nowhere to send the alert, so we just ignore
                    return

                for player_id in reported_player_ids:
                    # For each player, get all reports that this community has not yet responded to
                    db_reports = await get_reports_for_player_with_no_community_response(
                        db, player_id, community_id, community.reasons_filter
                    )

                    messages: list[discord.Message] = []
                    sorted_reports = sorted(
                        (schemas.ReportWithToken.model_validate(db_report) for db_report in db_reports),
                        key=lambda x: x.created_at
                    )

                    # Locate all the messages, resending as necessary, and updating them with the most
                    # up-to-date details.
                    for report in sorted_reports:
                        if report.token.community_id == community_id:
                            message = await send_or_edit_report_management_message(report)
                        else:
                            db_community = await get_community_by_id(db, community.id)
                            responses = await get_pending_responses(db, community, report.players)
                            message = await send_or_edit_report_review_message(report, responses, community)
                        
                        if message:
                            # Remember the message
                            messages.append(message)

                    if not messages:
                        # No messages were located, so we don't have any reports to point the user at.
                        continue

                    # Get the most recent PlayerReport for the most up-to-date name
                    player = next(
                        pr for pr in sorted_reports[-1].players
                        if pr.player_id == player_id
                    )

                    if community.admin_role_id:
                        content = f"<@&{community.admin_role_id}> a potentially dangerous player has joined your server!"
                    else:
                        content = "A potentially dangerous player has joined your server!"

                    reports_urls = list(zip(sorted_reports, (message.jump_url for message in messages)))
                    embed = get_alert_embed(
                        reports_urls=list(reversed(reports_urls)),
                        player=player
                    )

                    await channel.send(
                        content=content,
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(roles=True),
                    )

class CustomIntegration(Integration):
    meta = IntegrationMetaData(
        name="Custom",
        config_cls=schemas.CustomIntegrationConfig,
        type=IntegrationType.CUSTOM,
        emoji="💭",
    )

    def __init__(self, config: schemas.CustomIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CustomIntegrationConfigParams
        self.ws = Websocket(
            address=self.get_ws_url(),
            token=config.api_key,
            request_handler_factory=lambda ws: IntegrationRequestHandler(ws, self),
            logger=self.logger,
        )

    def get_api_url(self):
        return self.config.api_url

    def get_ws_url(self):
        return self.config.api_url

    # --- Extended parent methods

    def start_connection(self):
        self.ws.start()
    
    def stop_connection(self):
        self.ws.stop()
    
    def update_connection(self):
        self.ws.address = self.config.api_url
        self.ws.token = self.config.api_key
        self.ws.update_connection()

    @is_enabled
    async def on_report_create(self, report: schemas.ReportWithToken):
        await self.ws.execute(
            ClientRequestType.NEW_REPORT,
            NewReportRequestPayload(
                created_at=report.created_at,
                body=report.body,
                reasons=report.reasons_bitflag.to_list(report.reasons_custom),
                attachment_urls=[],
                players=[
                    NewReportRequestPayloadPlayer(
                        player_id=player.player_id,
                        player_name=player.player_name,
                        bm_rcon_url=player.player.bm_rcon_url,
                    )
                    for player in report.players
                ]
            ).model_dump()
        )

    # --- Abstract method implementations

    async def get_instance_name(self) -> str:
        return "Custom"
    
    def get_instance_url(self) -> str:
        return self.config.api_url

    async def validate(self, community: schemas.Community):
        if community.id != self.config.community_id:
            raise IntegrationValidationError("Communities do not match")

    @is_enabled
    async def ban_player(self, response: schemas.ResponseWithToken):
        async with session_factory.begin() as db:
            player_id = response.player_report.player_id
            self.logger.info("%r: Banning player %s", self, player_id)
            db_ban = await self.get_ban(db, player_id)
            if db_ban is not None:
                raise AlreadyBannedError(player_id, "Player is already banned")

            try:
                remote_id = await self.add_ban(
                    player_id=player_id,
                    reason=self.get_ban_reason(response)
                )
            except Exception as e:
                raise IntegrationBanError(player_id, "Failed to ban player") from e

            await self.set_ban_id(db, player_id, remote_id)

    @is_enabled
    async def unban_player(self, player_id: str):
        self.logger.info("%r: Unbanning player %s", self, player_id)
        async with session_factory.begin() as db:
            db_ban = await self.get_ban(db, player_id)
            if db_ban is None:
                raise NotFoundError("Ban does not exist")

            await db.delete(db_ban)
            await db.flush()

            try:
                await self.remove_ban(db_ban.remote_id)
            except Exception as e:
                raise IntegrationBanError(player_id, "Failed to unban player") from e
    
    @is_enabled
    async def bulk_ban_players(self, responses: Sequence[schemas.ResponseWithToken]):
        self.logger.info(
            "%r: Bulk banning players %s",
            self, [response.player_report.player_id for response in responses]
        )
        ban_ids: list[tuple[str, str]] = []
        try:
            async for ban in self.add_multiple_bans(
                player_ids={
                    response.player_report.player_id: self.get_ban_reason(response)
                    for response in responses
                }
            ):
                ban_ids.append(ban)

        finally:
            if ban_ids:
                async with session_factory.begin() as db:
                    await self.set_multiple_ban_ids(db, *ban_ids)

    @is_enabled
    async def bulk_unban_players(self, player_ids: Sequence[str]):
        self.logger.info("%r: Bulk unbanning players %s", self, player_ids)
        async with session_factory() as db:
            remote_ids: dict[str, str] = {}
            for player_id in player_ids:
                ban = await self.get_ban(db, player_id)
                if ban:
                    remote_ids[ban.remote_id] = player_id

        successful_player_ids: list[str] = []
        try:
            async for ban_id in self.remove_multiple_bans(ban_ids=list(remote_ids.keys())):
                successful_player_ids.append(remote_ids[ban_id])
        finally:
            if successful_player_ids:
                async with session_factory.begin() as db:
                    await self.discard_multiple_ban_ids(db, successful_player_ids)

    @is_enabled
    async def synchronize(self):
        pass

    # --- Websocket API wrappers

    async def _make_request(self, method: str, endpoint: str, data: dict | None = None) -> dict:
        """Make an API request.

        Parameters
        ----------
        method : str
            One of GET, POST, PATCH, DELETE
        endpoint : str
            The resource to query, gets prepended with the API root URL.
            For example, `/login` queries `http://<api>:<port>/api/login`.
        data : dict, optional
            Additional data to include in the request, by default None

        Returns
        -------
        dict
            The response from the server

        Raises
        ------
        Exception
            Doom and gloom
        """
        url = self.get_api_url() + endpoint
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            if method in {"POST", "PATCH"}:
                kwargs = {"json": data}
            else:
                kwargs = {"params": data}

            async with session.request(method=method, url=url, **kwargs) as r: # type: ignore
                r.raise_for_status()
                content_type = r.headers.get('content-type', '')

                if 'json' in content_type:
                    response = await r.json()
                # elif "text/html" in content_type:
                #     response = (await r.content.read()).decode()
                else:
                    raise Exception(f"Unsupported content type: {content_type}")

        return response

    async def add_multiple_bans(self, player_ids: dict[str, str | None], *, partial_retry: bool = True) -> AsyncGenerator[tuple[str, str], None]:
        try:
            response = await self.ws.execute(ClientRequestType.BAN_PLAYERS, BanPlayersRequestPayload(
                player_ids=player_ids,
                config=BanPlayersRequestConfigPayload(
                    banlist_id=self.config.banlist_id,
                    reason="Banned via shared HLL Barricade report.",
                )
            ).model_dump())
        except IntegrationCommandError as e:
            if e.response.get("error") != "Could not ban all players":
                raise

            successful_ids = e.response["ban_ids"]
            for player_id, ban_id in successful_ids.items():
                yield (player_id, ban_id)
            
            if not partial_retry:
                raise

            # Retry for failed player IDs
            missing_player_ids = {k: v for k, v in player_ids.items() if k not in successful_ids}
            async for (player_id, ban_id) in self.add_multiple_bans(missing_player_ids, partial_retry=False):
                yield player_id, ban_id
        else:
            assert response is not None
            for player_id, ban_id in response["ban_ids"].items():
                yield player_id, ban_id

    async def remove_multiple_bans(self, ban_ids: Sequence[str], *, partial_retry: bool = True) -> AsyncGenerator[str, None]:
        try:
            response = await self.ws.execute(ClientRequestType.UNBAN_PLAYERS, UnbanPlayersRequestPayload(
                ban_ids=list(ban_ids),
                config=UnbanPlayersRequestConfigPayload(
                    banlist_id=self.config.banlist_id,
                )
            ).model_dump())
        except IntegrationCommandError as e:
            if e.response.get("error") != "Could not unban all players":
                raise

            successful_ids = e.response["ban_ids"]
            for ban_id in successful_ids:
                yield ban_id
            
            if not partial_retry:
                raise

            # Retry for failed ban IDs
            missing_ban_ids = list(set(ban_ids) - set(successful_ids))
            async for ban_id in self.remove_multiple_bans(missing_ban_ids, partial_retry=False):
                yield ban_id
        else:
            assert response is not None
            for ban_id in response["ban_ids"]:
                yield ban_id

    async def add_ban(self, player_id: str, reason: str | None = None):
        _, ban_id = await anext(self.add_multiple_bans({player_id: reason}))
        return ban_id

    async def remove_ban(self, ban_id: str):
        return await anext(self.remove_multiple_bans([ban_id]))
