from __future__ import annotations

import os
from datetime import datetime, timezone

from instagram_scheduler_api import load_rows, load_state, publish, save_state
from x_scheduler_api import XPoster, load_items, run_due, save_items


def main() -> None:
    """One-shot cloud job. Credentials come only from environment variables."""
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
            post_id = publish(row, __import__("os").environ["INSTAGRAM_ACCESS_TOKEN"])
            item.update({"status": "posted", "post_id": post_id, "posted_at_utc": datetime.now(timezone.utc).isoformat(), "error": ""})
            print(f"Instagram投稿成功: {key} / 投稿ID {post_id}")
        except Exception as exc:
            item.update({"status": "error", "error": str(exc)[:500], "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
            print(f"Instagram投稿失敗: {key} / {exc}")
            failures.append((key, str(exc)))
        changed = True
    if changed:
        save_state(state)
    if failures:
        print(f"投稿失敗 {len(failures)}件。GitHub Actionsを失敗扱いにします。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
