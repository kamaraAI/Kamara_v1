import asyncio
import base64
import json
import logging
import time

from fastapi import WebSocketDisconnect
from google.genai import types
from websockets.exceptions import ConnectionClosedError

from kamara.tutor.tool_ack import resolve_tool_result

logger = logging.getLogger("KamaraLogger")


async def _forward_audio_frames(student_id: str, session, queue: asyncio.Queue):
    while True:
        audio_data = await queue.get()

        if audio_data is None:
            return

        if isinstance(audio_data, dict) and audio_data.get("type") == "audio_stream_end":
            try:
                await session.send_realtime_input(audio_stream_end=True)
                logger.info("Forwarded audio stream end marker for %s", student_id)
            except Exception as exc:
                logger.exception("Failed forwarding audio stream end for %s: %s", student_id, str(exc))
            continue

        try:
            await session.send_realtime_input(
                audio=types.Blob(
                    data=audio_data,
                    mime_type="audio/pcm;rate=16000",
                )
            )
        except ConnectionClosedError as exc:
            logger.warning("Gemini session closed while forwarding mic for %s: %s", student_id, str(exc))
            return
        except Exception as exc:
            logger.exception("Failed forwarding mic frame to Gemini for %s: %s", student_id, str(exc))


async def _forward_canvas_frames(student_id: str, session, queue: asyncio.Queue):
    while True:
        payload = await queue.get()

        if payload is None:
            return

        event_type = payload.get("type")

        try:
            if event_type == "canvas_snapshot_text":
                snapshot_data = f"CURRENT_WHITEBOARD_OBJECTS_STORE:\n{payload.get('data', '')}"
                await session.send_realtime_input(text=snapshot_data)
                logger.info("Injected canvas snapshot text for %s", student_id)
                continue

            if event_type == "canvas_snapshot_vision":
                image_string = payload.get("image", "")
                if "," in image_string:
                    image_string = image_string.split(",")[-1]

                raw_image_bytes = base64.b64decode(image_string)
                await session.send_realtime_input(
                    video=types.Blob(
                        data=raw_image_bytes,
                        mime_type="image/png",
                    )
                )
                logger.info("Injected canvas vision frame for %s", student_id)
                continue

            logger.warning("Unknown canvas payload routed to canvas worker for %s: %s", student_id, event_type)
        except ConnectionClosedError as exc:
            logger.warning("Gemini session closed while forwarding canvas for %s: %s", student_id, str(exc))
            return
        except Exception as exc:
            logger.exception("Failed forwarding canvas frame to Gemini for %s: %s", student_id, str(exc))


async def _forward_control_frames(student_id: str, session, queue: asyncio.Queue):
    while True:
        payload = await queue.get()

        if payload is None:
            return

        event_type = payload.get("type")

        try:
            if event_type == "tool_result":
                tool_call_id = payload.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id.strip():
                    result = {
                        "success": bool(payload.get("success")),
                        "message": payload.get("message"),
                        "details": payload.get("details"),
                    }
                    resolve_tool_result(student_id, tool_call_id, result)
                    logger.info(
                        "Resolved frontend tool ack for %s | tool_call_id=%s | success=%s",
                        student_id,
                        tool_call_id,
                        result["success"],
                    )
                else:
                    logger.warning("Received tool_result without a valid tool_call_id for %s", student_id)
                continue

            if event_type == "audio_stream_end":
                logger.info("Received audio stream end control event for %s", student_id)
                continue

            logger.warning("Unknown control payload routed to control worker for %s: %s", student_id, event_type)
        except ConnectionClosedError as exc:
            logger.warning("Gemini session closed while processing control frames for %s: %s", student_id, str(exc))
            return
        except Exception as exc:
            logger.exception("Failed processing control frame for %s: %s", student_id, str(exc))


