import json
import logging
import os
from functools import lru_cache

from dotenv import load_dotenv
from openai import AsyncOpenAI

from .prompts import build_writer_system_prompt, build_writer_user_prompt
from .schemas import WriterContentBundle, WriterModuleSchema, WriterRequestSchema, WriterResponseSchema
from .source_loader import build_writer_content_bundle

logger = logging.getLogger("KamaraLogger")

WRITER_MODEL = os.getenv("OPENROUTER_WRITER_MODEL", 
                        "deepseek/deepseek-v4-flash"
                       )
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

load_dotenv()


def _extract_course_context(message: str) -> tuple[str, str]:
    import re

    subject_match = re.search(r"Course Subject Classification:\s*(.+)", message)
    goal_match = re.search(r"Student Main Learning Goal:\s*(.+)", message)

    subject = subject_match.group(1).strip() if subject_match else "General Studies"
    goal = goal_match.group(1).strip() if goal_match else "the requested topic"
    return subject or "General Studies", goal or "the requested topic"


def _fallback_syllabus(message: str) -> WriterResponseSchema:
    subject, goal = _extract_course_context(message)
    topic = goal[:90].strip(". ") or subject

    modules = [
        WriterModuleSchema(
            sub_topic=f"{subject.title()} foundations",
            section_notes=(
                f"## {subject.title()} Foundations\n\n"
                f"This section introduces the core vocabulary and ideas behind "
                f"{topic}, starting from a plain-language definition before any "
                "formal notation is used.\n\n"
                f"**Definition:** {topic} refers to the foundational concept "
                "being studied in this lesson; the precise definition depends "
                "on the specific subject and should be verified against a "
                "current, reliable source.\n\n"
                "**Worked Example:** A simple case applying the definition "
                "above, with the complete solution shown step by step, goes "
                "here."
            ),
        ),
        WriterModuleSchema(
            sub_topic="Core methods",
            section_notes=(
                "## Core Methods\n\n"
                "This section lays out the standard method or rule set used "
                "to work through problems on this topic.\n\n"
                "**Example 1 (basic case):** A straightforward worked example "
                "with its full solution.\n\n"
                "**Example 2 (exam-style case):** A more advanced worked "
                "example, closer to what appears in exam questions, with its "
                "full solution."
            ),
        ),
        WriterModuleSchema(
            sub_topic="Summary and key takeaways",
            section_notes=(
                "## Summary\n\n"
                "A concise recap of the key definitions, formulas, and "
                "methods covered above, presented as a short list a student "
                "could review quickly before an exam."
            ),
        ),
    ]

    notes = (
        f"# Study Guide: {subject.title()}\n\n"
        f"## Learning Goal\n{goal}\n\n"
        f"{modules[0].section_notes}\n\n"
        f"{modules[1].section_notes}\n\n"
        f"{modules[2].section_notes}\n"
    )

    assessment_questions = [
        f"State, in your own words, the core definition covered under {subject.title()}.",
        "Work through one basic example using the standard method described above, showing every step.",
        "Attempt one exam-style question on this topic and check your reasoning against the worked examples.",
    ]

    return WriterResponseSchema(
        title=f"{subject.title()} Study Guide",
        source_type="prompt",
        source_summary="Fallback prompt-only note package.",
        modules=modules,
        textbook_handout_notes=notes,
        assessment_questions=assessment_questions,
    )


