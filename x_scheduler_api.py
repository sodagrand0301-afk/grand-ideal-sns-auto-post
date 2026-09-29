import argparse
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from getpass import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
import truststore
from requests_oauthlib import OAuth1

truststore.inject_into_ssl()
ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "x_schedule_live.json"
JST = timezone(timedelta(hours=9), "JST")
LOCK = threading.Lock()


def load_items():
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_items(items):
    temp = DATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(DATA_FILE)


class XPoster:
    def __init__(self):
        self.client_id = os.getenv("X_CLIENT_ID")
        self.access_token = os.getenv("X_OAUTH2_ACCESS_TOKEN")
        self.refresh_token = os.getenv("X_OAUTH2_REFRESH_TOKEN")

    def refresh_access_token(self):
        if not self.refresh_token or not self.client_id:
            return
        response = requests.post(
            "https://api.x.com/2/oauth2/token",
            data={"refresh_token": self.refresh_token, "grant_type": "refresh_token", "client_id": self.client_id},
            timeout=30,
        )
        if response.ok:
            self.access_token = response.json().get("access_token", self.access_token)

    def post(self, image_path, text):
        self.refresh_access_token()
        headers = {"Authorization": f"Bearer {self.access_token}"}
        with image_path.open("rb") as image_file:
            upload = requests.post(
                "https://upload.x.com/1.1/media/upload.json",
                files={"media": (image_path.name, image_file, "image/png")},
                headers=headers,
                timeout=60,
            )
        upload.raise_for_status()
        media_id = upload.json()["media_id_string"]
        response = requests.post(
            "https://api.x.com/2/tweets",
            json={"text": text, "media": {"media_ids": [media_id]}},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("data", {}).get("id")

def parse_time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(timezone.utc)


def run_due(poster):
    now = datetime.now(timezone.utc)
    failures = []
    with LOCK:
        items = load_items()
        changed = False
        for item in items:
            if item.get("status") == "posted":
                continue
            if not item.get("text", "").strip():
                item["status"] = "needs_text_review"
                item["error"] = "本文が空欄です"
                changed = True
                continue
            if parse_time(item["scheduled_at"]) > now:
                continue
            image_path = ROOT / item["image_path"]
            if not image_path.is_file():
                image_path = ROOT / Path(item["image_path"]).name
            if not image_path.is_file():
                item["status"] = "error"
                item["error"] = f"画像が見つかりません: {image_path}"
                failures.append((item.get("post_key", ""), item["error"]))
                changed = True
                continue
            try:
                item["post_id"] = poster.post(image_path, item["text"])
                item["status"] = "posted"
                item["posted_at"] = datetime.now(JST).isoformat()
                item["error"] = ""
            except Exception as exc:
                item["status"] = "error"
                item["error"] = str(exc)[:500]
                failures.append((item.get("post_key", ""), item["error"]))
            changed = True
        if changed:
            save_items(items)
    return failures


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
        elif self.path == "/schedule":
            with LOCK:
                self.send_json(200, load_items())
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/schedule":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            item = json.loads(self.rfile.read(length))
            for field in ("post_key", "scheduled_at", "image_path", "text"):
                if not item.get(field):
                    raise ValueError(f"{field} is required")
            parse_time(item["scheduled_at"])
            item["status"] = "planned"
            item["error"] = ""
            with LOCK:
                items = load_items()
                if any(x.get("post_key") == item["post_key"] for x in items):
                    self.send_json(409, {"error": "post_key already exists"})
                    return
                items.append(item)
                save_items(items)
            self.send_json(201, item)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    poster = XPoster()

    def worker():
        while True:
            run_due(poster)
            time.sleep(30)

    threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"予約API起動: http://127.0.0.1:{args.port}")
    print("終了するにはCtrl+Cを押してください。")
    server.serve_forever()


if __name__ == "__main__":
    main()
