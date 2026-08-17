import base64
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.auth import verify_student_token
from connection.connect_manager import manager
from connection.channels import AUDIO_INPUT_CHANNEL, AUDIO_OUTPUT_CHANNEL, CANVAS_INPUT_CHANNEL, CANVAS_OUTPUT_CHANNEL
from kamara.tutor.live_session_runtime import drop_live_session_runtime, get_live_session_runtime
from kamara.tutor.tasks.canvas_input import forward_frontend_canvas_to_ai
from kamara.tutor.tasks.canvas_output import forward_ai_canvas_to_frontend
from kamara.tutor.tasks.audio_input import forward_frontend_audio_to_ai
from kamara.tutor.tasks.audio_output import forward_ai_audio_to_frontend
from prompts.tutor_prompt import tutor_system_instruction
from .database import fetch_complete_note


logger = logging.getLogger("KamaraLogger")

socket_router = APIRouter(tags=["Real-Time Vision & Voice Streaming Engine"])


async def _prepare_session_context(token: str, session_id: str | None):
    current_user = await verify_student_token(authorization=f"Bearer {token}")
    student_id = current_user.id

    ctx = await fetch_complete_note(session_id=session_id, student_id=str(student_id))
    if not ctx:
        raise ValueError(f"Curated notes data unavailable for session {session_id}")

    system_prompt = await tutor_system_instruction(ctx)
    runtime = await get_live_session_runtime(str(student_id), session_id, system_prompt)
    return str(student_id), ctx, runtime


async def _parse_json_payload(raw_text: str) -> dict | None:
        try:
            payload = json.loads(raw_text)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None


async def _handle_audio_frame(runtime, frame: dict):
    if "bytes" in frame and frame["bytes"]:
        raw_data = frame["bytes"]
        logger.info("Received microphone websocket binary frame | bytes=%s", len(raw_data))

        if raw_data.startswith(b'{"') or b'"type"' in raw_data:
            payload = await _parse_json_payload(raw_data.decode("utf-8", errors="ignore"))
            if payload:
                if payload.get("type") == "audio_stream_end":
                    logger.info("Received audio stream end event on microphone channel")
                    await runtime.enqueue({"channel": "audio", "type": "audio_stream_end"})
                    return

                inner_bytes_data = payload.get("bytes")
                if isinstance(inner_bytes_data, str):
                    raw_data = base64.b64decode(inner_bytes_data)
                elif inner_bytes_data:
                    raw_data = bytes(inner_bytes_data)
                else:
                    return

        await runtime.enqueue({"channel": "audio", "type": "audio_frame", "data": raw_data})
        logger.info("Queued microphone frame for Gemini | bytes=%s", len(raw_data))
        return

    if "text" in frame and frame["text"]:
        payload = await _parse_json_payload(frame["text"])
        if payload and payload.get("type") == "audio_stream_end":
            logger.info("Received audio stream end text event on microphone channel")
            await runtime.enqueue({"channel": "audio", "type": "audio_stream_end"})


async def _handle_canvas_frame(runtime, frame: dict):
    if "bytes" in frame and frame["bytes"]:
        raw_data = frame["bytes"]
        if raw_data.startswith(b'{"') or b'"type"' in raw_data:
            payload = await _parse_json_payload(raw_data.decode("utf-8", errors="ignore"))
            if payload:
                await runtime.enqueue(
                    {
                        "channel": "canvas",
                        "type": payload.get("type") or "canvas_snapshot_vision",
                        "data": payload.get("data"),
                        "image": payload.get("image"),
                    }
                )
        return

    if "text" in frame and frame["text"]:
        payload = await _parse_json_payload(frame["text"])
        if payload:
            await runtime.enqueue(
                {
                    "channel": "canvas",
                    "type": payload.get("type") or "canvas_snapshot_vision",
                    "data": payload.get("data"),
                    "image": payload.get("image"),
                }
            )


async def _handle_control_frame(runtime, frame: dict):
    if "text" not in frame or not frame["text"]:
        return

    payload = await _parse_json_payload(frame["text"])
    if not payload:
        return

    logger.info("Received control frame | type=%s", payload.get("type"))

    await runtime.enqueue(
        {
            "channel": "control",
            "type": payload.get("type"),
            "tool_call_id": payload.get("tool_call_id"),
            "success": payload.get("success"),
            "message": payload.get("message"),
            "detail": payload.get("detail"),
            "action": payload.get("action"),
        }
    )


async def _run_channel_socket(websocket: WebSocket, token: str, session_id: str | None, channel: str):
    await websocket.accept()
    logger.info("WebSocket accepted for %s channel | session_id=%s", channel, session_id)

    student_id = None
    runtime = None

    try:
        student_id, ctx, runtime = await _prepare_session_context(token, session_id)
        await manager.connect(student_id, websocket, channel=channel)
        logger.info("Attached %s websocket for student %s on topic %s", channel, student_id, ctx["note_title"])

        while True:
            frame = await websocket.receive()

            if channel == "canvas":
                await _handle_canvas_frame(runtime, frame)
            elif channel == "control":
                await _handle_control_frame(runtime, frame)

    except WebSocketDisconnect:
        if student_id:
            logger.info("Student %s disconnected from %s channel.", student_id, channel)
    except ValueError as exc:
        logger.error("WebSocket setup aborted for %s channel: %s", channel, str(exc))
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    except Exception as err:
        logger.error("Exception thrown inside %s channel loop: %s", channel, str(err), exc_info=True)
    finally:
        if student_id:
            await manager.disconnect(student_id, websocket, channel=channel)
            await drop_live_session_runtime(student_id, session_id)
            logger.info("Cleaned up %s websocket for user: %s", channel, student_id)


