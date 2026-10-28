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
from social_text import format_social_text, validate_post


truststore.inject_into_ssl()


ROOT = Path(__file__).resolve().parent
CSV_FILE = ROOT / "instagram_schedule_2026-10-05_onward.csv"
STATE_FILE = ROOT / "instagram_schedule_live.json"
IG_USER_ID = "17841431756345101"
# クラウド実行時はGitHub等の永続HTTPS URLを環境変数で指定します。
# GitHub Pages/Cloudflare is optional. The raw GitHub URL is a stable public
# fallback that Meta can fetch without a local tunnel.
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://raw.githubusercontent.com/sodagrand0301-afk/grand-ideal-sns-auto-post/main",
)


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


def safe_delete(*args, **kwargs):
    try:
        return requests.delete(*args, **kwargs)
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
    text = format_social_text(row["text"], max_chars=2200)
    errors = validate_post({**row, "text": text}, ROOT, 2200)
    if errors:
        raise RuntimeError("投稿前検査エラー: " + " / ".join(errors))
    return text


def delete_media(media_id: str, token: str) -> None:
    response = safe_delete(api_url(media_id), data={"access_token": token}, timeout=45)
    if response.status_code == 404:
        return
    if response.status_code == 400:
        try:
            error = response.json().get("error", {})
            # Meta API may report an already-deleted/inaccessible object as code 100/subcode 33.
            # Treat it as absent so a manual UI deletion does not block the replacement post.
            if error.get("code") == 100 and error.get("error_subcode") == 33:
                return
        except ValueError:
            pass
    if not response.ok:
        raise RuntimeError(f"旧Instagram投稿の削除失敗 HTTP {response.status_code}: {response.text[:300]}")


def publish(row: dict[str, str], token: str) -> str:
    image_ref = row["image_path"].replace("\\", "/").lstrip("/")
    image_url = row["image_path"] if row["image_path"].startswith("http") else f"{PUBLIC_BASE_URL.rstrip('/')}/{quote(image_ref, safe='/')}"
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

    # Meta may briefly report that a finished container is not yet publishable.
    # Retry only that transient condition; never retry a completed publish.
    for attempt in range(3):
        published = safe_post(
            api_url(f"{IG_USER_ID}/media_publish"),
            data={"creation_id": container_id, "access_token": token},
            timeout=45,
        )
        if published.ok:
            return published.json().get("id", "")
        try:
            error = published.json().get("error", {})
        except ValueError:
            error = {}
        transient_not_ready = (
            published.status_code == 400
            and error.get("code") == 9007
            and error.get("error_subcode") == 2207027
        )
        if not transient_not_ready or attempt == 2:
            raise RuntimeError(f"Instagram投稿失敗 HTTP {published.status_code}: {published.text[:300]}")
        time.sleep(15)


def main() -> None:
    if PUBLIC_BASE_URL.startswith("REPLACE_"):
        raise SystemExit("PUBLIC_BASE_URLが未設定です。公開HTTPS画像URLを設定してください。")
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN") or getpass.getpass("Instagramアクセストークン: ").strip()
    if not token:
        raise SystemExit("アクセストークンが空です。")
    rows = load_rows()
    state = load_state()
    print(f"Instagram予約API起動: {len(rows)}件 / 毎日10:00（日本時間）")
    print("このPowerShellとパソコンをスリープさせないでください。終了はCtrl+Cです。")
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

