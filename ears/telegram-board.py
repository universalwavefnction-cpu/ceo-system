#!/usr/bin/env python3
"""The owner's wave board as a Telegram Mini App (standard library, Python 3.10+).

Run: python3 ears/telegram-board.py --url <https://address> [--port <port>] [--every <seconds>]
The URL is required; port defaults to 8790 (0 chooses a free port), every to 60 seconds.
Bind only 127.0.0.1, set the owner's menu button, then serve and tell newly waiting rows.
Read TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID from the environment only; TELEGRAM_API
defaults to https://api.telegram.org. Read $CEO_HOME/board/*.json (CEO_HOME defaults to
~/.ceo), render through ceo.py board --page, and keep notification memory atomically in
$CEO_HOME/telegram-board.json. The first sight of each CEO is silent. No inbox is polled.
A local tunnel supplies HTTPS; this ear neither starts one nor reads a credential file.
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KERNEL = Path(__file__).resolve().parents[1] / "ceo.py"
SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://telegram.org/js/telegram-web-app.js?64"></script>
<title>Board</title>
<style>html,body{height:100%;margin:0}iframe{width:100%;height:100%;border:0;display:block}
[hidden]{display:none}p{padding:1rem;font:16px system-ui}</style></head>
<body><p id="message" role="status">Loading the board…</p><iframe title="Wave board" hidden></iframe>
<script>
const frame = document.querySelector('iframe');
const message = document.querySelector('#message');
function failed(text) {
  frame.hidden = true;
  frame.removeAttribute('srcdoc');
  message.textContent = text;
  message.hidden = false;
}
async function refresh() {
  try {
    const answer = await fetch('board', {
      method: 'POST', body: Telegram.WebApp.initData,
      headers: {'Content-Type': 'text/plain'}, cache: 'no-store'
    });
    if (answer.status === 403) {
      failed("Open the board from the bot's menu button.");
    } else if (answer.status === 200) {
      frame.srcdoc = await answer.text();
      frame.hidden = false;
      message.hidden = true;
    } else {
      failed('The board cannot be read now.');
    }
  } catch (_) { failed('The board cannot be read now.'); }
}
if (window.Telegram && Telegram.WebApp) {
  Telegram.WebApp.ready();
  Telegram.WebApp.expand();
  refresh();
  setInterval(refresh, 5 * 60 * 1000);
} else { failed("Open the board from the bot's menu button."); }
</script></body></html>
"""


