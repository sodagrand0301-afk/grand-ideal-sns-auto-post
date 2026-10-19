from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    RULES = json.loads((ROOT / "posting_rules.json").read_text(encoding="utf-8"))
except FileNotFoundError:
    RULES = {
        "image": {"width": 1080, "height": 1440},
        "caption": {
            "minimum_characters": 250,
            "forbidden_phrases": ["画像のポイントは"],
            "minimum_hashtags": 5,
            "brand_cta": "グランイデアルでは購入前の整理をサポートします。",
            "line_cta": "詳しくは公式LINEまでご相談ください。",
        },
    }
IMAGE_WIDTH = RULES["image"]["width"]
IMAGE_HEIGHT = RULES["image"]["height"]
BRAND_CTA = RULES["caption"]["brand_cta"]
OFFICIAL_LINE_CTA = RULES["caption"]["line_cta"]
FORBIDDEN_PHRASES = tuple(RULES["caption"]["forbidden_phrases"])
VOLUME_SUPPORT_LINES = (
    "審査では一つの数字だけで判断せず、収入・借入・支出のバランスを確認することが大切です。",
    "早い段階で整理しておくと、無理のない返済額や必要な準備が見えやすくなります。",
)


def format_social_text(text: str, max_chars: int | None = None) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in cleaned.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line and line != OFFICIAL_LINE_CTA and line != BRAND_CTA:
            lines.append(line)
    body_lines = [line for line in lines if not line.startswith("#") and "公式LINEまでご相談ください" not in line and not line.startswith("グランイデアルでは") and not line.startswith("グランイデアルが") and line not in VOLUME_SUPPORT_LINES]
    hashtag_lines = [line for line in lines if line.startswith("#")]
    body = "\n".join(body_lines).strip()
    if len(body) < RULES["caption"]["minimum_characters"]:
        body = f"{body}\n\n" + "\n".join(VOLUME_SUPPORT_LINES)
    hashtags = " ".join(hashtag_lines).strip()
    result = "\n\n".join(part for part in (body, BRAND_CTA, OFFICIAL_LINE_CTA, hashtags) if part)
    if max_chars is not None and len(result) > max_chars:
        suffix = "\n\n".join(part for part in (BRAND_CTA, OFFICIAL_LINE_CTA, hashtags) if part)
        available = max_chars - len(suffix) - 2
        if available <= 0:
            return OFFICIAL_LINE_CTA[:max_chars]
        result = f"{body[:max(1, available - 1)].rstrip()}…\n\n{suffix}"
    return result


def validate_caption(text: str, max_chars: int) -> list[str]:
    errors = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    minimum = RULES["caption"]["minimum_characters"]
    if len(normalized) < minimum:
        errors.append(f"本文が短すぎます（{len(normalized)}文字）")
    if len(normalized) > max_chars:
        errors.append(f"本文が上限超過です（{len(normalized)}文字 / 上限{max_chars}）")
    for phrase in FORBIDDEN_PHRASES:
        if phrase in normalized:
            errors.append(f"禁止表現が含まれています: {phrase}")
    if BRAND_CTA not in normalized or OFFICIAL_LINE_CTA not in normalized:
        errors.append("固定CTAが不足しています")
    hashtags = re.findall(r"(?<!\S)#\S+", normalized)
    minimum_hashtags = RULES["caption"]["minimum_hashtags"]
    if len(hashtags) < minimum_hashtags:
        errors.append(f"ハッシュタグが{minimum_hashtags}個未満です")
    footer = f"{BRAND_CTA}\n\n{OFFICIAL_LINE_CTA}\n\n"
    if footer not in normalized:
        errors.append("CTAの順序または空行がルールと異なります")
    if hashtags and normalized.rfind(hashtags[0]) < normalized.find(OFFICIAL_LINE_CTA):
        errors.append("ハッシュタグが公式LINE案内より前にあります")
    return errors


def validate_image_dimensions(path: Path) -> list[str]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            if image.size != (IMAGE_WIDTH, IMAGE_HEIGHT):
                return [f"画像サイズが{IMAGE_WIDTH}×{IMAGE_HEIGHT}pxではありません: {image.size[0]}×{image.size[1]}px"]
    except Exception as exc:
        return [f"画像サイズを確認できません: {exc}"]
    return []


def validate_post(row: dict[str, str], root: Path, max_chars: int) -> list[str]:
    errors = validate_caption(format_social_text(row.get("text", ""), max_chars=max_chars), max_chars)
    image_path = root / row["image_path"]
    if not image_path.is_file():
        image_path = root / Path(row["image_path"]).name
    if not image_path.is_file():
        errors.append(f"画像が見つかりません: {row.get('image_path', '')}")
    else:
        errors.extend(validate_image_dimensions(image_path))
    return errors
