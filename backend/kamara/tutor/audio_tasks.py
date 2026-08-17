from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types
from starlette.websockets import WebSocketState

from connection.channels import AUDIO_INPUT_CHANNEL, AUDIO_OUTPUT_CHANNEL
from connection.connect_manager import manager
from kamara.tutor.audio_settings import SEND_SAMPLE_RATE
from kamara.tutor.live_session_runtime import LiveSessionRuntime

logger = logging.getLogger("KamaraLogger")


async def listen_to_frontend_audio(
    websocket: WebSocket,
    student_id: str,
    runtime: LiveSessionRuntime,
    session_id: str | None = None,
) -> None:
    await websocket.accept()
    await manager.connect(student_id, websocket, channel=AUDIO_INPUT_CHANNEL)
    logger.info("Attached microphone websocket for student %s | session_id=%s", student_id, session_id)

    try:
        while True:
            frame = await websocket.receive()
            raw_audio = frame.get("bytes")

            if isinstance(raw_audio, (bytes, bytearray)) and raw_audio:
                await runtime.enqueue_audio_input(bytes(raw_audio))
                logger.info("Queued raw mic audio for %s | bytes=%s", student_id, len(raw_audio))
                continue

            text_frame = frame.get("text")
            if isinstance(text_frame, str) and text_frame.strip() == "audio_stream_end":
                await runtime.enqueue_audio_input(None)
                logger.info("Received mic stream end for %s", student_id)

    except WebSocketDisconnect:
        logger.info("Student %s disconnected from microphone websocket.", student_id)
    finally:
        await runtime.enqueue_audio_input(None)
        await manager.disconnect(student_id, websocket, channel=AUDIO_INPUT_CHANNEL)


async def send_frontend_audio_to_ai(
    session,
    student_id: str,
    runtime: LiveSessionRuntime,
) -> None:
    while True:
        audio_chunk = await runtime.audio_queue_mic.get()

        if audio_chunk is None:
            try:
                await session.send_realtime_input(audio_stream_end=True)
            except Exception:
                pass
            logger.info("Forwarded audio stream end marker for %s", student_id)
            return

        if not isinstance(audio_chunk, (bytes, bytearray)) or not audio_chunk:
            continue

        try:
            await session.send_realtime_input(
                audio=types.Blob(
                    data=bytes(audio_chunk),
                    mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                )
            )
            logger.info("Forwarded mic audio to Gemini for %s | bytes=%s", student_id, len(audio_chunk))
        except Exception as exc:
            logger.error("Failed forwarding mic audio for %s: %s", student_id, str(exc), exc_info=True)


async def ai_audio_to_frontend(
    websocket: WebSocket,
    student_id: str,
    runtime: LiveSessionRuntime,
    session_id: str | None = None,
) -> None:
    await websocket.accept()
    await manager.connect(student_id, websocket, channel=AUDIO_OUTPUT_CHANNEL)
    logger.info("Attached Gemini playback websocket for student %s | session_id=%s", student_id, session_id)

    try:
        while True:
            audio_chunk = await runtime.audio_queue_output.get()

            if audio_chunk is None:
                return

            if isinstance(audio_chunk, dict):
                if audio_chunk.get("type") == "audio_stream_end":
                    return
                continue

            if not isinstance(audio_chunk, (bytes, bytearray)) or not audio_chunk:
                continue

            if websocket.client_state != WebSocketState.CONNECTED:
                return

            await websocket.send_bytes(bytes(audio_chunk))
            logger.info("Forwarded Gemini audio to frontend for %s | bytes=%s", student_id, len(audio_chunk))

    except WebSocketDisconnect:
        logger.info("Student %s disconnected from Gemini playback websocket.", student_id)
    finally:
        await manager.disconnect(student_id, websocket, channel=AUDIO_OUTPUT_CHANNEL)
