# app/connection/connect_manager.py
from collections import defaultdict
import asyncio
import logging
from fastapi import WebSocket
from starlette.websockets import WebSocketState


logger = logging.getLogger("KamaraLogger")

class ConnectionManager:
    def __init__(self):
        # Maps student_id -> channel -> active WebSockets.
        self.active_connections: dict[str, dict[str, set[WebSocket]]] = defaultdict(lambda: defaultdict(set))

    async def connect(self, student_id: str, websocket: WebSocket, channel: str = "default"):
       # await websocket.accept()
        self.active_connections[student_id][channel].add(websocket)
        logger.info("Student websocket attached for UUID: %s | channel=%s", student_id, channel)

    def _disconnect_now(self, student_id: str, websocket: WebSocket | None = None, channel: str = "default"):
        channels = self.active_connections.get(student_id)
        if not channels:
            return

        sockets = channels.get(channel)
        if not sockets:
            return

        if websocket is not None:
            sockets.discard(websocket)
        else:
            sockets.clear()

        if not sockets:
            channels.pop(channel, None)

        if not channels:
            self.active_connections.pop(student_id, None)
            logger.info("Socket session closed for Student ID: %s", student_id)

    async def disconnect(self, student_id: str, websocket: WebSocket | None = None, channel: str = "default"):
        self._disconnect_now(student_id, websocket, channel=channel)

    def _is_socket_active(self, websocket: WebSocket) -> bool:
        return (
            getattr(websocket, "client_state", None) == WebSocketState.CONNECTED
            and getattr(websocket, "application_state", None) == WebSocketState.CONNECTED
        )

    def _get_active_targets(self, student_id: str, channel: str | None = None) -> list[WebSocket]:
        if channel is None:
            sockets = [
                websocket
                for socket_group in self.active_connections.get(student_id, {}).values()
                for websocket in socket_group
            ]
        else:
            sockets = list(self.active_connections.get(student_id, {}).get(channel, set()))

        active_targets = [websocket for websocket in sockets if self._is_socket_active(websocket)]

        if len(active_targets) != len(sockets):
            for websocket in sockets:
                if websocket not in active_targets:
                    self._disconnect_now(student_id, websocket, channel=channel or "default")

        return active_targets

    def has_any_active_connection(self, student_id: str) -> bool:
        channels = self.active_connections.get(student_id, {})
        return any(len(sockets) > 0 for sockets in channels.values())

    async def send_json_message(self, message: dict, student_id: str, channel: str = "default"):
        """Broadcasts a JSON payload to one logical channel for a student's active connections."""
        active_targets = self._get_active_targets(student_id, channel=channel)
        logger.info(
            "Broadcasting JSON payload to student %s | channel=%s | targets=%s | type=%s",
            student_id,
            channel,
            len(active_targets),
            message.get("type") or message.get("action"),
        )

        async def send_one(websocket: WebSocket):
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error("Failed broadcasting JSON payload to student socket: %s", str(e))
                await self.disconnect(student_id, websocket, channel=channel)

        await asyncio.gather(*(send_one(websocket) for websocket in active_targets), return_exceptions=True)

    async def send_binary_audio(self, audio_bytes: bytes, student_id: str, channel: str = "audio"):
        """Broadcasts raw voice audio bytes to the active sockets for one channel."""
        active_targets = self._get_active_targets(student_id, channel=channel)
        logger.info(
            "Broadcasting binary audio to student %s | channel=%s | targets=%s | bytes=%s",
            student_id,
            channel,
            len(active_targets),
            len(audio_bytes),
        )

        async def send_one(websocket: WebSocket):
            try:
                await websocket.send_bytes(audio_bytes)
            except Exception as e:
                logger.error("Failed broadcasting audio streaming bytes to student socket: %s", str(e))
                await self.disconnect(student_id, websocket, channel=channel)

        await asyncio.gather(*(send_one(websocket) for websocket in active_targets), return_exceptions=True)

    async def send_private_tab_message(self, message: dict, websocket: WebSocket):
        """Sends a JSON message strictly to ONE specific socket instance (e.g., error alert back to sender)."""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error("Failed delivering direct target socket frame payload: %s", str(e))

manager = ConnectionManager()
