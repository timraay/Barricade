import asyncio
import itertools
import json
import logging
import pydantic
from typing import TYPE_CHECKING

from barricade.exceptions import IntegrationCommandError
from barricade.forwarding import send_optional_player_alert_to_community
from barricade.integrations.custom.models import RequestBody, ResponseBody, ClientRequestType, ServerRequestType
from barricade.integrations.websocket import Websocket, WebsocketRequestException

if TYPE_CHECKING:
    from barricade.integrations.custom.integration import CustomIntegration

class CustomWebsocket(Websocket):
    def __init__(
        self,
        address: str,
        token: str | None = None,
        logger: logging.Logger = logging # type: ignore
    ):
        super().__init__(address=address, token=token, logger=logger)
        self._waiters: dict[int, asyncio.Future[dict]] = {}
        self._counter = itertools.count()
        self.integration = None

    @classmethod
    def from_integration(cls, integration: 'CustomIntegration'):
        self = cls(
            address=integration.get_ws_url(),
            token=integration.config.api_key,
            logger=integration.logger,
        )
        self.integration = integration
        return self

    async def handle_message(self, message: str | bytes):
        content = json.loads(message)
        try:
            request = content["request"]
        except KeyError:
            self.logger.error("Received malformed websocket request: %s", content)
            return

        try:
            if request:
                await self.handle_request(RequestBody.model_validate(content))
            else:
                await self.handle_response(ResponseBody.model_validate(content))
        except pydantic.ValidationError:
            self.logger.error("Received malformed Barricade request: %s", content)
            return

    async def handle_request(self, request: RequestBody):
        self.logger.debug(
            "Handling websocket request #%s %s %s",
            request.id, request.request.name, request.payload
        )

        handler = None
        match request.request:
            case ServerRequestType.SCAN_PLAYERS:
                handler = self.scan_players
        
        if handler:
            try:
                ret = await handler(request.payload)
                response = request.response_ok(ret)
            except WebsocketRequestException as e:
                response = request.response_error(str(e))
            except Exception as e:
                self.logger.exception("Unexpected error while handling %r", request)
                response = request.response_error(str(e))
        else:
            self.logger.warning("No handler for websocket request %s", request.request.name)
            response = request.response_error("No such command")

        # Respond to request
        ws = await self.wait_until_connected(timeout=10)
        await ws.send(response.model_dump_json())

    async def handle_response(self, response: ResponseBody):
        self.logger.info("Handling websocket response #%s %s", response.id, response.response)
        waiter = self._waiters.get(response.id)

        # Make sure response is being awaited
        if not waiter:
            self.logger.warning("Discarding response since it is not being awaited: %r", response)
            return
        
        # Make sure waiter is still available
        if waiter.done():
            self.logger.warning("Discarding response since waiter is already marked done: %r", response)
            return
        
        # Set response
        response_body: dict = response.response # type: ignore
        if response.failed:
            waiter.set_exception(
                IntegrationCommandError(
                    response_body,
                    response_body.get("error", ""),
                )
            )
        else:
            waiter.set_result(response_body)

    async def execute(self, request_type: ClientRequestType, payload: dict | None) -> dict | None:
        # First make sure websocket is connected
        ws = await self.wait_until_connected(2)

        # Send request
        request = RequestBody(
            id=next(self._counter),
            request=request_type,
            payload=payload
        )
        request_dump = request.model_dump_json()
        await ws.send(request_dump)
        
        # Allocate response waiter
        fut = asyncio.Future()
        self._waiters[request.id] = fut

        self.logger.info(
            "Sent websocket request #%s %s %s",
            request.id, request.request.name, request.payload
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
            if request.id in self._waiters:
                del self._waiters[request.id]

    async def scan_players(self, payload: dict | None):
        if not self.integration:
            return

        player_ids: list[str] | None = payload.get("player_ids") if payload else None
        if not player_ids:
            raise WebsocketRequestException("Missing player_ids")
        
        await send_optional_player_alert_to_community(
            self.integration.config.community_id, player_ids
        )
