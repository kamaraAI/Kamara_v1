from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from google.genai import Client, types
from websockets.exceptions import ConnectionClosedError

from connection.connect_manager import manager
from kamara.tutor.audio_settings import CHUNK_SIZE, SEND_SAMPLE_RATE
from kamara.tutor.response_handler import receive_response_from_ai
from kamara.tutor.session_resume_store import load_session_resumption_handle
from kamara.tutor.tool_ack import resolve_tool_result
from kamara.tutor.toolset.tools import tools as board_tools

load_dotenv()

logger = logging.getLogger("KamaraLogger")

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("Missing API KEY")

client = Client(api_key=api_key)

_RUNTIME_REGISTRY: dict[str, "LiveSessionRuntime"] = {}
_REGISTRY_LOCK = asyncio.Lock()


async def _runtime_key(student_id: str, session_id: str | None) -> str:
    return f"{student_id}:{session_id or 'anonymous'}"


async def _is_wav_audio(data: bytes) -> bool:
    return (
        len(data) >= 12
        and data[0:4] == b"RIFF"
        and data[8:12] == b"WAVE"
    )


async def _extract_image_bytes(image_payload: str | bytes) -> bytes:
        raw_bytes = image_payload

        if isinstance(raw_bytes, str):
            if "," in raw_bytes:
                raw_bytes = raw_bytes.split(",")[-1]
            raw_bytes = base64.b64decode(raw_bytes)

        return bytes(raw_bytes)


async def _infer_image_mime_type(image_payload: str | bytes) -> str:
    if isinstance(image_payload, str) and image_payload.startswith("data:"):
        header = image_payload.split(",", 1)[0]
        if "image/jpeg" in header or "image/jpg" in header:
            return "image/jpeg"
        if "image/png" in header:
            return "image/png"
        if "image/webp" in header:
            return "image/webp"

    return "image/jpeg"


async def _live_tools() -> list[types.Tool]:

    return [
        types.Tool(function_declarations=board_tools["function_declarations"])
    ]


async def _realtime_input_config() -> types.RealtimeInputConfig:
    return types.RealtimeInputConfig(
        automaticActivityDetection=types.AutomaticActivityDetection(
            disabled=False,
            startOfSpeechSensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
            endOfSpeechSensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefixPaddingMs=120,
            silenceDurationMs=700,
        )
    )