async def forward_frontend_mic_and_canvas_to_gemini(student_id: str, websocket, session):
    """
    Read the student WebSocket and fan frames out to dedicated workers so audio, canvas,
    and control traffic do not block one another.
    """
    audio_queue: asyncio.Queue = asyncio.Queue()
    canvas_queue: asyncio.Queue = asyncio.Queue()
    control_queue: asyncio.Queue = asyncio.Queue()
    stop_event = asyncio.Event()

    worker_tasks = [
        asyncio.create_task(_forward_audio_frames(student_id, session, audio_queue)),
        asyncio.create_task(_forward_canvas_frames(student_id, session, canvas_queue)),
        asyncio.create_task(_forward_control_frames(student_id, session, control_queue)),
    ]

    def _request_shutdown():
        if not stop_event.is_set():
            stop_event.set()
            for queue in (audio_queue, canvas_queue, control_queue):
                try:
                    queue.put_nowait(None)
                except Exception:
                    pass

    def _queue_frame(queue: asyncio.Queue, payload):
        if stop_event.is_set():
            return

        queue.put_nowait(payload)

    try:
        logger.info("Multimodal inbound streaming worker activated for %s", student_id)

        while True:
            if stop_event.is_set():
                logger.info("Stopping inbound frame reader for %s because Gemini session is closed.", student_id)
                return

            try:
                frame = await websocket.receive()

                if "bytes" in frame and frame["bytes"]:
                    raw_data = frame["bytes"]
                    if not raw_data:
                        continue

                    audio_data = None

                    if raw_data.startswith(b'{"') or b'"type"' in raw_data:
                        try:
                            parsed_payload = json.loads(raw_data.decode("utf-8", errors="ignore"))
                            inner_bytes_data = parsed_payload.get("bytes")

                            if isinstance(inner_bytes_data, str):
                                audio_data = base64.b64decode(inner_bytes_data)
                            elif inner_bytes_data:
                                audio_data = bytes(inner_bytes_data)
                        except Exception as parse_err:
                            logger.warning("Failed parsing binary-wrapped JSON audio metadata: %s", str(parse_err))
                            continue
                    else:
                        audio_data = raw_data

                    if not audio_data:
                        continue

                    _queue_frame(audio_queue, audio_data)
                    logger.info(
                        "Queued inbound mic frame for %s | bytes=%s",
                        student_id,
                        len(audio_data),
                    )

                elif "text" in frame and frame["text"]:
                    payload = json.loads(frame["text"])
                    event_type = payload.get("type")
                    logger.info(
                        "Inbound canvas/control frame received for %s | type=%s | bytes=%s",
                        student_id,
                        event_type,
                        len(frame["text"]),
                    )

                    if event_type in {"canvas_snapshot_text", "canvas_snapshot_vision"}:
                        _queue_frame(canvas_queue, payload)
                        continue

                    if event_type == "tool_result":
                        _queue_frame(control_queue, payload)
                        continue

                    if event_type == "audio_stream_end":
                        _queue_frame(audio_queue, payload)
                        continue

                    logger.warning("Ignoring unsupported inbound control event for %s: %s", student_id, event_type)

            except (WebSocketDisconnect, RuntimeError):
                logger.info("Connection drop detected for student %s. Stopping inbound worker thread.", student_id)
                _request_shutdown()
                return
            except json.JSONDecodeError as decode_err:
                logger.warning("Skipping malformed canvas payload for %s: %s", student_id, str(decode_err))
            except Exception as exc:
                logger.warning("Recoverable frame skipping on pipeline for %s: %s", student_id, str(exc))
                await asyncio.sleep(0.01)

            await asyncio.sleep(0.001)

    except asyncio.CancelledError:
        logger.info("Multimodal input worker safely cancelled for student %s.", student_id)
    except Exception as fatal_err:
        logger.error("Non-fatal collapse caught inside inbound processor for %s: %s", student_id, str(fatal_err))
    finally:
        _request_shutdown()
        for task in worker_tasks:
            task.cancel()

        await asyncio.gather(*worker_tasks, return_exceptions=True)
