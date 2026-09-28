from __future__ import annotations

import csv
import getpass
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import truststore

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent
CSV_FILE = ROOT / "本番投稿台帳_2026-09-27_10-02.csv"
STATE_FILE = ROOT / "instagram_schedule_live.json"
IG_USER_ID = "17841431756345101"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
IMAGE_PROXY_BASE_URL = "https://external-content.duckduckgo.com/iu/?u="


def api_url(path: str) -> str:
    return f"https://graph.instagram.com/{path.lstrip('/')}"


def safe_get(*args, **kwargs):
    try:
        return requests.get(*args, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError("ネットワーク接続に失敗しました（トークンは表示しません）。") from exc


def safe_post(*args, **kwargs):
    try:
        return requests.post(*args, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError("ネットワーク接続に失敗しました（トークンは表示しません）。") from exc


def load_rows() -> list[dict[str, str]]:
    with CSV_FILE.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_state() -> dict[str, dict]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict[str, dict]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def caption(row: dict[str, str]) -> str:
    return row["text"].strip()


def publish(row: dict[str, str], token: str) -> str:
    image_name = Path(row["image_path"].replace("/", "\\")).name
    image_name = Path(image_name).with_suffix(".jpg").name
    source_url = f"https://raw.githubusercontent.com/sodagrand0301-afk/grand-ideal-sns-auto-post/main/instagram_media_jpg/{image_name}"
    image_url = f"{IMAGE_PROXY_BASE_URL}{quote(source_url, safe='')}&f=1"
    created = safe_post(
        api_url(f"{IG_USER_ID}/media"),
        data={"image_url": image_url, "caption": caption(row), "access_token": token},
        timeout=45,
    )
    if not created.ok:
        raise RuntimeError(f"メディア作成失敗 HTTP {created.status_code}: {created.text[:300]}")
    container_id = created.json()["id"]

    for _ in range(24):
        status = safe_get(
            api_url(container_id),
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )
        if not status.ok:
            raise RuntimeError(f"メディア状態確認失敗 HTTP {status.status_code}: {status.text[:300]}")
        code = status.json().get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"メディア処理失敗: {status.json()}")
        time.sleep(5)
    else:
        raise RuntimeError("メディア処理が時間内に完了しませんでした。")

    published = safe_post(
        api_url(f"{IG_USER_ID}/media_publish"),
        data={"creation_id": container_id, "access_token": token},
        timeout=45,
    )
    if not published.ok:
        raise RuntimeError(f"Instagram投稿失敗 HTTP {published.status_code}: {published.text[:300]}")
    return published.json().get("id", "")


def main() -> None:
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN") or getpass.getpass("Instagramアクセストークン: ").strip()
    if not token:
        raise SystemExit("アクセストークンが空です。")
    rows = load_rows()
    state = load_state()
    print(f"Instagram予約API起動: {len(rows)}件 / 毎日10:00・14:00・18:00（日本時間）")
    while True:
        now = datetime.now(timezone.utc)
        changed = False
        for row in rows:
            key = row["post_key"].replace("-X", "-Instagram")
            item = state.setdefault(key, {"status": "planned", "scheduled_at_jst": row["scheduled_at_jst"]})
            if item.get("status") == "posted":
                continue
            scheduled = datetime.fromisoformat(row["scheduled_at_jst"]).astimezone(timezone.utc)
            if scheduled > now:
                continue
            try:
                post_id = publish(row, token)
                item.update({"status": "posted", "post_id": post_id, "posted_at_utc": datetime.now(timezone.utc).isoformat()})
                print(f"投稿成功: {key} / 投稿ID {post_id}")
            except Exception as exc:
                item.update({"status": "error", "error": str(exc), "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
                print(f"投稿失敗: {key} / {exc}")
            changed = True
            save_state(state)
        if changed:
            save_state(state)
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Instagram予約APIを停止しました。")
