from __future__ import annotations

from kamara.tutor.audio_tasks import listen_to_frontend_audio


async def forward_frontend_audio_to_ai(websocket, token: str, session_id: str | None, runtime, student_id: str) -> None:
    await listen_to_frontend_audio(websocket=websocket, student_id=student_id, runtime=runtime, session_id=session_id)
