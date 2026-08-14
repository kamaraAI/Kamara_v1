import asyncio
import logging
import os

from dotenv import load_dotenv
from fastapi import WebSocket
from google.genai import Client, types
from starlette.websockets import WebSocketState

from .response_handler import receive_response_from_ai
from .session_resume_store import load_session_resumption_handle
from .task_handler import forward_frontend_mic_and_canvas_to_gemini

load_dotenv()
logger = logging.getLogger("KamaraLogger")

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("Missing API KEY")

client = Client(api_key=api_key)


async def agent(student_id: str, websocket: WebSocket, system_prompt: str, session_id: str | None = None):
    """
    Orchestrates the Gemini Live session and bridges the browser WebSocket.
    """
    model = "gemini-3.1-flash-live-preview"
    reconnect_attempt = 0
    resume_handle = await load_session_resumption_handle(student_id, session_id)
    voice_only_prompt = f"{system_prompt}\n\n"
    
    #(
   #     "TEMPORARY SESSION MODE: Ignore any whiteboard, canvas, drawing, or tool instructions. "
  #      "Do not call tools. Focus only on hearing the student's microphone input and speaking back continuously by audio.\n\n"
  #      f"{system_prompt}"
   # )

    while websocket.client_state == WebSocketState.CONNECTED:
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config={
                "automatic_activity_detection": {
                    "disabled": True,   # False
                    "start_of_speech_sensitivity": types.StartSensitivity.START_SENSITIVITY_LOW,
                    "end_of_speech_sensitivity": types.EndSensitivity.END_SENSITIVITY_LOW,
                    "prefix_padding_ms": 120,
                    "silence_duration_ms": 700,
                }
            },
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=120_000,
                sliding_window=types.SlidingWindow(target_tokens=80_000),
            ),
            session_resumption=types.SessionResumptionConfig(
                handle=resume_handle,
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(text=voice_only_prompt)]
            ),
            speech_config={
                "voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}}
            },
        )

        try:
            async with client.aio.live.connect(model=model, config=config) as session:
                logger.info(
                    "AI Orchestrator running live session for student=%s | resumption=%s",
                    student_id,
                    bool(resume_handle),
                )

                reconnect_attempt = 2

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(
                        forward_frontend_mic_and_canvas_to_gemini(
                            student_id=student_id,
                            websocket=websocket,
                            session=session,
                        )
                    )

                    tg.create_task(
                        receive_response_from_ai(
                            session=session,
                            student_id=student_id,
                            websocket=websocket,
                            session_id=session_id,
                        )
                    )

            if websocket.client_state != WebSocketState.CONNECTED:
                break

        except TimeoutError:
            logger.error("Time out, failed to connect")
            break
        except Exception as exc:
            import traceback

            logger.error("Detailed TaskGroup Failure:\n%s", "".join(traceback.format_exception(exc)))
            logger.error("Error occurred inside the runtime orchestrator for %s: %s", student_id, str(exc))

            if websocket.client_state != WebSocketState.CONNECTED:
                break

            reconnect_attempt += 1
            if reconnect_attempt > 10:
                logger.error("Gemini reconnect limit reached for %s. Ending live session.", student_id)
                break

            resume_handle = await load_session_resumption_handle(student_id, session_id) or resume_handle
            await asyncio.sleep(min(2**reconnect_attempt, 8))
