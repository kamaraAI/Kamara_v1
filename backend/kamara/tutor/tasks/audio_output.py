from __future__ import annotations

from kamara.tutor.audio_tasks import ai_audio_to_frontend


async def forward_ai_audio_to_frontend(websocket, token: str, session_id: str | None, runtime, student_id: str) -> None:
    await ai_audio_to_frontend(websocket=websocket, student_id=student_id, runtime=runtime, session_id=session_id)
