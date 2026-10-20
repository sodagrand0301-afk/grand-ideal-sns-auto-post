from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from instagram_scheduler_api import load_rows, load_state, publish, save_state
from x_scheduler_api import JST, ROOT, XPoster, load_items, run_due, save_items

NEW_SUPPORT = "グランイデアルは不動産に関わる全ての事をサポートいたします。"
OLD_SUPPORT = (
    "グランイデアルでは購入前の整理をサポートします。",
    "グランイデアルでは状況を一つずつ整理して相談をお受けします。",
)


def revised(text: str) -> str:
    for old in OLD_SUPPORT:
        text = text.replace(old, NEW_SUPPORT)
    return text


def delete_x(post_id: str, poster: XPoster) -> None:
    headers = {}
    auth = poster.auth
    if poster.oauth2_access_token:
        headers["Authorization"] = f"Bearer {poster.oauth2_access_token}"
        auth = None
    response = requests.delete(
        f"https://api.x.com/2/tweets/{post_id}",
        headers=headers,
        auth=auth,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"X削除失敗 HTTP {response.status_code}: {response.text[:300]}")


def delete_instagram(post_id: str, token: str) -> None:
    response = requests.delete(
        f"https://graph.instagram.com/{post_id}",
        params={"access_token": token},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Instagram削除失敗 HTTP {response.status_code}: {response.text[:300]}")


def repost_today(date_text: str) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    poster = XPoster()
    x_items = load_items()
    for item in x_items:
        key = item.get("post_key", "")
        if not key.startswith(date_text + "-") or not key.endswith("-X") or item.get("status") != "posted":
            continue
        try:
            old_id = item.get("post_id")
            if not old_id:
                raise RuntimeError("既存X投稿IDがありません")
            delete_x(old_id, poster)
            item["text"] = revised(item.get("text", ""))
            image_path = ROOT / item["image_path"]
            if not image_path.is_file():
                image_path = ROOT / Path(item["image_path"]).name
            new_id = poster.post(image_path, item["text"])
            item.update({"status": "posted", "post_id": new_id, "posted_at": datetime.now(JST).isoformat(), "error": "", "reposted_from": old_id})
            print(f"X再投稿成功: {key} / 旧ID {old_id} / 新ID {new_id}")
        except Exception as exc:
            item.update({"status": "error", "error": str(exc)[:500]})
            failures.append((key, str(exc)))
            print(f"X再投稿失敗: {key} / {exc}")
    save_items(x_items)

    token = os.environ["INSTAGRAM_ACCESS_TOKEN"]
    rows = load_rows()
    state = load_state()
    for row in rows:
        x_key = row["post_key"]
        if not x_key.startswith(date_text + "-"):
            continue
        key = x_key.replace("-X", "-Instagram")
        item = state.get(key, {})
        if item.get("status") != "posted":
            continue
        try:
            old_id = item.get("post_id")
            if not old_id:
                raise RuntimeError("既存Instagram投稿IDがありません")
            delete_instagram(old_id, token)
            row["text"] = revised(row.get("text", ""))
            new_id = publish(row, token)
            item.update({"status": "posted", "post_id": new_id, "posted_at_utc": datetime.now(timezone.utc).isoformat(), "error": "", "reposted_from": old_id})
            print(f"Instagram再投稿成功: {key} / 旧ID {old_id} / 新ID {new_id}")
        except Exception as exc:
            item.update({"status": "error", "error": str(exc)[:500], "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
            failures.append((key, str(exc)))
            print(f"Instagram再投稿失敗: {key} / {exc}")
        state[key] = item
    save_state(state)
    return failures


def normal_publish() -> list[tuple[str, str]]:
    failures = []
    x = XPoster()
    failures.extend(run_due(x))
    rows = load_rows()
    state = load_state()
    now = datetime.now(timezone.utc)
    changed = False
    for row in rows:
        key = row["post_key"].replace("-X", "-Instagram")
        item = state.setdefault(key, {"status": "planned", "scheduled_at_jst": row["scheduled_at_jst"]})
        if item.get("status") in {"posted", "cancelled"}:
            continue
        scheduled = datetime.fromisoformat(row["scheduled_at_jst"]).astimezone(timezone.utc)
        if scheduled > now:
            continue
        try:
            post_id = publish(row, os.environ["INSTAGRAM_ACCESS_TOKEN"])
            item.update({"status": "posted", "post_id": post_id, "posted_at_utc": datetime.now(timezone.utc).isoformat(), "error": ""})
            print(f"Instagram投稿成功: {key} / 投稿ID {post_id}")
        except Exception as exc:
            item.update({"status": "error", "error": str(exc)[:500], "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
            failures.append((key, str(exc)))
            print(f"Instagram投稿失敗: {key} / {exc}")
        changed = True
    if changed:
        save_state(state)
    return failures


def main() -> None:
    if os.getenv("REPOST_TODAY") == "1":
        failures = repost_today(os.getenv("REPOST_DATE", datetime.now(JST).date().isoformat()))
    else:
        failures = normal_publish()
    if failures:
        print(f"投稿失敗 {len(failures)}件。GitHub Actionsを失敗扱いにします。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