async def _run_audio_input_socket(websocket: WebSocket, token: str, session_id: str | None):
    await websocket.accept()
    logger.info("WebSocket accepted for microphone input channel | session_id=%s", session_id)

    student_id = None
    runtime = None

    try:
        student_id, ctx, runtime = await _prepare_session_context(token, session_id)
        await manager.connect(student_id, websocket, channel=AUDIO_INPUT_CHANNEL)
        logger.info("Attached microphone websocket for student %s on topic %s", student_id, ctx["note_title"])

        while True:
            frame = await websocket.receive()
            await _handle_audio_frame(runtime, frame)

    except WebSocketDisconnect:
        if student_id:
            logger.info("Student %s disconnected from microphone input channel.", student_id)
    except ValueError as exc:
        logger.error("WebSocket setup aborted for microphone input channel: %s", str(exc))
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    except Exception as err:
        logger.error("Exception thrown inside microphone input loop: %s", str(err), exc_info=True)
    finally:
        if student_id:
            await manager.disconnect(student_id, websocket, channel=AUDIO_INPUT_CHANNEL)
            await drop_live_session_runtime(student_id, session_id)
            logger.info("Cleaned up microphone websocket for user: %s", student_id)


async def _run_audio_output_socket(websocket: WebSocket, token: str, session_id: str | None):
    await websocket.accept()
    logger.info("WebSocket accepted for Gemini playback channel | session_id=%s", session_id)

    student_id = None
    runtime = None

    try:
        student_id, ctx, runtime = await _prepare_session_context(token, session_id)
        await manager.connect(student_id, websocket, channel=AUDIO_OUTPUT_CHANNEL)
        logger.info("Attached Gemini playback websocket for student %s on topic %s", student_id, ctx["note_title"])

        while True:
            frame = await websocket.receive()
            if frame.get("type") == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        if student_id:
            logger.info("Student %s disconnected from Gemini playback channel.", student_id)
    except ValueError as exc:
        logger.error("WebSocket setup aborted for Gemini playback channel: %s", str(exc))
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    except Exception as err:
        logger.error("Exception thrown inside Gemini playback loop: %s", str(err), exc_info=True)
    finally:
        if student_id:
            await manager.disconnect(student_id, websocket, channel=AUDIO_OUTPUT_CHANNEL)
            await drop_live_session_runtime(student_id, session_id)
            logger.info("Cleaned up Gemini playback websocket for user: %s", student_id)


@socket_router.websocket("/ws/api/v1/live")
@socket_router.websocket("/ws/api/v1/live/audio")
@socket_router.websocket("/ws/api/v1/live/audio/in")
async def live_audio_input_stream(
    websocket: WebSocket,
    token: str = Query(...),
    session_id: str | None = Query(None),
):
    student_id = None
    try:
        student_id, _, runtime = await _prepare_session_context(token, session_id)
        await forward_frontend_audio_to_ai(websocket, token, session_id, runtime, student_id)
    finally:
        if student_id:
            await drop_live_session_runtime(student_id, session_id)


@socket_router.websocket("/ws/api/v1/live/audio/out")
async def live_audio_output_stream(
    websocket: WebSocket,
    token: str = Query(...),
    session_id: str | None = Query(None),
):
    student_id = None
    try:
        student_id, _, runtime = await _prepare_session_context(token, session_id)
        await forward_ai_audio_to_frontend(websocket, token, session_id, runtime, student_id)
    finally:
        if student_id:
            await drop_live_session_runtime(student_id, session_id)


@socket_router.websocket("/ws/api/v1/live/canvas")
@socket_router.websocket("/ws/api/v1/live/canvas/in")
async def live_canvas_input_stream(
    websocket: WebSocket,
    token: str = Query(...),
    session_id: str | None = Query(None),
):
    student_id = None
    try:
        student_id, _, runtime = await _prepare_session_context(token, session_id)
        await forward_frontend_canvas_to_ai(websocket, token, session_id, runtime, student_id)
    finally:
        if student_id:
            await drop_live_session_runtime(student_id, session_id)


@socket_router.websocket("/ws/api/v1/live/canvas/out")
async def live_canvas_output_stream(
    websocket: WebSocket,
    token: str = Query(...),
    session_id: str | None = Query(None),
):
    student_id = None
    try:
        student_id, _, runtime = await _prepare_session_context(token, session_id)
        await forward_ai_canvas_to_frontend(websocket, token, session_id, runtime, student_id)
    finally:
        if student_id:
            await drop_live_session_runtime(student_id, session_id)


@socket_router.websocket("/ws/api/v1/live/control")
async def live_control_stream(
    websocket: WebSocket,
    token: str = Query(...),
    session_id: str | None = Query(None),
):
    await _run_channel_socket(websocket, token, session_id, "control")