def _normalize_message_parts(parts: list[object]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []

    for part in parts:
        if isinstance(part, dict):
            normalized.append(part)
            continue

        if hasattr(part, "model_dump"):
            normalized.append(part.model_dump())
            continue

        normalized.append({"type": "text", "text": str(part)})

    return normalized


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    return cleaned.strip()


def _parse_writer_response(content: str) -> WriterResponseSchema:
    raw_json = _strip_code_fences(content)
    parsed = json.loads(raw_json)
    return WriterResponseSchema.model_validate(parsed)


def _build_default_assessment_questions(subject: str, topic: str) -> list[str]:
    return [
        f"State, in your own words, the core idea of {topic} in {subject}.",
        f"Work through one basic example involving {topic}, showing every step.",
        f"Attempt one exam-style question on {topic} and check your reasoning against the notes.",
    ]


def _coerce_writer_payload(
    parsed: dict,
    *,
    request: WriterRequestSchema,
    bundle: WriterContentBundle,
) -> dict:
    notes_text = parsed.get("textbook_handout_notes") or parsed.get("notes")

    if isinstance(notes_text, list):
        notes_text = "\n\n".join(str(item) for item in notes_text)

    if isinstance(notes_text, str) and notes_text.strip() and not parsed.get("modules") and not parsed.get("sections"):
        fallback = _fallback_syllabus(request.prompt)
        return {
            "title": parsed.get("title") or fallback.title,
            "source_type": parsed.get("source_type") or bundle.source_type.value,
            "source_summary": parsed.get("source_summary") or bundle.source_summary,
            "modules": parsed.get("modules")
            or [module.model_dump() for module in fallback.modules],
            "textbook_handout_notes": notes_text.strip(),
            "assessment_questions": parsed.get("assessment_questions")
            or parsed.get("questions")
            or fallback.assessment_questions,
        }

    if "sections" not in parsed and "modules" in parsed and "textbook_handout_notes" in parsed:
        return {
            "title": parsed.get("title") or f"{request.course.strip().title()} Study Guide",
            "source_type": parsed.get("source_type") or bundle.source_type.value,
            "source_summary": parsed.get("source_summary") or bundle.source_summary,
            "modules": parsed.get("modules"),
            "textbook_handout_notes": parsed.get("textbook_handout_notes"),
            "assessment_questions": parsed.get("assessment_questions") or _build_default_assessment_questions(
                request.course.strip().title(),
                request.prompt.strip()[:90] or request.course.strip().title(),
            ),
        }

    sections = parsed.get("sections")
    if isinstance(sections, list) and sections:
        modules: list[dict[str, str]] = []
        notes_parts: list[str] = []

        for section in sections:
            if not isinstance(section, dict):
                continue

            section_title = (
                section.get("title")
                or section.get("sub_topic")
                or section.get("heading")
                or "Section"
            )
            section_notes = (
                section.get("section_notes")
                or section.get("notes")
                or section.get("content")
                or section.get("body")
                or ""
            )
            if isinstance(section_notes, list):
                section_notes = "\n".join(str(item) for item in section_notes)

            section_notes_text = str(section_notes).strip()
            if not section_notes_text:
                section_notes_text = f"## {section_title}\n\nNo section notes were returned."

            modules.append(
                {
                    "sub_topic": str(section_title).strip(),
                    "section_notes": section_notes_text,
                }
            )
            notes_parts.append(section_notes_text)

        course_title = request.course.strip().title() or "Study Guide"
        subject = request.course.strip().title() or "General Studies"
        topic = request.prompt.strip()[:90] or subject

        return {
            "title": parsed.get("title") or f"{course_title} Study Guide",
            "source_type": parsed.get("source_type") or bundle.source_type.value,
            "source_summary": parsed.get("source_summary") or bundle.source_summary,
            "modules": modules or [{"sub_topic": course_title, "section_notes": f"## {course_title}\n\nNo sections returned."}],
            "textbook_handout_notes": parsed.get("textbook_handout_notes") or "\n\n".join(notes_parts) or f"# {course_title}\n\n{topic}",
            "assessment_questions": parsed.get("assessment_questions")
            or parsed.get("questions")
            or _build_default_assessment_questions(subject, topic),
        }

    return parsed


@lru_cache(maxsize=1)
def _get_agentrouter_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("WRITER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY or WRITER_API_KEY environment variable.")

    return AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
    )


async def run_writer_agent(request: WriterRequestSchema, user_id: str = "course-generator") -> WriterResponseSchema:
    """
    Build a structured study note package from a prompt, PDF, image, or text attachment.
    """
    try:
        bundle: WriterContentBundle = await build_writer_content_bundle(
            prompt=request.prompt,
            helper_material_url=request.helper_material_url,
        )
        user_prompt = build_writer_user_prompt(
            course=request.course,
            prompt=request.prompt,
            source_type=bundle.source_type.value,
            source_summary=bundle.source_summary,
        )

        user_content = [{"type": "text", "text": user_prompt}, *_normalize_message_parts(bundle.contents)]

        client = _get_agentrouter_client()
        response = await client.chat.completions.create(
            model=WRITER_MODEL,
            messages=[
                {"role": "system", "content": build_writer_system_prompt()},
                {"role": "user", "content": user_content},
            ],
            temperature=1.0,
            response_format={"type": "json_object"},
        )

        choice = response.choices[0] if response.choices else None
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)

        if isinstance(content, str) and content.strip():
            raw_json = _strip_code_fences(content)
            parsed = json.loads(raw_json)
            normalized = _coerce_writer_payload(parsed, request=request, bundle=bundle)
            return WriterResponseSchema.model_validate(normalized)

        if isinstance(content, list):
            text_content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            ).strip()
            if text_content:
                raw_json = _strip_code_fences(text_content)
                parsed = json.loads(raw_json)
                normalized = _coerce_writer_payload(parsed, request=request, bundle=bundle)
                return WriterResponseSchema.model_validate(normalized)

        raise RuntimeError("Writer agent returned an empty response.")

    except Exception as exc:
        logger.error(
            "OpenRouter Writer failed; rolling over to local static fallback definitions: %s",
            str(exc),
            exc_info=True,
        )
        fallback = _fallback_syllabus(request.prompt)
        return WriterResponseSchema(
            title=fallback.title,
            source_type=fallback.source_type,
            source_summary=fallback.source_summary,
            modules=[WriterModuleSchema.model_validate(module.model_dump()) for module in fallback.modules],
            textbook_handout_notes=fallback.textbook_handout_notes,
            assessment_questions=fallback.assessment_questions,
        )
    finally:
        logger.info("Writer engine finished for designer instance: %s", user_id)


async def run_syllabus_designer(message: str, user_id: str = "course-generator") -> WriterResponseSchema:
    """
    Backward-compatible wrapper for older call sites that still pass a combined prompt string.
    """
    request = WriterRequestSchema(course="General Studies", prompt=message, helper_material_url=None)
    return await run_writer_agent(request=request, user_id=user_id)
