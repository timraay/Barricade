import inspect
import aiohttp
from functools import wraps
from typing import AsyncGenerator, Sequence

from barricade import schemas
from barricade.db import session_factory
from barricade.enums import IntegrationType
from barricade.exceptions import (
    IntegrationBanError, IntegrationCommandError, IntegrationDisabledError, IntegrationFailureError, NotFoundError,
    AlreadyBannedError, IntegrationValidationError
)
from barricade.integrations.custom.models import (
    BanPlayersRequestConfigPayload, BanPlayersRequestPayload, ClientRequestType, NewReportRequestPayload,
    NewReportRequestPayloadPlayer, UnbanPlayersRequestConfigPayload, UnbanPlayersRequestPayload
)
from barricade.integrations.custom.websocket import CustomWebsocket
from barricade.integrations.integration import Integration, IntegrationMetaData, is_enabled

def is_websocket_enabled(func):
    @wraps(func)
    def decorated(integration: 'CustomIntegration', *args, **kwargs):
        # Define the condition
        async def check():
            if not integration.ws.is_started():
                await integration.disable()
                raise IntegrationDisabledError("Integration %r is disabled. Enable before retrying." % integration)

        # Return an asyncgenerator if that's what we're decorating
        if inspect.isasyncgenfunction(func):
            async def inner_gen():
                await check()
                async for v in func(integration, *args, **kwargs):
                    yield v
            return inner_gen()
        else:
            async def inner_coro():
                await check()
                return await func(integration, *args, **kwargs)
            return inner_coro()

    return decorated

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
        self.ws = CustomWebsocket.from_integration(self)

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
        self.ws.address = self.get_ws_url()
        self.ws.token = self.config.api_key
        if self.ws.is_started():
            self.ws.update_connection()

    @is_enabled
    @is_websocket_enabled
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

    async def validate(self, community: schemas.Community) -> set[str]:
        if community.id != self.config.community_id:
            raise IntegrationValidationError("Communities do not match")
        
        return set()

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
            except IntegrationFailureError:
                raise
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
            except IntegrationFailureError:
                raise
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

    @is_websocket_enabled
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
                yield str(player_id), str(ban_id)
            
            if not partial_retry:
                raise

            # Retry for failed player IDs
            missing_player_ids = {k: v for k, v in player_ids.items() if k not in successful_ids}
            async for (player_id, ban_id) in self.add_multiple_bans(missing_player_ids, partial_retry=False):
                yield player_id, ban_id
        else:
            assert response is not None
            for player_id, ban_id in response["ban_ids"].items():
                yield str(player_id), str(ban_id)

    @is_websocket_enabled
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
                yield str(ban_id)
            
            if not partial_retry:
                raise

            # Retry for failed ban IDs
            missing_ban_ids = list(set(ban_ids) - set(successful_ids))
            async for ban_id in self.remove_multiple_bans(missing_ban_ids, partial_retry=False):
                yield ban_id
        else:
            assert response is not None
            for ban_id in response["ban_ids"]:
                yield str(ban_id)

    async def add_ban(self, player_id: str, reason: str | None = None):
        _, ban_id = await anext(self.add_multiple_bans({player_id: reason}))
        return ban_id

    async def remove_ban(self, ban_id: str):
        return await anext(self.remove_multiple_bans([ban_id]))
