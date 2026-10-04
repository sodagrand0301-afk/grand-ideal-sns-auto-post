from __future__ import annotations

import os
from datetime import datetime, timezone

from instagram_scheduler_api import load_rows, load_state, publish, save_state
from x_scheduler_api import XPoster, load_items, run_due, save_items


def main() -> None:
    failures = []
    x = XPoster()
    x_failures = run_due(x)
    for key, error in x_failures:
        print("X投稿失敗: %s / %s" % (key, error), flush=True)
    failures.extend(x_failures)

    rows = load_rows()
    state = load_state()
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
            post_id = publish(row, os.environ["INSTAGRAM_ACCESS_TOKEN"])
            item.update({"status": "posted", "post_id": post_id, "posted_at_utc": datetime.now(timezone.utc).isoformat(), "error": ""})
            print("Instagram投稿成功: %s / 投稿ID %s" % (key, post_id), flush=True)
        except Exception as exc:
            error = str(exc)[:500]
            item.update({"status": "error", "error": error, "last_attempt_utc": datetime.now(timezone.utc).isoformat()})
            print("Instagram投稿失敗: %s / %s" % (key, error), flush=True)
            failures.append((key, error))
        changed = True
    if changed:
        save_state(state)
    if failures:
        print("投稿失敗 %d件。GitHub Actionsを失敗扱いにします。" % len(failures), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
