from __future__ import annotations
import os
from datetime import datetime, timezone
from instagram_scheduler_api import load_rows, load_state, publish, save_state
from x_scheduler_api import XPoster, run_due
def main() -> None:
    """One-shot cloud job. Credentials come only from environment variables."""
    failures = []
    failures.extend(run_due(XPoster()))
    rows = load_rows()
    state = load_state()
    now = datetime.now(timezone.utc)
    changed = False
    token = os.environ["INSTAGRAM_ACCESS_TOKEN"]
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
            item.update({"status": "posted", "post_id": post_id, "posted_at_utc": datetime.now(timezone.utc).isoformat(), "error": ""})
            print(f"Instagram投稿成功: {key} / 投稿ID {post_id}")
        except Exception as exc:
            message = str(exc)[:500]
            item.update({"status": "error", "error": message, "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
            failures.append((key, message))
            print(f"Instagram投稿失敗: {key} / {message}")
        changed = True
    if changed:
        save_state(state)
    if failures:
        print(f"投稿失敗 {len(failures)}件。GitHub Actionsを失敗扱いにします。")
        raise SystemExit(1)
if __name__ == "__main__":
    main()
