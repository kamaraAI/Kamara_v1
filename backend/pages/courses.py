import asyncio
import json
import logging
import re
from typing import Optional
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.auth import verify_student_token
from app.supabase_client import get_supabase_admin
from app.subscriptions.service import enforce_feature_access, record_usage_event
from kamara.writer.writer import run_writer_agent
from kamara.writer.schemas import WriterRequestSchema

logger = logging.getLogger("KamaraLogger")
course_router = APIRouter(prefix="/api/v1", tags=["Course Pipeline"])


class CourseInitializationRequest(BaseModel):
    course: str
    prompt: str
    helper_material_url: Optional[str] = None


def _public_generation_error(error: Exception) -> str:
    message = str(error)
    if "403" in message or "Forbidden" in message:
        return "The AI provider refused the generation request. Please try again in a moment."

    clean_message = re.sub(r"<[^>]+>", " ", message)
    clean_message = " ".join(clean_message.split())
    return clean_message[:220] if clean_message else "Course generation failed. Please try again."


@course_router.post("/pages/course/generate")
@course_router.post("/courses/generate")
async def generate_course_modules(
    payload: CourseInitializationRequest,
    current_user: dict = Depends(verify_student_token),
):# want to use this kind of system to get user id 
    """
    Streaming pipeline that extracts text from uploaded materials, runs the writer
    agent, stores generated modules, and emits live progress updates.
    """
    student_id = current_user.id
    logger.info("Initializing course generation for student: %s", student_id)

    async def writer_agent_generator():
        supabase = get_supabase_admin()
        session_id = None
        finished_successfully = False

        try:
            enforce_feature_access(
                str(student_id),
                "course_generation",
                has_external_source=bool(payload.helper_material_url),
                supabase=supabase,
            )
            record_usage_event(str(student_id), "message_send", supabase=supabase)

            yield f"data: {json.dumps({'status': 'init', 'message': 'Waking up Kamara Writer Agent...'})}\n\n"
            await asyncio.sleep(0.3)

            session_insert = supabase.table("sessions").insert({
                "student_id": student_id,
                "course": payload.course.strip().lower(),
                "user_prompt": payload.prompt.strip(),
                "helper_material_url": payload.helper_material_url,
            }).execute()

            if not session_insert or not session_insert.data:
                raise RuntimeError("Failed to register study session metadata row.")

            session_data = session_insert.data[0]
            session_id = session_data.get("id")

            yield f"data: {json.dumps({'status': 'processing', 'message': 'Checking attached learning materials...', 'session_id': str(session_id)})}\n\n"
            await asyncio.sleep(0.3)

            try:
                agent_response = await run_writer_agent(
                    request=WriterRequestSchema(
                        course=payload.course,
                        prompt=payload.prompt,
                        helper_material_url=payload.helper_material_url,
                    ),
                    user_id=str(student_id),
                )
            except Exception as e:
                logger.error(
                    "Writer Agent failed inside router pipeline loop; generating default fallback object: %s",
                    str(e),
                    exc_info=True,
                )
                agent_response = None

            if agent_response is None:
                raise RuntimeError("Writer agent returned no response.")

            modules_list = agent_response.modules
            full_textbook_notes = agent_response.textbook_handout_notes

            enforce_feature_access(
                str(student_id),
                "note_size",
                content_chars=len(full_textbook_notes or ""),
                supabase=supabase,
            )

            yield f"data: {json.dumps({'status': 'processing', 'message': 'Research completed! Unpacking your structured study modules...'})}\n\n"
            await asyncio.sleep(0.5)

            for index, mod_step in enumerate(modules_list, start=1):
                module_title = mod_step.sub_topic if mod_step.sub_topic else f"Module {index}"
                module_notes = getattr(mod_step, "section_notes", None)
                if not module_notes:
                    module_notes = getattr(mod_step, "teaching_guidelines", "")
                compiled_body = (
                    f"### Topic Notes\n"
                    f"{module_notes}\n\n"
                    f"### Comprehensive Textbook Study Notes\n"
                    f"{full_textbook_notes}"
                )

                yield f"data: {json.dumps({'status': 'processing', 'message': f'Writing {module_title} into your Library...'})}\n\n"

                supabase.table("library").insert({
                    "session_id": session_id,
                    "student_id": student_id,
                    "content_type": "ai_module",
                    "title": module_title,
                    "body_text": compiled_body,
                }).execute()

            finished_successfully = True
            yield f"data: {json.dumps({'status': 'complete', 'message': 'Course generation finished successfully.', 'session_id': str(session_id)})}\n\n"

        except Exception as err:
            logger.error("COURSE ROUTE FAILURE: %s", str(err), exc_info=True)
            yield f"data: {json.dumps({'status': 'error', 'message': f'Generation failure: {_public_generation_error(err)}'})}\n\n"
        finally:
            logger.info(
                "Course generation stream finished for student=%s session_id=%s success=%s",
                student_id,
                session_id,
                finished_successfully,
            )

    return StreamingResponse(writer_agent_generator(), media_type="text/event-stream", status_code=status.HTTP_200_OK)
