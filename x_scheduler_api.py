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
from social_text import format_social_text

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

