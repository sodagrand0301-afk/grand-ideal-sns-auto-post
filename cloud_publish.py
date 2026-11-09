from __future__ import annotations

import os
from datetime import datetime, timezone

from instagram_scheduler_api import delete_media, load_rows, load_state, publish, save_state
from x_scheduler_api import XPoster, run_due


def main() -> None:
    """Run both platforms without allowing one credential failure to hide the other."""
    failures = []

    try:
        x = XPoster()
    except Exception as exc:  # noqa: BLE001
        x = None
        print(f"X初期化失敗: {exc}")
        failures.append(("X初期化", str(exc)))

    if x is not None:
        try:
            failures.extend(run_due(x))
        except Exception as exc:  # noqa: BLE001
            print(f"X実行失敗: {exc}")
            failures.append(("X実行", str(exc)))

    rows = load_rows()
    state = load_state()
    now = datetime.now(timezone.utc)
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    changed = False

    for row in rows:
        key = row["post_key"].replace("-X", "-Instagram")
        item = state.setdefault(key, {"status": "planned", "scheduled_at_jst": row["scheduled_at_jst"]})
        if item.get("status") != "planned":
            continue
        scheduled = datetime.fromisoformat(row["scheduled_at_jst"]).astimezone(timezone.utc)
        if scheduled > now:
            continue
        if not token:
            message = "INSTAGRAM_ACCESS_TOKENが未設定です。GitHub Secretsを確認してください。"
            item.update({"status": "error", "error": message, "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
            failures.append((key, message))
            changed = True
            continue
        try:
            replacement_id = item.get("replace_post_id")
            if replacement_id:
                delete_media(replacement_id, token)
            post_id = publish(row, token)
            item.update({"status": "posted", "post_id": post_id, "posted_at_utc": datetime.now(timezone.utc).isoformat(), "error": ""})
            if replacement_id:
                item["replaced_post_id"] = replacement_id
                item.pop("replace_post_id", None)
            print(f"Instagram投稿成功: {key} / 投稿ID {post_id}")
        except Exception as exc:  # noqa: BLE001
            item.update({"status": "error", "error": str(exc)[:500], "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
            print(f"Instagram投稿失敗: {key} / {exc}")
            failures.append((key, str(exc)))
        changed = True

    if changed:
        save_state(state)

    if failures:
        print(f"投稿処理の失敗 {len(failures)}件。GitHub Actionsを失敗扱いにします。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