@dataclass
class LiveSessionRuntime:
    student_id: str
    session_id: str | None
    system_prompt: str
    resume_handle: str | None = None
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    audio_queue_mic: asyncio.Queue[dict[str, Any] | bytes | None] = field(default_factory=lambda: asyncio.Queue(maxsize=5))
    audio_queue_output: asyncio.Queue[bytes | dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    canvas_queue_input: asyncio.Queue[dict[str, Any] | None] = field(default_factory=lambda: asyncio.Queue(maxsize=5))
    canvas_queue_output: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = None
    _start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _audio_buffer: bytearray = field(default_factory=bytearray)
    _audio_flush_at: float = 0.0

    async def start(self) -> None:
        async with self._start_lock:
            if self._task and not self._task.done():
                return

            self._task = asyncio.create_task(self._run())

    async def _signal_audio_shutdown(self) -> None:
        try:
            await self.audio_queue_mic.put(None)
        except Exception:
            pass

        try:
            await self.audio_queue_output.put(None)
        except Exception:
            pass

    async def _signal_canvas_shutdown(self) -> None:
        try:
            await self.canvas_queue_input.put(None)
        except Exception:
            pass

        try:
            await self.canvas_queue_output.put(None)
        except Exception:
            pass

    async def stop_if_idle(self) -> None:
        if manager.has_any_active_connection(self.student_id):
            return

        self.stopped.set()
        await self._signal_audio_shutdown()
        await self._signal_canvas_shutdown()
        await self.queue.put({"type": "__shutdown__"})

    async def enqueue(self, payload: dict[str, Any]) -> None:
        if self.stopped.is_set():
            return

        await self.queue.put(payload)

    async def enqueue_audio_input(self, payload: dict[str, Any] | bytes | None) -> None:
        if self.stopped.is_set():
            return

        await self.audio_queue_mic.put(payload)

    async def enqueue_audio_output(self, audio_chunk: bytes | dict[str, Any] | None) -> None:
        if self.stopped.is_set():
            return

        await self.audio_queue_output.put(audio_chunk)

    async def enqueue_canvas_input(self, payload: dict[str, Any] | None) -> None:
        if self.stopped.is_set():
            return

        if isinstance(payload, dict) and payload.get("type") == "canvas_snapshot_vision":
            retained: list[dict[str, Any] | None] = []
            while True:
                try:
                    queued = self.canvas_queue_input.get_nowait()
                except asyncio.QueueEmpty:
                    break

                if queued is None:
                    retained.append(None)
                    continue

                if isinstance(queued, dict) and queued.get("type") == "canvas_snapshot_vision":
                    continue

                retained.append(queued)

            for queued in retained:
                await self.canvas_queue_input.put(queued)

        await self.canvas_queue_input.put(payload)

    async def enqueue_canvas_output(self, payload: dict[str, Any] | None) -> None:
        if self.stopped.is_set():
            return

        await self.canvas_queue_output.put(payload)

    async def _send_audio(self, session, payload: dict[str, Any]) -> None:
        if payload.get("type") == "audio_stream_end":
            if self._audio_buffer:
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=bytes(self._audio_buffer),
                        mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                    )
                )
                logger.info(
                    "Flushed buffered audio before stream end for %s | bytes=%s",
                    self.student_id,
                    len(self._audio_buffer),
                )
                self._audio_buffer.clear()

            await session.send_realtime_input(audio_stream_end=True)
            logger.info("Forwarded audio stream end marker for %s", self.student_id)
            return

        audio_data = payload.get("data")
        if not isinstance(audio_data, (bytes, bytearray)) or not audio_data:
            return

        self._audio_buffer.extend(bytes(audio_data))
        now = asyncio.get_running_loop().time()

        # Send audio in small batches instead of frame-by-frame.
        # This reduces the request rate while still preserving near-real-time speech.
        if self._audio_flush_at == 0.0:
            self._audio_flush_at = now

        if len(self._audio_buffer) < CHUNK_SIZE and (now - self._audio_flush_at) < 0.14:
            return

        buffered_audio = bytes(self._audio_buffer)
        self._audio_buffer.clear()
        self._audio_flush_at = now

        await session.send_realtime_input(
            audio=types.Blob(
                data=buffered_audio,
                mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
            )
        )
        logger.info(
            "Forwarded buffered mic audio to Gemini for %s | bytes=%s",
            self.student_id,
            len(buffered_audio),
        )

    async def _send_canvas(self, session, payload: dict[str, Any]) -> None:
        event_type = payload.get("type")

        if event_type == "canvas_snapshot_text":
            snapshot = payload.get("data")
            if not isinstance(snapshot, str) or not snapshot.strip():
                return

            await session.send(
                input=types.LiveClientContent(
                    turns=[
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=f"CURRENT_WHITEBOARD_OBJECTS_STORE:\n{snapshot}")],
                        )
                    ]
                )
            )
            logger.info("Injected canvas text snapshot for %s", self.student_id)
            return

        if event_type == "canvas_snapshot_vision":
            image_payload = payload.get("image")
            if not image_payload:
                return

            raw_bytes = await self._extract_image_bytes(image_payload)
            if not raw_bytes:
                return

            await session.send(
                input=types.LiveClientContent(
                    turns=[
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_bytes(
                                    data=raw_bytes,
                                    mime_type="image/png",
                                )
                            ],
                        )
                    ]
                )
            )
            logger.info("Injected canvas vision snapshot for %s", self.student_id)

    async def _send_control(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("type")

        if event_type == "tool_result":
            tool_call_id = payload.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id.strip():
                resolve_tool_result(self.student_id, tool_call_id, payload)
            return

        if event_type == "end_session":
            self.stopped.set()
            return

    async def _drain_queue(self, session) -> None:
        while not self.stopped.is_set():
            payload = await self.queue.get()
            if payload.get("type") == "__shutdown__":
                if self._audio_buffer:
                    try:
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=bytes(self._audio_buffer),
                                mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                            )
                        )
                    except Exception:
                        pass
                    self._audio_buffer.clear()
                return

            channel = payload.get("channel")
            try:
                if channel == "audio":
                    await self._send_audio(session, payload)
                elif channel == "canvas":
                    await self._send_canvas(session, payload)
                elif channel == "control":
                    await self._send_control(payload)
            except ConnectionClosedError as exc:
                logger.error(
                    "Gemini session closed while forwarding %s payload for %s: %s",
                    channel,
                    self.student_id,
                    str(exc),
                )
                self._audio_buffer.clear()
                self.stopped.set()
                return
            except Exception as exc:
                logger.error(
                    "Failed to forward %s payload for %s: %s",
                    channel,
                    self.student_id,
                    str(exc),
                    exc_info=True,
                )

    async def _drain_audio_queue(self, session) -> None:
        while not self.stopped.is_set():
            payload = await self.audio_queue_mic.get()

            if payload is None:
                if self._audio_buffer:
                    try:
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=bytes(self._audio_buffer),
                                mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                            )
                        )
                    except Exception:
                        pass
                    self._audio_buffer.clear()
                try:
                    await session.send_realtime_input(audio_stream_end=True)
                except Exception:
                    pass
                return

            if isinstance(payload, dict) and payload.get("type") == "audio_stream_end":
                if self._audio_buffer:
                    try:
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=bytes(self._audio_buffer),
                                mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                            )
                        )
                    except Exception:
                        pass
                    self._audio_buffer.clear()

                try:
                    await session.send_realtime_input(audio_stream_end=True)
                except Exception:
                    pass
                continue

            audio_data = payload if isinstance(payload, (bytes, bytearray)) else payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(audio_data, (bytes, bytearray)) or not audio_data:
                continue

            self._audio_buffer.extend(bytes(audio_data))
            now = asyncio.get_running_loop().time()

            if self._audio_flush_at == 0.0:
                self._audio_flush_at = now

            if len(self._audio_buffer) < CHUNK_SIZE and (now - self._audio_flush_at) < 0.14:
                continue

            buffered_audio = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            self._audio_flush_at = now

            try:
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=buffered_audio,
                        mime_type=f"audio/pcm;rate={SEND_SAMPLE_RATE}",
                    )
                )
                logger.info(
                    "Forwarded buffered mic audio to Gemini for %s | bytes=%s",
                    self.student_id,
                    len(buffered_audio),
                )
            except ConnectionClosedError as exc:
                logger.error(
                    "Gemini session closed while forwarding mic queue for %s: %s",
                    self.student_id,
                    str(exc),
                )
                self._audio_buffer.clear()
                self.stopped.set()
                return
            except Exception as exc:
                logger.error(
                    "Failed to forward mic queue audio for %s: %s",
                    self.student_id,
                    str(exc),
                    exc_info=True,
                )

    async def _drain_canvas_input_queue(self, session) -> None:
        while not self.stopped.is_set():
            payload = await self.canvas_queue_input.get()

            if payload is None:
                return

            event_type = payload.get("type") if isinstance(payload, dict) else None

            try:
                if event_type == "canvas_snapshot_text":
                    snapshot = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(snapshot, str) or not snapshot.strip():
                        continue

                    await session.send(
                        input=types.LiveClientContent(
                            turns=[
                                types.Content(
                                    role="user",
                                    parts=[types.Part.from_text(text=f"CURRENT_WHITEBOARD_OBJECTS_STORE:\n{snapshot}")],
                                )
                            ]
                        )
                    )
                    logger.info("Injected canvas text snapshot for %s", self.student_id)
                else:
                    image_payload = payload.get("image") if isinstance(payload, dict) else None
                    if not image_payload:
                        continue

                    image_bytes = await _extract_image_bytes(image_payload)
                    if not image_bytes:
                        continue

                    mime_type = await _infer_image_mime_type(image_payload)
                    await session.send_realtime_input(
                        video=types.Blob(
                            data=image_bytes,
                            mime_type=mime_type,
                        )
                    )
                    logger.info(
                        "Injected canvas media snapshot for %s | bytes=%s | mime_type=%s",
                        self.student_id,
                        len(image_bytes),
                        mime_type,
                    )

                await asyncio.sleep(1.0)
            except ConnectionClosedError as exc:
                logger.error(
                    "Gemini session closed while forwarding canvas queue for %s: %s",
                    self.student_id,
                    str(exc),
                )
                self.stopped.set()
                return
            except Exception as exc:
                logger.error(
                    "Failed to forward canvas queue payload for %s: %s",
                    self.student_id,
                    str(exc),
                    exc_info=True,
                )

    async def _run(self) -> None:
        reconnect_attempt = 0

        while not self.stopped.is_set():
            self.resume_handle = await load_session_resumption_handle(self.student_id, self.session_id) or self.resume_handle

            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                tools=_live_tools(),
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                realtime_input_config=_realtime_input_config(),
                context_window_compression=types.ContextWindowCompressionConfig(
                    trigger_tokens=120_000,
                    sliding_window=types.SlidingWindow(target_tokens=80_000),
                ),
                session_resumption=types.SessionResumptionConfig(handle=self.resume_handle),
                system_instruction=types.Content(
                    parts=[types.Part.from_text(text=f"{self.system_prompt}\n\n")]
                ),
                speech_config={
                    "voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}}
                },
            )

            try:
                async with client.aio.live.connect(model="gemini-3.1-flash-live-preview", config=config) as session:
                    logger.info(
                        "Gemini live session started for student=%s | session=%s | resumption=%s",
                        self.student_id,
                        self.session_id,
                        bool(self.resume_handle),
                    )
                    reconnect_attempt = 0
                    self.started.set()

                    responder_task = asyncio.create_task(
                        receive_response_from_ai(
                            session=session,
                            student_id=self.student_id,
                            session_id=self.session_id,
                            runtime=self,
                        )
                    )
                    drain_task = asyncio.create_task(self._drain_queue(session))
                    mic_task = asyncio.create_task(self._drain_audio_queue(session))
                    canvas_task = asyncio.create_task(self._drain_canvas_input_queue(session))

                    done, pending = await asyncio.wait(
                        {responder_task, drain_task, mic_task, canvas_task},
                        return_when=asyncio.FIRST_EXCEPTION,
                    )

                    for task in pending:
                        task.cancel()

                    await asyncio.gather(*pending, return_exceptions=True)
                    await asyncio.gather(*done, return_exceptions=True)

                    if self.stopped.is_set() or drain_task.done() or mic_task.done() or canvas_task.done():
                        break

            except Exception as exc:
                self._audio_buffer.clear()
                logger.error(
                    "Live runtime error for student=%s session=%s: %s",
                    self.student_id,
                    self.session_id,
                    str(exc),
                    exc_info=True,
                )

                reconnect_attempt += 1
                if reconnect_attempt > 10 or self.stopped.is_set():
                    break

                await asyncio.sleep(min(2 ** reconnect_attempt, 8))

        await self._signal_audio_shutdown()
        await self._signal_canvas_shutdown()
        self.started.clear()


async def get_live_session_runtime(student_id: str, session_id: str | None, system_prompt: str) -> LiveSessionRuntime:
    key = _runtime_key(student_id, session_id)

    async with _REGISTRY_LOCK:
        runtime = _RUNTIME_REGISTRY.get(key)
        if runtime is None:
            runtime = LiveSessionRuntime(
                student_id=student_id,
                session_id=session_id,
                system_prompt=system_prompt,
            )
            _RUNTIME_REGISTRY[key] = runtime
        else:
            runtime.system_prompt = system_prompt

    await runtime.start()
    return runtime


async def drop_live_session_runtime(student_id: str, session_id: str | None) -> None:
    key = _runtime_key(student_id, session_id)

    async with _REGISTRY_LOCK:
        runtime = _RUNTIME_REGISTRY.get(key)
        if not runtime:
            return

        await runtime.stop_if_idle()
        if runtime.stopped.is_set():
            _RUNTIME_REGISTRY.pop(key, None)
