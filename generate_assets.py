from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "reservation_queue.json"
TERMS_FILE = ROOT / "search_terms.json"
USED_TERMS_FILE = ROOT / "generated_terms.json"
IMAGE_DIR = ROOT / "SNS投稿用画像" / "generated"
JST = timezone(timedelta(hours=9), "JST")
WIDTH, HEIGHT = 1080, 1440
TARGET = 9
BATCH = 3
BRAND = "株式会社グランイデアル"
LINE = "詳しくは公式LINEへ"


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).lower()


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansJP-Bold.otf" if bold else "/usr/share/fonts/opentype/noto/NotoSansJP-Regular.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def clean_term(term: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", term)).strip("？?、。")


def allowed(term: str) -> bool:
    normalized = normalize(term)
    if not normalized or len(normalized) < 4:
        return False
    if "アロックホーム" in normalized or "電話" in normalized:
        return False
    if any(word in normalized for word in ("絶対", "必ず通る", "誰でも通る")):
        return False
    return any(word in normalized for word in ("住宅", "ローン", "家", "マイホーム"))


def checks_for(term: str) -> list[str]:
    n = normalize(term)
    if any(k in n for k in ("中古", "一戸建て", "戸建て")):
        return ["物件の築年数", "担保評価の確認", "修繕費の見込み"]
    if any(k in n for k in ("借金", "借入", "まとめ", "おまとめ")):
        return ["借入先と残高", "毎月の返済額", "返済後の生活費"]
    if any(k in n for k in ("年収", "低収入", "所得")):
        return ["収入の見通し", "希望借入額", "生活費とのバランス"]
    if any(k in n for k in ("パート", "シングル", "母子", "女性")):
        return ["勤続年数", "収入の継続性", "毎月の返済額"]
    if any(k in n for k in ("審査", "通ら", "落ち", "異動", "信用")):
        return ["信用情報", "収入資料", "既存借入"]
    return ["希望借入額", "自己資金の範囲", "毎月返済できる額"]


def wrap_title(title: str) -> str:
    if len(title) <= 14:
        return title
    midpoint = max(6, min(14, len(title) // 2))
    return title[:midpoint] + "\n" + title[midpoint:]


def caption_for(term: str, checks: list[str]) -> str:
    topic = clean_term(term)
    lines = [
        f"「{topic}」を調べるとき、何を確認すればよい？",
        f"住宅ローンの検討では、{checks[0]}を最初に整理します。",
        f"次に、{checks[1]}を資料や数字で確認します。",
        f"さらに、{checks[2]}を含めて返済後の家計を考えます。",
        "住宅ローン審査は、検索語句や年収だけで結果が決まるものではありません。",
        "借りられる額と、無理なく返せる額は分けて考える必要があります。",
        f"まずは{checks[0]}・{checks[1]}・{checks[2]}を一覧にしてください。",
        "希望条件だけで判断せず、購入後の返済計画と資金計画を確認しましょう。",
        "必要書類や審査基準は、金融機関や個人の状況により異なります。",
        "申込み前に数字を整理し、気になる点を相談できる状態にしておきます。",
        BRAND + "は不動産に関わる全ての事をサポートいたします。",
        "詳しくは公式LINEまでご相談ください。",
        "",
        "#住宅ローン #住宅ローン審査 #返済計画 #資金計画 #既存借入 #住宅購入 #グランイデアル",
    ]
    return "\n".join(lines)


def draw_image(term: str, checks: list[str], output: Path, sequence: int) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#eaf6ff")
    draw = ImageDraw.Draw(image)
    # Bright sky and a simple house illustration; no external image or person is used.
    for y in range(HEIGHT):
        r = int(235 - 25 * y / HEIGHT)
        g = int(249 - 18 * y / HEIGHT)
        b = int(255 - 8 * y / HEIGHT)
        draw.line((0, y, WIDTH, y), fill=(r, g, b))
    draw.ellipse((760, 120, 930, 290), fill="#fff5a8")
    draw.rectangle((0, 505, WIDTH, 1160), fill="#dff2e5")
    draw.polygon([(30, 760), (225, 580), (420, 760)], fill="#2b78b8")
    draw.rectangle((70, 755, 380, 1050), fill="#fffdf6", outline="#16528b", width=5)
    draw.rectangle((200, 860, 285, 1050), fill="#c97a42", outline="#16528b", width=4)
    draw.rectangle((110, 820, 175, 885), fill="#a9ddf5", outline="#16528b", width=4)
    draw.rectangle((300, 820, 365, 885), fill="#a9ddf5", outline="#16528b", width=4)
    draw.rectangle((470, 720, 1040, 1160), fill="#f5fbff", outline="#1d6da8", width=4)
    draw.rounded_rectangle((35, 35, 1045, 545), radius=28, fill="#ffffff", outline="#b6d8ef", width=5)
    draw.rounded_rectangle((70, 70, 680, 132), radius=24, fill="#e9345b")
    draw.text((375, 101), "住宅ローン審査のリアル", font=font(34, True), fill="white", anchor="mm")
    title = wrap_title(clean_term(term))
    draw.multiline_text((75, 170), title, font=font(66, True), fill="#073b82", spacing=8)
    draw.text((78, 420), "検索語句から確認項目を整理", font=font(31, True), fill="#096da9")
    draw.rounded_rectangle((50, 610, 1030, 1215), radius=30, fill="#f4fbff", outline="#2588c2", width=5)
    draw.rounded_rectangle((90, 650, 500, 720), radius=22, fill="#073b82")
    draw.text((295, 685), "確認したい項目", font=font(34, True), fill="white", anchor="mm")
    y = 770
    for check in checks:
        draw.rounded_rectangle((90, y, 990, y + 112), radius=18, fill="white", outline="#c7deef", width=3)
        draw.text((145, y + 55), "✓", font=font(46, True), fill="#f3bd00", anchor="mm")
        draw.text((215, y + 56), check, font=font(34, True), fill="#073b82", anchor="lm")
        y += 130
    draw.rounded_rectangle((70, 1280, 1010, 1374), radius=30, fill="#ff7117")
    draw.text((540, 1327), LINE, font=font(42, True), fill="white", anchor="mm")
    draw.text((540, 1412), BRAND, font=font(23, True), fill="#073b82", anchor="mm")
    image.save(output, format="JPEG", quality=94, optimize=True)


def main() -> None:
    queue = load_json(QUEUE_FILE, [])
    if not isinstance(queue, list):
        queue = []
    needed = max(0, TARGET - len(queue))
    if needed == 0:
        print(json.dumps({"status": "ready", "queue_available": len(queue), "generated": 0}, ensure_ascii=False))
        return
    count = ((needed + BATCH - 1) // BATCH) * BATCH
    terms = load_json(TERMS_FILE, [])
    state = load_json(USED_TERMS_FILE, {"next_index": 0, "used_terms": []})
    next_index = int(state.get("next_index", 0))
    used_terms = {normalize(x) for x in state.get("used_terms", [])}
    selected = []
    while next_index < len(terms) and len(selected) < count:
        term = str(terms[next_index]).strip()
        next_index += 1
        n = normalize(term)
        if n in used_terms or not allowed(term):
            continue
        selected.append(term)
        used_terms.add(n)
    if len(selected) < count:
        raise RuntimeError(f"未使用の検索語句が不足しています: 必要={count}, 選択={len(selected)}")
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(JST).date().isoformat()
    generated = []
    for offset, term in enumerate(selected, 1):
        checks = checks_for(term)
        safe = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥ー]+", "", clean_term(term))[:28]
        name = f"{today}_自動生成_{len(queue)+offset:02d}_{safe}.jpg"
        path = IMAGE_DIR / name
        draw_image(term, checks, path, len(queue) + offset)
        with Image.open(path) as check_image:
            if check_image.size != (WIDTH, HEIGHT):
                raise RuntimeError(f"生成画像サイズ不正: {path} {check_image.size}")
        generated.append({"image_path": f"SNS投稿用画像/generated/{name}", "text": caption_for(term, checks)})
    queue.extend(generated)
    QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    USED_TERMS_FILE.write_text(json.dumps({"next_index": next_index, "used_terms": sorted(used_terms)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "generated", "queue_available": len(queue), "generated": len(generated), "terms": selected}, ensure_ascii=False))


if __name__ == "__main__":
    main()

