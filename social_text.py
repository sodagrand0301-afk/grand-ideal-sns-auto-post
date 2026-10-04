from __future__ import annotations

import re


OFFICIAL_LINE_CTA = "詳しくは公式LINEまでご相談ください。"


def format_social_text(text: str, max_chars: int | None = None) -> str:
    """読みやすい改行と公式LINE案内を付けて投稿文を整える。"""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in cleaned.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line and line != OFFICIAL_LINE_CTA:
            lines.append(line)

    hashtag_lines = [line for line in lines if line.startswith("#")]
    body_lines = [line for line in lines if not line.startswith("#")]
    body = "\n".join(body_lines).strip()
    hashtags = "\n".join(hashtag_lines).strip()
    parts = [part for part in (body, hashtags, OFFICIAL_LINE_CTA) if part]
    result = "\n\n".join(parts)

    if max_chars is not None and len(result) > max_chars:
        suffix_parts = [part for part in (hashtags, OFFICIAL_LINE_CTA) if part]
        suffix = "\n\n".join(suffix_parts)
        available = max_chars - len(suffix) - 2
        if available <= 0:
            return OFFICIAL_LINE_CTA[:max_chars]
        shortened = body[: max(1, available - 1)].rstrip() + "…"
        result = f"{shortened}\n\n{suffix}"

    return result
