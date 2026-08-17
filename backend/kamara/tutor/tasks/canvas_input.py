from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from connection.connect_manager import manager
from connection.channels import CANVAS_INPUT_CHANNEL
from kamara.tutor.live_session_runtime import LiveSessionRuntime

logger = logging.getLogger("KamaraLogger")


def _parse_json_payload(raw_text: str) -> dict | None:
    try:
        payload = json.loads(raw_text)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


async def forward_frontend_canvas_to_ai(
    websocket: WebSocket,
    token: str,
    session_id: str | None,
    runtime: LiveSessionRuntime,
    student_id: str,
) -> None:
    await websocket.accept()
    await manager.connect(student_id, websocket, channel=CANVAS_INPUT_CHANNEL)
    logger.info("Attached canvas input websocket for student %s | session_id=%s", student_id, session_id)

    try:
        while True:
            frame = await websocket.receive()

            if "bytes" in frame and frame["bytes"]:
                raw_data = frame["bytes"]
                if raw_data.startswith(b"{") or b'"type"' in raw_data:
                    payload = _parse_json_payload(raw_data.decode("utf-8", errors="ignore"))
                    if payload:
                        await runtime.enqueue_canvas_input(payload)
                        logger.info(
                            "Queued canvas binary payload for Gemini for %s | type=%s",
                            student_id,
                            payload.get("type"),
                        )
                        continue

                await runtime.enqueue_canvas_input({"type": "canvas_snapshot_vision", "image": raw_data})
                logger.info("Queued raw canvas image payload for Gemini for %s | bytes=%s", student_id, len(raw_data))
                continue

            if "text" in frame and frame["text"]:
                payload = _parse_json_payload(frame["text"])
                if payload:
                    await runtime.enqueue_canvas_input(payload)
                    logger.info(
                        "Queued canvas text payload for Gemini for %s | type=%s",
                        student_id,
                        payload.get("type"),
                    )

    except WebSocketDisconnect:
        logger.info("Student %s disconnected from canvas input websocket.", student_id)
    finally:
        await manager.disconnect(student_id, websocket, channel=CANVAS_INPUT_CHANNEL)
