import argparse
import json
import re
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
OFFICIAL_LINE_CTA = "詳しくは公式LINEまでご相談ください。"

def format_social_text(text: str, max_chars: int | None = None) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in cleaned.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line and line != OFFICIAL_LINE_CTA:
            lines.append(line)
    hashtag_lines = [line for line in lines if line.startswith("#")]
    body_lines = [line for line in lines if not line.startswith("#")]
    body = "\n".join(body_lines).strip()
    hashtags = "\n".join(hashtag_lines).strip()
    parts = [part for part in (body, hashtags, OFFICIAL_LINE_CTA) if part]
    result = "\n\n".join(parts)
    if max_chars is not None and len(result) > max_chars:
        suffix = "\n\n".join(part for part in (hashtags, OFFICIAL_LINE_CTA) if part)
        available = max_chars - len(suffix) - 2
        if available <= 0:
            return OFFICIAL_LINE_CTA[:max_chars]
        result = f"{body[:max(1, available - 1)].rstrip()}…\n\n{suffix}"
    return result


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


def response_error(response, label):
    return RuntimeError(f"{label} ({response.status_code}): {response.text[:500]}")


class XPoster:
    def __init__(self):
        values = {
            "consumer_key": os.getenv("X_CONSUMER_KEY"),
            "consumer_secret": os.getenv("X_CONSUMER_SECRET"),
            "access_token": os.getenv("X_ACCESS_TOKEN"),
            "access_token_secret": os.getenv("X_ACCESS_TOKEN_SECRET"),
        }
        self.oauth2_access_token = os.getenv("X_OAUTH2_ACCESS_TOKEN")
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
        text = format_social_text(text, max_chars=280)
        if len(text) > 280:
            raise ValueError(f"X本文が280文字を超えています: {len(text)}文字")

        # 画像はOAuth 1.0a User Contextでアップロードする。
        with image_path.open("rb") as image_file:
            upload = requests.post(
                "https://upload.x.com/1.1/media/upload.json",
                files={"media": (image_path.name, image_file, "image/png")},
                auth=self.auth,
                timeout=60,
            )
        if not upload.ok:
            raise response_error(upload, "X画像アップロード失敗")
        media_id = upload.json().get("media_id_string")
        if not media_id:
            raise RuntimeError(f"X画像IDが返りませんでした: {upload.text[:500]}")

        headers = {"Content-Type": "application/json"}
        auth = self.auth
        if self.oauth2_access_token:
            # App-only Bearer Tokenではなく、ユーザーアクセストークンを使う。
            headers["Authorization"] = f"Bearer {self.oauth2_access_token}"
            auth = None
        response = requests.post(
            "https://api.x.com/2/tweets",
            json={"text": text, "media": {"media_ids": [media_id]}},
            headers=headers,
            auth=auth,
            timeout=30,
        )
        if not response.ok:
            raise response_error(response, "X本文投稿失敗")
        post_id = response.json().get("data", {}).get("id")
        if not post_id:
            raise RuntimeError(f"X投稿IDが返りませんでした: {response.text[:500]}")
        return post_id


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
                post_id = poster.post(image_path, item["text"])
                item.update({"status": "posted", "post_id": post_id, "posted_at": datetime.now(JST).isoformat(), "error": ""})
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
