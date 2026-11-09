from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
X_FILE = ROOT / "x_schedule_live.json"
IG_CSV_FILE = ROOT / "instagram_schedule_2026-10-05_onward.csv"
IG_STATE_FILE = ROOT / "instagram_schedule_live.json"
QUEUE_FILE = ROOT / "reservation_queue.json"
REPORT_FILE = ROOT / "reservation_maintenance_report.json"
JST = timezone(timedelta(hours=9), "JST")
WIDTH = 1080
HEIGHT = 1440
BRAND = "グランイデアルは不動産に関わる全ての事をサポートいたします。"
LINE = "詳しくは公式LINEまでご相談ください。"
BUFFER_TARGET = 9
BUFFER_REPLENISH_TRIGGER = 6


def now_jst() -> datetime:
    return datetime.now(JST)


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def csv_field(value: str) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)).astimezone(JST)


def load_csv_text() -> str:
    return IG_CSV_FILE.read_text(encoding="utf-8-sig")


def load_ig_rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def write_report(payload: dict) -> None:
    REPORT_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def image_is_valid(image_path: str) -> tuple[bool, str]:
    path = ROOT / image_path
    if not path.is_file():
        path = ROOT / Path(image_path).name
    if not path.is_file():
        return False, f"画像ファイルがありません: {image_path}"
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.size != (WIDTH, HEIGHT):
                return False, f"画像サイズが{WIDTH}x{HEIGHT}pxではありません: {image_path} ({image.size})"
    except Exception as exc:
        return False, f"画像を正常に開けません: {image_path} ({exc})"
    return True, ""


