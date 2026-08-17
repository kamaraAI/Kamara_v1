from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from connection.connect_manager import manager
from connection.channels import CANVAS_OUTPUT_CHANNEL
from kamara.tutor.live_session_runtime import LiveSessionRuntime

logger = logging.getLogger("KamaraLogger")


async def forward_ai_canvas_to_frontend(
    websocket: WebSocket,
    token: str,
    session_id: str | None,
    runtime: LiveSessionRuntime,
    student_id: str,
) -> None:
    await websocket.accept()
    await manager.connect(student_id, websocket, channel=CANVAS_OUTPUT_CHANNEL)
    logger.info("Attached canvas output websocket for student %s | session_id=%s", student_id, session_id)

    try:
        while True:
            payload = await runtime.canvas_queue_output.get()

            if payload is None:
                return

            if websocket.client_state != WebSocketState.CONNECTED:
                return

            if not isinstance(payload, dict):
                continue

            await websocket.send_json(payload)
            logger.info(
                "Forwarded Gemini canvas payload to frontend for %s | type=%s",
                student_id,
                payload.get("type"),
            )

    except WebSocketDisconnect:
        logger.info("Student %s disconnected from canvas output websocket.", student_id)
    except asyncio.CancelledError:
        logger.info("Canvas output websocket task cancelled for %s.", student_id)
        raise
    finally:
        await manager.disconnect(student_id, websocket, channel=CANVAS_OUTPUT_CHANNEL)