def report(error, token):
    """All error output is one line, including errors containing a Bot API URL."""
    text = str(error).replace(token, "<token>") if token else str(error)
    print("telegram-board: " + " ".join(text.split()), file=sys.stderr, flush=True)


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def options(token):
    parser = Arguments(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--every", type=float, default=60)
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        raise ValueError("--url must start with https://.")
    if args.port != 0 and not 1024 <= args.port <= 65535:
        raise ValueError("--port must be 0 or between 1024 and 65535.")
    if not token.strip():
        raise ValueError("TELEGRAM_BOT_TOKEN is required.")
    owner = os.environ.get("TELEGRAM_OWNER_ID", "")
    if not re.fullmatch(r"[0-9]+", owner):
        raise ValueError("TELEGRAM_OWNER_ID must be numeric.")
    args.owner = int(owner)
    if not math.isfinite(args.every) or args.every <= 0:
        raise ValueError("--every must be a positive number of seconds.")
    return args


class Board:
    def __init__(self, token, owner):
        self.token, self.owner = token, owner
        self.api = os.environ.get("TELEGRAM_API") or "https://api.telegram.org"
        self.home = Path(os.environ.get("CEO_HOME") or "~/.ceo").expanduser()
        self.memory = self.home / "telegram-board.json"

    def call(self, method, **params):
        request = urllib.request.Request(
            f"{self.api.rstrip('/')}/bot{self.token}/{method}",
            json.dumps(params).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as answer:
            said = json.load(answer)
        if said.get("ok") is not True:
            raise ValueError(f"{method} failed: {said.get('description', 'Bot API refused the call')}")
        return said["result"]

    def authentic(self, body):
        try:
            pairs = urllib.parse.parse_qsl(body, keep_blank_values=True)
            fields = dict(pairs)
            if len(fields) != len(pairs) or "hash" not in fields:
                return False
            supplied = fields.pop("hash")
            check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
            secret = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()
            expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                return False
            user = json.loads(fields["user"])
            if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] != self.owner:
                return False
            age = time.time() - int(fields["auth_date"])
            return -60 <= age <= 86400
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
            return False

    def files(self):
        found = []
        for path in sorted((self.home / "board").glob("*.json")):
            try:
                one = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                continue  # a partial copy, unrelated JSON, or an unreadable file
            if (isinstance(one, dict) and isinstance(one.get("ceo"), str) and one["ceo"]
                    and isinstance(one.get("status"), dict) and isinstance(one.get("wave"), dict)):
                found.append((path, one))
        return found

    def page(self):
        files = self.files()
        if not files:
            raise ValueError("No board status files are available.")
        done = subprocess.run([sys.executable, "-B", str(KERNEL), "board", "--page",
                               *[str(path) for path, _ in files]],
                              capture_output=True, timeout=30)
        if done.returncode:
            raise ValueError("The board command failed.")
        return done.stdout

    def save(self, seen):
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.memory.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(seen, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.memory)

    def tell(self):
        # Reload after each failed pass too: only the on-disk record confirms a send.
        try:
            seen = json.loads(self.memory.read_text(encoding="utf-8"))
        except FileNotFoundError:
            seen = {}
        if not (isinstance(seen, dict) and all(isinstance(slugs, list)
                and all(isinstance(slug, str) for slug in slugs) for slugs in seen.values())):
            raise ValueError("Notification memory is not a mapping of CEOs to slugs.")
        waiting = {}
        for _, one in self.files():
            rows = waiting.setdefault(one["ceo"], {})
            for row in one["status"].get("rows", []):
                if (isinstance(row, dict) and row.get("state") == "waiting" and not row.get("answered")
                        and isinstance(row.get("slug"), str)):
                    rows[row["slug"]] = row
        for name, rows in waiting.items():
            if name not in seen:
                seen[name] = list(rows)
                self.save(seen)  # baseline each new CEO, even if a later send fails
                continue
            for slug, row in rows.items():
                if slug not in seen[name]:
                    text = f"{name}: {row.get('sentence') or ''} {row.get('question') or ''} ({slug})"
                    self.call("sendMessage", chat_id=self.owner, text=" ".join(text.split())[:4096])
                    seen[name].append(slug)
                    self.save(seen)  # each successful send survives a later failure or restart
        for name, rows in waiting.items():
            seen[name] = list(rows)  # missing CEOs retain their last record
        self.save(seen)

    def pass_once(self):
        try:
            self.tell()
        except Exception as why:
            report(why, self.token)

    def passes(self, stopped, every):
        while not stopped.wait(every):
            self.pass_once()


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def log_message(self, *_):
        pass  # neither initData nor request paths belong in logs

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def answer(self, code, body, content_type="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def not_found(self):
        self.answer(404, "Not found.\n")

    def __getattr__(self, name):
        if name.startswith("do_"):
            return self.not_found  # unsupported methods also get 404, not the default 501
        raise AttributeError(name)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/":
            self.answer(200, SHELL, "text/html; charset=utf-8")
        else:
            self.not_found()

    def do_POST(self):
        if self.path != "/board":
            return self.not_found()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request body.")
            body = self.rfile.read(length).decode("utf-8")
        except (ValueError, OSError):
            return self.answer(403, "Open the board from the bot's menu button.\n")
        if not self.server.board.authentic(body):
            return self.answer(403, "Open the board from the bot's menu button.\n")
        try:
            page = self.server.board.page()
        except Exception as why:
            report(why, self.server.board.token)
            return self.answer(503, "The board cannot be read now.\n")
        self.answer(200, page, "text/html; charset=utf-8")


class Server(ThreadingHTTPServer):
    def handle_error(self, *_):
        report(sys.exc_info()[1], self.board.token)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    try:
        args = options(token)
    except ValueError as why:
        report(why, token)
        return 2
    stopped = threading.Event()

    def terminate(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    try:
        with Server(("127.0.0.1", args.port), Handler) as server:
            server.board = Board(token, args.owner)
            server.board.call("setChatMenuButton", chat_id=args.owner, menu_button={
                "type": "web_app", "text": "Board", "web_app": {"url": args.url}})
            server.board.pass_once()
            print(f"telegram-board: serving http://127.0.0.1:{server.server_port}", flush=True)
            threading.Thread(target=server.board.passes, args=(stopped, args.every), daemon=True).start()
            server.serve_forever()
    except Exception as why:
        report(why, token)
        return 1
    finally:
        stopped.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