def text_is_valid(text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "本文が空欄です"
    if "画像のポイントは" in text:
        return False, "禁止表現が含まれています"
    if BRAND not in text or LINE not in text:
        return False, "指定案内文が不足しています"
    lines = text.splitlines()
    hashtag_indexes = [i for i, line in enumerate(lines) if line.strip().startswith("#")]
    if not hashtag_indexes or hashtag_indexes[-1] != len(lines) - 1:
        return False, "ハッシュタグが本文の最後にありません"
    if len(text) < 250:
        return False, "本文が短すぎます"
    return True, ""


def planned_date_counts(x_items, ig_rows):
    x_counts: dict[date, int] = {}
    ig_counts: dict[date, int] = {}
    for item in x_items:
        if item.get("status") != "planned":
            continue
        scheduled = parse_dt(item["scheduled_at"])
        if scheduled > now_jst():
            x_counts[scheduled.date()] = x_counts.get(scheduled.date(), 0) + 1
    for row in ig_rows:
        if row.get("status") != "planned":
            continue
        scheduled = parse_dt(row["scheduled_at_jst"])
        if scheduled > now_jst():
            ig_counts[scheduled.date()] = ig_counts.get(scheduled.date(), 0) + 1
    return x_counts, ig_counts


def complete_future_dates(x_items, ig_rows, ig_state) -> list[date]:
    x_counts, ig_counts = planned_date_counts(x_items, ig_rows)
    start = now_jst().date() + timedelta(days=1)
    complete = []
    for offset in range(3):
        target = start + timedelta(days=offset)
        x_ok = x_counts.get(target, 0) == 3
        ig_ok = ig_counts.get(target, 0) == 3
        state_keys = [
            f"{target.isoformat()}-10-00-Instagram-0{i}"
            for i in range(1, 4)
        ]
        state_ok = all(ig_state.get(key, {}).get("status") == "planned" for key in state_keys)
        if x_ok and ig_ok and state_ok:
            complete.append(target)
    return complete


def main() -> None:
    now = now_jst()
    x_items = load_json(X_FILE, [])
    ig_state = load_json(IG_STATE_FILE, {})
    csv_text = load_csv_text()
    ig_rows = load_ig_rows(csv_text)
    queue = load_json(QUEUE_FILE, [])
    if not isinstance(queue, list):
        queue = []

    start = now.date() + timedelta(days=1)
    target_dates = [start + timedelta(days=i) for i in range(3)]
    complete_dates = complete_future_dates(x_items, ig_rows, ig_state)
    missing_dates = [target for target in target_dates if target not in complete_dates]
    used_image_names = {
        Path(str(item.get("image_path", "")).replace("\\\\", "/")).name
        for item in x_items
        if item.get("image_path")
    }
    used_image_names.update(
        Path(str(row.get("image_path", "")).replace("\\\\", "/")).name
        for row in ig_rows
        if row.get("image_path")
    )
    used_keys = {item.get("post_key", "") for item in x_items}
    used_keys.update(row.get("post_key", "") for row in ig_rows)

    validation_errors = []
    valid_queue = []
    seen_queue_images = set()
    seen_queue_texts = set()
    for candidate in queue:
        image_path = str(candidate.get("image_path", "")).replace("\\", "/").lstrip("/")
        text = str(candidate.get("text", ""))
        image_name = Path(image_path).name
        if image_name in seen_queue_images:
            validation_errors.append(f"キュー内画像重複: {image_path}")
            continue
        if text in seen_queue_texts:
            validation_errors.append(f"キュー内本文重複: {image_path}")
            continue
        if Path(image_path).name in used_image_names:
            validation_errors.append(f"既存予約・投稿と画像重複: {image_path}")
            continue
        ok, reason = image_is_valid(image_path)
        if not ok:
            validation_errors.append(reason)
            continue
        ok, reason = text_is_valid(text)
        if not ok:
            validation_errors.append(f"{image_path}: {reason}")
            continue
        seen_queue_images.add(image_name)
        seen_queue_texts.add(text)
        valid_queue.append({"image_path": image_path, "text": text})

    needed = len(missing_dates) * 3
    if needed == 0:
        report = {
            "checked_at_jst": now.isoformat(),
            "status": "ok",
            "target_future_dates": [d.isoformat() for d in target_dates],
            "complete_future_dates": [d.isoformat() for d in complete_dates],
            "added_dates": [],
            "queue_available": len(valid_queue),
            "buffer_target": BUFFER_TARGET,
            "buffer_replenish_trigger": BUFFER_REPLENISH_TRIGGER,
            "buffer_status": "ready" if len(valid_queue) >= BUFFER_TARGET else "low",
            "buffer_shortfall": max(0, BUFFER_TARGET - len(valid_queue)),
            "validation_errors": validation_errors,
        }
        write_report(report)
        print(json.dumps(report, ensure_ascii=False))
        return

    if len(valid_queue) < needed:
        report = {
            "checked_at_jst": now.isoformat(),
            "status": "blocked_missing_assets",
            "target_future_dates": [d.isoformat() for d in target_dates],
            "complete_future_dates": [d.isoformat() for d in complete_dates],
            "missing_dates": [d.isoformat() for d in missing_dates],
            "required_queue_items": needed,
            "queue_available": len(valid_queue),
            "buffer_target": BUFFER_TARGET,
            "buffer_replenish_trigger": BUFFER_REPLENISH_TRIGGER,
            "buffer_status": "blocked",
            "buffer_shortfall": max(0, BUFFER_TARGET - len(valid_queue)),
            "validation_errors": validation_errors,
            "message": "予約候補の予備が不足しているため、重複防止のため予約登録を行いません。画像・本文を3件単位で補充してください。",
        }
        write_report(report)
        print("::warning::" + report["message"])
        print(json.dumps(report, ensure_ascii=False))
        return

    csv_rows = []
    added_dates = []
    cursor = 0
    for target in missing_dates:
        scheduled = f"{target.isoformat()}T10:00:00+09:00"
        added_dates.append(target.isoformat())
        for index in range(1, 4):
            candidate = valid_queue[cursor]
            cursor += 1
            image_path = candidate["image_path"]
            text = candidate["text"]
            x_key = f"{target.isoformat()}-10-00-X-0{index}"
            ig_key = f"{target.isoformat()}-10-00-Instagram-0{index}"
            if x_key in used_keys or ig_key in used_keys:
                raise RuntimeError(f"予約キー重複を検出したため停止: {x_key} / {ig_key}")
            x_items.append(
                {
                    "post_key": x_key,
                    "scheduled_at": scheduled,
                    "image_path": image_path,
                    "text": text,
                    "status": "planned",
                    "error": "",
                    "post_id": "",
                }
            )
            ig_state[ig_key] = {
                "status": "planned",
                "scheduled_at_jst": scheduled,
                "image_path": image_path,
                "text": text,
                "error": "",
            }
            csv_rows.append(
                ",".join(
                    csv_field(value)
                    for value in (ig_key, scheduled, "Instagram", image_path, text, "planned", "")
                )
            )

    remaining_queue = valid_queue[cursor:]
    new_csv = csv_text.rstrip("\r\n") + "\r\n" + "\r\n".join(csv_rows) + "\r\n"
    X_FILE.write_text(json.dumps(x_items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    IG_STATE_FILE.write_text(json.dumps(ig_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    IG_CSV_FILE.write_text(new_csv, encoding="utf-8-sig")
    QUEUE_FILE.write_text(json.dumps(remaining_queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "checked_at_jst": now.isoformat(),
        "status": "added",
        "target_future_dates": [d.isoformat() for d in target_dates],
        "complete_future_dates_before": [d.isoformat() for d in complete_dates],
        "added_dates": added_dates,
        "added_items_per_platform": len(csv_rows),
        "queue_remaining": len(remaining_queue),
        "buffer_target": BUFFER_TARGET,
        "buffer_replenish_trigger": BUFFER_REPLENISH_TRIGGER,
        "buffer_status": "ready" if len(remaining_queue) >= BUFFER_TARGET else "low",
        "buffer_shortfall": max(0, BUFFER_TARGET - len(remaining_queue)),
        "validation_errors": validation_errors,
    }
    write_report(report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
