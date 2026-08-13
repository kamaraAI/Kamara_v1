from __future__ import annotations

import base64
import io
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from pypdf import PdfReader

from .schemas import WriterContentBundle, WriterSourceType

load_dotenv()

IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".rtf",
    ".html",
    ".htm",
}


def _guess_extension(url: str, content_type: str | None) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()

    if suffix:
        return suffix

    if content_type:
        if "pdf" in content_type:
            return ".pdf"
        if content_type.startswith("image/"):
            return ".png"
        if content_type.startswith("text/"):
            return ".txt"

    return ""


async def _download_remote_source(url: str) -> tuple[bytes, str | None]:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content, response.headers.get("content-type")


def _build_text_bundle(prompt: str, helper_text: str | None = None) -> WriterContentBundle:
    source_summary = "Prompt-only request from the student."

    if helper_text:
        source_summary = "Prompt plus extracted text attachment."
        return WriterContentBundle(
            source_type=WriterSourceType.mixed,
            source_summary=source_summary,
            contents=[{"type": "text", "text": helper_text}],
        )

    return WriterContentBundle(
        source_type=WriterSourceType.prompt,
        source_summary=source_summary,
        contents=[],
    )


def _build_image_bundle(source_name: str, raw_bytes: bytes, content_type: str | None) -> WriterContentBundle:
    mime_type = content_type or "image/png"
    data_url = f"data:{mime_type};base64,{base64.b64encode(raw_bytes).decode('ascii')}"

    return WriterContentBundle(
        source_type=WriterSourceType.mixed,
        source_summary=f"Image attachment: {source_name}",
        contents=[
            {
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                },
            }
        ],
    )


def _extract_pdf_text(raw_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        page_text: list[str] = []

        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                page_text.append(text.strip())

        extracted = "\n\n".join(page_text).strip()
        return extracted[:120_000]
    except Exception:
        return ""


async def build_writer_content_bundle(prompt: str, helper_material_url: str | None = None) -> WriterContentBundle:
    if not helper_material_url:
        return _build_text_bundle(prompt)

    raw_bytes, content_type = await _download_remote_source(helper_material_url)
    source_name = helper_material_url.rsplit("/", 1)[-1] or "attached material"
    suffix = _guess_extension(helper_material_url, content_type)

    if suffix in IMAGE_EXTENSIONS:
        return _build_image_bundle(source_name, raw_bytes, IMAGE_EXTENSIONS[suffix])

    if suffix == ".pdf" or content_type == "application/pdf":
        extracted_text = _extract_pdf_text(raw_bytes)
        if extracted_text:
            return WriterContentBundle(
                source_type=WriterSourceType.mixed,
                source_summary=f"PDF attachment: {source_name}",
                contents=[{"type": "text", "text": extracted_text}],
            )

        return WriterContentBundle(
            source_type=WriterSourceType.mixed,
            source_summary=f"PDF attachment: {source_name} (text extraction unavailable)",
            contents=[],
        )

    if suffix in TEXT_EXTENSIONS or (content_type and content_type.startswith("text/")):
        try:
            helper_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            helper_text = raw_bytes.decode("utf-8", errors="ignore")

        return _build_text_bundle(prompt, helper_text)

    return _build_text_bundle(prompt)
