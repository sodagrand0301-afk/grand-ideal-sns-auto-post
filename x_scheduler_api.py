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
        values = {"consumer_key": os.getenv("X_CONSUMER_KEY"), "consumer_secret": os.getenv("X_CONSUMER_SECRET"), "access_token": os.getenv("X_ACCESS_TOKEN"), "access_token_secret": os.getenv("X_ACCESS_TOKEN_SECRET")}
        if not all(values.values()):
            values = {"consumer_key": getpass("コンシューマーキー: "), "consumer_secret": getpass("コンシューマーシークレット: "), "access_token": getpass("アクセストークン: "), "access_token_secret": getpass("アクセストークンシークレット: ")}
        self.auth = OAuth1(values["consumer_key"], client_secret=values["consumer_secret"], resource_owner_key=values["access_token"], resource_owner_secret=values["access_token_secret"])
    def post(self, image_path, text):
        with image_path.open("rb") as image_file:
            upload = requests.post("https://upload.x.com/1.1/media/upload.json", files={"media": (image_path.name, image_file, "image/png")}, auth=self.auth, timeout=60)
        upload.raise_for_status()
        media_id = upload.json()["media_id_string"]
