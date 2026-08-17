from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from fastapi import WebSocket
from google.genai import Client, types

from .audio_tasks import send_frontend_audio_to_ai
from .live_session_runtime import LiveSessionRuntime, get_live_session_runtime
from .response_handler import receive_response_from_ai
from .session_resume_store import load_session_resumption_handle
from .toolset.tools import tools as board_tools

load_dotenv()
logger = logging.getLogger("KamaraLogger")

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("Missing API KEY")

client = Client(api_key=api_key)


async   def _realtime_input_config() -> types.RealtimeInputConfig:
        return types.RealtimeInputConfig(
            automaticActivityDetection=types.AutomaticActivityDetection(
                disabled=False,
                startOfSpeechSensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                endOfSpeechSensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                prefixPaddingMs=120,
                silenceDurationMs=700,
        )
    )


async def agent(
    student_id: str,
    websocket: WebSocket | None,
    system_prompt: str,
    session_id: str | None = None,
    runtime: LiveSessionRuntime | None = None,
):
    """
    Orchestrates the Gemini Live session and runs the audio tasks.
    """
    resume_handle = await load_session_resumption_handle(student_id, session_id)
    runtime = runtime or await get_live_session_runtime(student_id, session_id, system_prompt)

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        tools=types.Tool(function_declarations=board_tools["function_declarations"]),
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        realtime_input_config=await _realtime_input_config(),
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=120_000,
            sliding_window=types.SlidingWindow(target_tokens=80_000),
        ),
        session_resumption=types.SessionResumptionConfig(handle=resume_handle),
        system_instruction=types.Content(
            parts=[types.Part.from_text(text=f"{system_prompt}\n\n")]
        ),
        speech_config={"voice_config": {"prebuilt_voice_config": {"voice_name": "Rasalgethi"}}},
    )

    async with client.aio.live.connect(model="gemini-3.1-flash-live-preview", config=config) as session:
        logger.info(
            "AI Orchestrator running live session for student=%s | resumption=%s",
            student_id,
            bool(resume_handle),
        )

        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(
                send_frontend_audio_to_ai(
                    session=session,
                    student_id=student_id,
                    runtime=runtime,
                )
            )
            task_group.create_task(
                receive_response_from_ai(
                    session=session,
                    student_id=student_id,
                    session_id=session_id,
                    runtime=runtime,
                )
            )
