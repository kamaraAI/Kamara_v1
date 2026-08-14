import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from connection.connect_manager import manager

from .session_resume_store import save_session_resumption_handle
from .toolset.tools import tools_handler

logger = logging.getLogger("KamaraLogger")


async def _async_trigger_greeting(session, student_id: str):
    """Hidden helper task that wakes up Gemini asynchronously without blocking."""
    try:
        await asyncio.sleep(0.1)
        await session.send(
            input="The student has successfully connected to the classroom. Please speak immediately and give them a warm, short greeting to begin the session.",
            end_of_turn=True,
        )
        logger.info("Successfully injected asynchronous initialization greeting packet upstream.")
    except Exception as exc:
        logger.warning("Bypassed non-fatal startup greeting injection: %s", str(exc))


async def receive_response_from_ai(session, student_id: str, websocket: WebSocket, session_id: str | None = None):
    """
    Receives text, voice, and tool calls from Gemini Live and streams them directly to the browser.
    """
    try:
        logger.info("AI Response streaming task fully activated for user: %s", student_id)
        asyncio.create_task(_async_trigger_greeting(session, student_id))
        pending_tool_tasks: set[asyncio.Task] = set()

        def _track_tool_task(task: asyncio.Task) -> None:
            pending_tool_tasks.add(task)
            task.add_done_callback(pending_tool_tasks.discard)

        async def _handle_tool_call(tool_call):
            try:
                await tools_handler(
                    student_id=student_id,
                    session=session,
                    tool_call=tool_call,
                    websocket=websocket,
                )
            except Exception as exc:
                logger.error(
                    "Async tool handler failed for student %s: %s",
                    student_id,
                    str(exc),
                    exc_info=True,
                )

        async for response in session.receive():
            try:
                logger.info("Received raw response packet from Gemini for %s", student_id)
                logger.info(
                    "Gemini packet summary for %s | has_server_content=%s | has_tool_call=%s | has_session_resumption_update=%s | has_go_away=%s",
                    student_id,
                    bool(getattr(response, "server_content", None)),
                    bool(getattr(response, "tool_call", None)),
                    bool(getattr(response, "session_resumption_update", None)),
                    bool(getattr(response, "go_away", None)),
                )

                session_resumption_update = getattr(response, "session_resumption_update", None)
                if session_resumption_update:
                    handle = getattr(session_resumption_update, "handle", None)
                    if isinstance(handle, str) and handle.strip():
                        await save_session_resumption_handle(student_id, session_id, handle)

                go_away = getattr(response, "go_away", None)
                if go_away:
                    logger.info("Gemini issued GoAway for %s | time_left=%s", student_id, getattr(go_away, "time_left", None))

                if response.server_content:
                    logger.info("Server content frame data metadata present.")
                    logger.info(
                        "Gemini server content flags for %s | turn_complete=%s | interrupted=%s | generation_complete=%s",
                        student_id,
                        getattr(response.server_content, "turn_complete", None),
                        getattr(response.server_content, "interrupted", None),
                        getattr(response.server_content, "generation_complete", None),
                    )
                    output_transcription = getattr(response.server_content, "output_transcription", None)
                    if output_transcription:
                        logger.info(
                            "Gemini output transcription for %s | finished=%s | text=%s",
                            student_id,
                            getattr(output_transcription, "finished", None),
                            getattr(output_transcription, "text", None),
                        )

                    input_transcription = getattr(response.server_content, "input_transcription", None)
                    if input_transcription:
                        logger.info(
                            "Gemini input transcription for %s | finished=%s | text=%s",
                            student_id,
                            getattr(input_transcription, "finished", None),
                            getattr(input_transcription, "text", None),
                        )

                    if response.server_content.model_turn:
                        logger.info("Gemini model turn detected for %s", student_id)

                        parts = list(getattr(response.server_content.model_turn, "parts", []) or [])
                        if not parts:
                            logger.info("Gemini model turn had no parts for %s", student_id)

                        for index, part in enumerate(parts, start=1):
                            inline_data = getattr(part, "inline_data", None)
                            text_part = getattr(part, "text", None)
                            function_call = getattr(part, "function_call", None)

                            if inline_data and getattr(inline_data, "data", None):
                                audio_bytes = bytes(inline_data.data)
                                mime_type = getattr(inline_data, "mime_type", None)

                                logger.info(
                                    "Gemini audio chunk ready for %s | part=%s | bytes=%s | mime_type=%s",
                                    student_id,
                                    index,
                                    len(audio_bytes),
                                    mime_type,
                                )
                                await manager.send_binary_audio(audio_bytes, student_id)
                            elif text_part:
                                logger.info("Gemini text fragment for %s | part=%s | text=%s", student_id, index, text_part)
                            elif function_call:
                                logger.info("Gemini function call fragment for %s | part=%s | call=%s", student_id, index, function_call)
                            else:
                                logger.info("Gemini model turn part %s for %s had no audio/text/function payload.", index, student_id)

                    if response.server_content.interrupted:
                        logger.info("Student %s interrupted the AI tutor.", student_id)
                        await manager.send_json_message(
                            {"type": "interrupted", "action": "stop_audio_playback"},
                            student_id,
                        )

                if response.tool_call:
                    logger.info("Gemini triggered whiteboard tool(s) for student: %s", student_id)
                    _track_tool_task(asyncio.create_task(_handle_tool_call(response.tool_call)))

            except (WebSocketDisconnect, RuntimeError) as socket_dead_err:
                logger.warning("Browser wire connection dropped for %s. Breaking outbound streaming loop.", student_id)
                raise WebSocketDisconnect() from socket_dead_err
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
