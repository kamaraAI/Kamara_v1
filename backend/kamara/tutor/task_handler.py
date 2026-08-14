import asyncio
import json
import logging
import base64

from fastapi import WebSocketDisconnect
from google.genai import types
from websockets.exceptions import ConnectionClosedError

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


async def forward_frontend_mic_and_canvas_to_gemini(student_id: str, websocket, session):
    """
    Read the student WebSocket and forward only microphone audio to Gemini.
    Canvas and control traffic are intentionally ignored for this temporary voice-only pass.
    """
    audio_queue: asyncio.Queue = asyncio.Queue()
    stop_event = asyncio.Event()

    worker_tasks = [
        asyncio.create_task(_forward_audio_frames(student_id, session, audio_queue)),
    ]

    def _request_shutdown():
        if not stop_event.is_set():
            stop_event.set()
            try:
                audio_queue.put_nowait(None)
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
                            event_type = parsed_payload.get("type")

                            if event_type == "audio_stream_end":
                                _queue_frame(audio_queue, {"type": "audio_stream_end"})
                                logger.info("Received audio stream end control event for %s", student_id)
                                continue

                            # If the JSON-wrapped payload contains inner bytes, extract them and
                            # treat as audio. Otherwise log and skip the frame.
                            inner_bytes_data = parsed_payload.get("bytes")
                            if isinstance(inner_bytes_data, str):
                                audio_data = base64.b64decode(inner_bytes_data)
                            elif inner_bytes_data:
                                audio_data = bytes(inner_bytes_data)
                            else:
                                logger.info(
                                    "Ignoring non-audio binary-wrapped frame for %s | type=%s",
                                    student_id,
                                    event_type,
                                )
                                continue
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
                        "Ignoring non-audio inbound text frame for %s | type=%s | bytes=%s",
                        student_id,
                        event_type,
                        len(frame["text"]),
                    )

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
