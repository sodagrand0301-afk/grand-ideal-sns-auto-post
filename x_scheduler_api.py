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
        values = {
            "consumer_key": os.getenv("X_CONSUMER_KEY"),
            "consumer_secret": os.getenv("X_CONSUMER_SECRET"),
            "access_token": os.getenv("X_ACCESS_TOKEN"),
            "access_token_secret": os.getenv("X_ACCESS_TOKEN_SECRET"),
        }
        if not all(values.values()):
            print("OAuth1認証情報を入力してください。入力内容は保存・表示しません。")
            values = {
                "consumer_key": getpass("コンシューマーキー: "),
                "consumer_secret": getpass("コンシューマーシークレット: "),
                "access_token": getpass("アクセストークン: "),
                "access_token_secret": getpass("アクセストークンシークレット: "),
            }
        self.auth = OAuth1(
            values["consumer_key"],
            client_secret=values["consumer_secret"],
            resource_owner_key=values["access_token"],
            resource_owner_secret=values["access_token_secret"],
        )

    def post(self, image_path, text):
        upload_url = "https://upload.x.com/1.1/media/upload.json"
        total_bytes = image_path.stat().st_size
        init = requests.post(
            upload_url,
            data={
                "command": "INIT",
                "total_bytes": str(total_bytes),
                "media_type": "image/png",
                "media_category": "tweet_image",
            },
            auth=self.auth,
            timeout=60,
        )
        if not init.ok:
            raise RuntimeError(f"X media INIT {init.status_code}: {init.text[:500]}")
        media_id = init.json().get("media_id_string")
        if not media_id:
            raise RuntimeError("X media INIT returned no media id")
        with image_path.open("rb") as image_file:
            append = requests.post(
                upload_url,
                data={"command": "APPEND", "media_id": media_id, "segment_index": "0"},
                files={"media": (image_path.name, image_file, "image/png")},
                auth=self.auth,
                timeout=60,
            )
        if not append.ok:
            raise RuntimeError(f"X media APPEND {append.status_code}: {append.text[:500]}")
        finalize = requests.post(
            upload_url,
            data={"command": "FINALIZE", "media_id": media_id},
            auth=self.auth,
            timeout=60,
        )
        if not finalize.ok:
            raise RuntimeError(f"X media FINALIZE {finalize.status_code}: {finalize.text[:500]}")
        response = requests.post(
            "https://api.x.com/1.1/statuses/update.json",
            data={"status": text, "media_ids": media_id},
            auth=self.auth,
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"X tweet post {response.status_code}: {response.text[:500]}")
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
            return
        if self.path == "/schedule":
            with LOCK:
                self.send_json(200, load_items())
            return
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
