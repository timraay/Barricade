import asyncio
import itertools
import json
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID
import pydantic

from barricade.exceptions import IntegrationCommandError
from barricade.forwarding import send_optional_player_alert_to_community
from barricade.integrations.battlemetrics.models import Packet, ClientRequestType, ServerRequestType
from barricade.integrations.battlemetrics.utils import find_player_id_in_attributes
from barricade.integrations.websocket import Websocket, WebsocketRequestException

if TYPE_CHECKING:
    from barricade.integrations.battlemetrics.integration import BattlemetricsIntegration

class BattlemetricsWebsocket(Websocket):
    def __init__(self, integration: 'BattlemetricsIntegration'):
        super().__init__(
            address=integration.get_ws_url(),
            token=integration.config.api_key,
            logger=integration.logger,
        )
        self.integration = integration
        self._waiters: dict[UUID, asyncio.Future[Any]] = {}
        self._counter = itertools.count()
    
    async def setup_hook(self):
        await self.execute(ClientRequestType.auth, payload=self.token, is_sensitive=True)
        self.logger.info("Authorized Battlemetrics websocket")

        server_ids = await self.integration.get_server_ids_from_org()
        await self.execute(ClientRequestType.join, payload=[
            f"server:updates:{server_id}"
            for server_id in server_ids
        ])

    async def handle_message(self, message: str | bytes):
        content = json.loads(message)
        try:
            packet = Packet.model_validate(content)
        except pydantic.ValidationError:
            self.logger.error("Received malformed websocket data: %s", content)
            return

        if packet.is_response():
            await self.handle_response(packet)
        else:
            await self.handle_request(packet)

    async def handle_request(self, request: Packet):
        self.logger.debug(
            "Handling websocket request #%s %s %s",
            request.i, request.t, request.p
        )

        try:        
            match request.t:
                case ServerRequestType.ACTIVITY:
                    await self.handle_activity(request.p) # type: ignore
                case ServerRequestType.SERVER_UPDATE:
                    await self.handle_server_update(request.p) # type: ignore
                case _:
                    self.logger.warning("No implementation for request %s", request.t)
                    return

        except Exception:
            self.logger.exception("Unexpected error while handling %r", request)

    async def handle_response(self, response: Packet):
        self.logger.info("Handling websocket response #%s", response.i)
        waiter = self._waiters.get(response.i)

        # Make sure response is being awaited
        if not waiter:
            self.logger.warning("Discarding response since it is not being awaited: %r", response)
            return
        
        # Make sure waiter is still available
        if waiter.done():
            self.logger.warning("Discarding response since waiter is already marked done: %r", response)
            return
        
        # Set response
        if response.t == ServerRequestType.error:
            waiter.set_exception(WebsocketRequestException(response.p.get("detail", response.p))) # type: ignore
        else:
            waiter.set_result(response.p)

    async def execute(self, request_type: ClientRequestType, payload: Optional[dict | list | str] = None, is_sensitive: bool = False) -> Any:
        # First make sure websocket is connected
        ws = await self.wait_until_connected(2)

        # Send request
        request = Packet(t=request_type, p=payload)
        request_dump = request.model_dump_json(exclude_none=True)
        await ws.send(request_dump)
        
        # Allocate response waiter
        fut = asyncio.Future()
        self._waiters[request.i] = fut

        self.logger.info(
            "Sent websocket request #%s %s %s",
            request.i, request.t.name, "[********]" if is_sensitive else request.p
        )

        try:
            try:
                # Wait for and return response
                return await asyncio.wait_for(fut, timeout=10)
            except asyncio.TimeoutError:
                self.logger.warning((
                    "Websocket did not respond in time to request, retransmitting and"
                    " waiting another 5 seconds: %r"
                ), request)

                ws = await self.wait_until_connected(2)
                await ws.send(request_dump)

                try:
                    return await asyncio.wait_for(fut, timeout=5)
                except asyncio.TimeoutError:
                    self.logger.error("Websocket did not respond in time to request: %r", request)
                    raise
        except IntegrationCommandError as e:
            self.logger.error("Websocket returned error \"%s\" for request: %r", e, request)
            raise
        finally:
            # Remove waiter
            if request.i in self._waiters:
                del self._waiters[request.i]

    async def handle_activity(self, payload: dict):
        # Currently unused; does not provide SteamID or Team17 directly
        pass

    async def handle_server_update(self, payload: dict):
        # We're after the information telling us a player joining the
        # server; everything else can be discarded.
        if "players" not in payload:
            return
        
        player_ids = []

        for player in payload["players"]:
            # Check whether the player has joined; we are not interested
            # in updates to existing players.
            if player.get("action") != "add":
                continue

            player_id, _ = find_player_id_in_attributes(player)
            if not player_id:
                continue
            
            player_ids.append(player_id)
        
        if not player_ids:
            return

        await send_optional_player_alert_to_community(
            self.integration.config.community_id, player_ids
        )

        