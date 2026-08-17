from __future__ import annotations

import asyncio
import logging

from connection.connect_manager import manager
from .session_resume_store import save_session_resumption_handle
from .toolset.tools import tools_handler

logger = logging.getLogger("KamaraLogger")


async def _async_trigger_greeting(session, student_id: str) -> None:
    try:
        await asyncio.sleep(0.1)
        await session.send(
            input=(
                "The student has successfully connected to the classroom. "
                "Please speak immediately and give them a warm, short greeting to begin the session."
            ),
            end_of_turn=True,
        )
        logger.info("Successfully injected asynchronous initialization greeting packet upstream.")
    except Exception as exc:
        logger.warning("Bypassed non-fatal startup greeting injection: %s", str(exc))


async def _route_audio_output(runtime, student_id: str, response) -> None:
    server_content = getattr(response, "server_content", None)
    model_turn = getattr(server_content, "model_turn", None) if server_content else None
    if not runtime or not model_turn:
        return

    for part in getattr(model_turn, "parts", []) or []:
        inline_data = getattr(part, "inline_data", None)
        audio_data = getattr(inline_data, "data", None) if inline_data else None
        if not audio_data:
            continue

        audio_bytes = bytes(audio_data)
        await runtime.enqueue_audio_output(audio_bytes)
        logger.info("Queued Gemini audio for %s | bytes=%s", student_id, len(audio_bytes))


async def _route_interrupt(runtime, student_id: str) -> None:
    logger.info("Student %s interrupted the AI tutor.", student_id)

    if runtime and hasattr(runtime, "clear_audio_output_queue"):
        await runtime.clear_audio_output_queue()
    elif runtime and hasattr(runtime, "audio_queue_output"):
        while not runtime.audio_queue_output.empty():
            try:
                runtime.audio_queue_output.get_nowait()
            except asyncio.QueueEmpty:
                break

    await manager.send_json_message(
        {"type": "interrupted", "action": "stop_audio_playback"},
        student_id,
        channel="control",
    )


async def receive_response_from_ai(session, student_id: str, session_id: str | None = None, runtime=None):
    """
    Receives Gemini Live events and routes them to the correct downstream handler.
    """
    try:
        logger.info("AI Response streaming task fully activated for user: %s", student_id)
        asyncio.create_task(_async_trigger_greeting(session, student_id))

        async for response in session.receive():
            try:
                session_resumption_update = getattr(response, "session_resumption_update", None)
                if session_resumption_update:
                    handle = getattr(session_resumption_update, "handle", None)
                    if isinstance(handle, str) and handle.strip():
                        await save_session_resumption_handle(student_id, session_id, handle)

                server_content = getattr(response, "server_content", None)
                if server_content:
                    if getattr(server_content, "interrupted", None):
                        await _route_interrupt(runtime, student_id)
                    else:
                        await _route_audio_output(runtime, student_id, response)

                if getattr(response, "tool_call", None):
                    await tools_handler(
                        student_id=student_id,
                        session=session,
                        tool_call=response.tool_call,
                        runtime=runtime,
                    )
            except Exception as item_err:
                logger.error(
                    "Failed to process individual Gemini stream frame for student %s: %s",
                    student_id,
                    str(item_err),
                    exc_info=True,
                )
                continue

    except asyncio.CancelledError:
        logger.info("Gemini stream receiver task safely cancelled for student %s.", student_id)
    except Exception as exc:
        logger.error("Fatal crash in AI response listener loop for student %s: %s", student_id, str(exc), exc_info=True)
