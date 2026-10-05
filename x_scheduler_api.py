import argparse
import csv
import json
import os
import re
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
INSTAGRAM_CSV_FILE = ROOT / "instagram_schedule_2026-10-05_onward.csv"
JST = timezone(timedelta(hours=9), "JST")
LOCK = threading.Lock()
OFFICIAL_LINE_CTA = "詳しくは公式LINEまでご相談ください。"

def format_social_text(text, max_chars=None):
    cleaned = text.replace("\\r\\n", "\\n").replace("\\r", "\\n")
    lines = []
    for line in cleaned.split("\\n"):
        line = re.sub(r"[ \\t]+", " ", line).strip()
        if line and line != OFFICIAL_LINE_CTA:
            lines.append(line)
    tags = [line for line in lines if line.startswith("#")]
    body = [line for line in lines if not line.startswith("#")]
    result = "\\n".join(body).strip()
    if tags:
        result += ("\\n\\n" if result else "") + "\\n".join(tags)
    result += ("\\n\\n" if result else "") + OFFICIAL_LINE_CTA
    if max_chars and len(result) > max_chars:
        suffix = ("\\n\\n" + "\\n".join(tags) if tags else "") + "\\n\\n" + OFFICIAL_LINE_CTA
        result = result[:max(1, max_chars - len(suffix) - 1)].rstrip() + "…" + suffix
        result = result[:max_chars]
    return result

def load_items():
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))

def save_items(items):
    temp = DATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(DATA_FILE)

def load_instagram_texts():
    """Instagram本文を読み込み、X本文との内容差を防ぐ。"""
    if not INSTAGRAM_CSV_FILE.exists():
        return {}
    with INSTAGRAM_CSV_FILE.open(newline="", encoding="utf-8-sig") as file:
        rows = csv.DictReader(file)
        return {
            row["post_key"].replace("-Instagram", "-X"): row["text"]
            for row in rows
            if row.get("platform") == "Instagram" and row.get("post_key") and row.get("text")
        }

class XPoster:
    def __init__(self):
        values = {name: os.getenv(name) for name in ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")}
        if not all(values.values()):
            raise RuntimeError("XのOAuth1認証情報が未設定です。GitHub Secretsを確認してください。")
        self.auth = OAuth1(values["X_CONSUMER_KEY"], client_secret=values["X_CONSUMER_SECRET"], resource_owner_key=values["X_ACCESS_TOKEN"], resource_owner_secret=values["X_ACCESS_TOKEN_SECRET"])

    def post(self, image_path, text):
        text = format_social_text(text, 4000)
        with image_path.open("rb") as image_file:
            upload = requests.post("https://upload.x.com/1.1/media/upload.json", files={"media": (image_path.name, image_file, "image/png")}, auth=self.auth, timeout=60)
        if not upload.ok:
            raise RuntimeError("X画像アップロード失敗 HTTP %s: %s" % (upload.status_code, upload.text[:300]))
        media_id = upload.json().get("media_id_string")
        if not media_id:
            raise RuntimeError("X画像IDが返りませんでした")
        response = requests.post("https://api.x.com/2/tweets", json={"text": text, "media": {"media_ids": [media_id]}}, auth=self.auth, timeout=30)
        if not response.ok:
            raise RuntimeError("X本文投稿失敗 HTTP %s: %s" % (response.status_code, response.text[:300]))
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
        instagram_texts = load_instagram_texts()
        changed = False
        for item in items:
            matching_text = instagram_texts.get(item.get("post_key", ""))
            if matching_text and item.get("text") != matching_text:
                item["text"] = matching_text
                changed = True
            if item.get("status") == "posted":
                continue
            if parse_time(item["scheduled_at"]) > now:
                continue
            key = item.get("post_key", "unknown")
            if not item.get("text", "").strip():
                err = "本文が空欄です"
                item.update({"status": "needs_text_review", "error": err})
                failures.append((key, err))
                changed = True
                continue
            image_path = ROOT / item["image_path"]
            if not image_path.is_file():
                alt = ROOT / Path(item["image_path"]).name
                image_path = alt if alt.is_file() else image_path
            if not image_path.is_file():
                err = "画像が見つかりません: %s" % image_path
                item.update({"status": "error", "error": err})
                failures.append((key, err))
                changed = True
                continue
            try:
                post_id = poster.post(image_path, item["text"])
                item.update({"status": "posted", "post_id": post_id, "posted_at": datetime.now(JST).isoformat(), "error": ""})
            except Exception as exc:
                err = str(exc)[:500]
                item.update({"status": "error", "error": err})
                failures.append((key, err))
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
        self.send_json(404, {"error": "not found"})
    def log_message(self, *_args):
        return

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    poster = XPoster()
    def worker():
        while True:
            failures = run_due(poster)
            for key, err in failures:
                print("X投稿失敗: %s / %s" % (key, err))
            time.sleep(30)
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()

if __name__ == "__main__":
    main()
