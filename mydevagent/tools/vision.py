"""Optional multimodal support: screenshot/mockup → technical description for the agents (`vision` tier model)."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

VISION_PROMPT = (
    "You are the eyes of a software team. Describe this image precisely for an engineer. "
    "If it is a UI/mockup: layout (top→bottom, left→right), components, text/copy verbatim, colors (hex if "
    "possible), spacing, states. If it is an error/screenshot of code or terminal: transcribe the exact error "
    "text and relevant code. If it is a diagram: entities and relations. Max 250 words, bullets."
)


def image_to_data_url(path: str | Path) -> str:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def describe_images(llm, images: list[str], hint: str = "") -> str:
    """`images`: data URLs or http(s) URLs. Returns a text description (empty on failure)."""
    if not images:
        return ""
    content: list[dict] = [{"type": "text", "text": VISION_PROMPT + (f"\nUser request: {hint[:500]}" if hint else "")}]
    content += [{"type": "image_url", "image_url": {"url": url}} for url in images[:4]]
    try:
        result = llm.complete([{"role": "user", "content": content}], tier="vision", max_tokens=500,
                              temperature=0.1)
        return result.text.strip()
    except Exception as exc:
        return f"(vision model unavailable: {type(exc).__name__}; configure the `vision` tier)"
