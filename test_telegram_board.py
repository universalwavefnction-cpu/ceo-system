"""The Mini App ear as a process, eight status files and an offline Bot API on loopback.

These tests use only the public CLI, HTTP responses, messages and persisted notification
memory. No kernel module is imported or patched, and every child is stopped in cleanup.
"""
import contextlib
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EAR = ROOT / "ears" / "telegram-board.py"
TOKEN, OWNER, STRANGER = "4242:offline-board-token", 1001, 2002
FIRST, SECOND = "https://first.example.test", "https://second.example.test"
NAMES = ["kernel", "shop", "store", "blog", "lab", "blog-site", "outreach", "portfolio"]
STAMP = datetime.now(timezone.utc).isoformat()


def row(slug, state="waiting", **more):
    return dict(slug=slug, state=state, order=f"orders/{slug}.md", since=STAMP,
                sentence=f"Order {slug} of its CEO.", question="Agree or decline?",
                choices=[dict(pick="agree", text="agree")]) | more


def signed(owner=OWNER, age=10, token=TOKEN, **changes):
    fields = dict(auth_date=str(int(time.time()) - age), query_id="offline-query",
                  signature="c3RhbmQtaW4gc2lnbmF0dXJl", start_param="",
                  user=json.dumps({"id": owner, "first_name": "Владелец"}, ensure_ascii=False))
    fields.update(changes)
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


class StandIn:
    """Keep every call, including failures; only accepted sends count as delivered."""
    def __init__(self, case):
        self.lock = threading.Lock()
        self.calls, self.delivered = [], []
        self.failure_method, self.failure_text, self.failure_code = None, None, 200
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                params = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                method = self.path.rsplit("/", 1)[-1]
                with api.lock:
                    api.calls.append((method, params))
                    fail = (method == api.failure_method or
                            (api.failure_text and api.failure_text in params.get("text", "")))
                    code = api.failure_code if fail else 200
                    if self.path != f"/bot{TOKEN}/{method}":
                        code, said = 401, dict(ok=False, description="Unauthorized")
                    elif fail:
                        said = dict(ok=False, description=f"Failure at /bot{TOKEN}/{method}\ntry again")
                    elif method == "sendMessage":
                        api.delivered.append(params)
                        said = dict(ok=True, result=dict(message_id=len(api.delivered),
                                    chat={"id": params["chat_id"]}, date=int(time.time()), text=params["text"]))
                    else:
                        said = dict(ok=True, result=True)
                body = json.dumps(said).encode()
                with contextlib.suppress(ConnectionError):
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        case.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def snapshot(self):
        with self.lock:
            return list(self.calls), list(self.delivered)

    def fail(self, method=None, text=None, code=200):
        with self.lock:
            self.failure_method, self.failure_text, self.failure_code = method, text, code


class BoardCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="telegram-board-test-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.home = self.scratch / "home"
        self.folder = self.home / "board"
        self.folder.mkdir(parents=True)
        self.api = StandIn(self)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("TELEGRAM_") and not key.lower().endswith("_proxy")}
        self.env.update(CEO_HOME=str(self.home), HOME=str(self.scratch / "nohome"),
                        TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_OWNER_ID=str(OWNER), TELEGRAM_API=self.api.url,
                        PYTHONDONTWRITEBYTECODE="1", NO_PROXY="*")
        self.logs = []
        self.old = [row("052-old"), row("001-a", "in progress")]
        for name in NAMES:
            self.status(name, self.old if name == "kernel" else [row("046-old")] if name == "shop"
                        else [row("001-a", "in progress")])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def status(self, name, rows, title=None):
        one = dict(ceo=name, host="vps" if name == "kernel" else "mac", time=STAMP, last_tick=STAMP,
                   status=dict(rows=rows, agents=[], awake=False),
                   wave=dict(plans=[dict(plan="plans/w.md", title=f"The {name} wave", go=STAMP)],
                             orders=[dict(number="001", order="orders/001-a.md", latest="001-a", round=1,
                                          title=title or f"First order of {name}", merged=False,
                                          cut=False, stalled=False)]))
        temporary = self.folder / f".{name}.tmp"
        temporary.write_text(json.dumps(one), encoding="utf-8")
        os.replace(temporary, self.folder / f"{name}.json")

    def wait_for(self, predicate, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.04)
        self.fail("Timed out waiting for the ear: " + "\n".join(log.read_text() for log in self.logs))

    def launch(self, args, env=None):
        log = self.scratch / f"ear-{len(self.logs)}.log"
        self.logs.append(log)
        with log.open("wb") as out:
            child = subprocess.Popen([sys.executable, "-B", str(EAR), *args], cwd=self.scratch,
                                     env=self.env if env is None else env, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self.stop, child)
        return child, log

    def stop(self, child):
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
                self.fail("The ear did not stop on SIGTERM.")

    def start(self, url=FIRST):
        child, log = self.launch(["--url", url, "--port", "0", "--every", "0.1"])
        found = None

        def ready():
            nonlocal found
            found = re.search(r"^telegram-board: serving http://127\.0\.0\.1:(\d+)", log.read_text(), re.M)
            self.assertIsNone(child.poll(), log.read_text())
            return found

        self.wait_for(ready)
        return child, f"http://127.0.0.1:{found[1]}"

    def fetch(self, base, path="/", data=None, method=None):
        body = data.encode() if isinstance(data, str) else data
        asked = urllib.request.Request(base + path, data=body, method=method,
                                       headers={"Content-Type": "text/plain"})
        try:
            answer = self.opener.open(asked, timeout=40)
        except urllib.error.HTTPError as why:
            answer = why
        with answer:
            self.assertEqual(answer.headers.get("Cache-Control"), "no-store")
            return answer.code, answer.read().decode()

    def memory(self):
        return json.loads((self.home / "telegram-board.json").read_text())

    def sent(self):
        return self.api.snapshot()[1]

    def quiet(self, expected):
        time.sleep(0.4)  # several more passes; a repeated send is observable
        self.assertEqual(len(self.sent()), expected, self.api.snapshot())

    def clean_logs(self):
        for log in self.logs:
            self.assertNotIn(TOKEN, log.read_text())


class Access(BoardCase):
    def test_only_the_owners_fresh_signed_init_data_loads_the_board(self):
        for name, data in {"partial": b'{"ceo":', "other": b'{"token":"unrelated"}',
                           "utf8": b"\xff", "list": b"[]", "empty-name": b'{"ceo":"","status":{},"wave":{}}',
                           "nested": b"[" * 1500 + b"0" + b"]" * 1500,
                           "bad-status": b'{"ceo":"bad","status":[],"wave":{}}'}.items():
            (self.folder / f"{name}.json").write_bytes(data)
        child, base = self.start()
        for path in ("/", "/?tgWebAppStartParam=board"):
            code, shell = self.fetch(base, path)
            self.assertEqual(code, 200)
            self.assertIn('<script src="https://telegram.org/js/telegram-web-app.js?64"></script>', shell)
            self.assertLess(shell.index("telegram-web-app.js"), shell.index("</head>"))
            for required in ("Telegram.WebApp.ready()", "Telegram.WebApp.expand()", "Telegram.WebApp.initData",
                             "fetch('board'", "method: 'POST'", "frame.srcdoc", "setInterval(refresh, 5 * 60 * 1000)",
                             "<iframe", "Open the board from the bot's menu button."):
                self.assertIn(required, shell)
            self.assertNotIn("data-view", shell)
            self.assertNotIn(TOKEN, shell)
        for method, path in (("GET", "/board"), ("POST", "/"), ("GET", "/board/kernel.json"),
                             ("GET", "/telegram-board.json"), ("POST", "/board?x=1"),
                             ("HEAD", "/"), ("PUT", "/board"), ("OPTIONS", "/"), ("WHATEVER", "/")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.fetch(base, path, method=method)[0], 404)
        good = signed()
        tampered = good.replace(urllib.parse.quote(str(OWNER)), urllib.parse.quote(str(STRANGER)))
        self.assertNotEqual(good, tampered)
        invalid = ["", signed(token="other-token"), tampered, signed(STRANGER), signed(age=86401),
                   signed(age=-120), good + "&user=duplicate", good + "&hash=duplicate",
                   good + "&start_param=", good.replace("signature=c", "signature=d"),
                   good.rsplit("&hash=", 1)[0], good.rsplit("&hash=", 1)[0] + "&hash=%FF",
                   signed(auth_date="tomorrow"), signed(user="null"), signed(user="[]"),
                   signed(user="{"), signed(user='{"id":"1001"}'), signed(user='{"id":1001.0}'),
                   signed(user="[" * 1500 + "0" + "]" * 1500), b"\xff"]
        for data in invalid:
            with self.subTest(data=data):
                code, body = self.fetch(base, "/board", data)
                self.assertEqual(code, 403)
                self.assertEqual(len(body.splitlines()), 1)
                self.assertNotIn("data-view", body)
                self.assertNotIn("First order", body)
        for data in (good, signed(age=23 * 3600), signed(age=-45)):
            code, body = self.fetch(base, "/board", data)
            self.assertEqual(code, 200, body)
            self.assertEqual(re.findall(r'<section[^>]*\sdata-view="([^"]+)"', body),
                             ["all", *sorted(NAMES, key=lambda name: name + ".json")])
        self.status("kernel", [row("001-a")], title="A changed order")
        code, body = self.fetch(base, "/board", good)
        self.assertEqual(code, 200)
        self.assertIn("Agree or decline?", body)
        self.assertIn('data-row="kernel/001"', body)  # the valid request rendered the changed file
        duplicate = self.folder / "duplicate.json"
        duplicate.write_bytes((self.folder / "kernel.json").read_bytes())
        code, body = self.fetch(base, "/board", good)
        self.assertEqual(code, 503)  # the CLI refuses duplicate CEOs
        self.assertEqual(len(body.splitlines()), 1)
        for path in self.folder.glob("*.json"):
            path.unlink()
        code, body = self.fetch(base, "/board", good)
        self.assertEqual(code, 503)
        self.assertNotIn("data-view", body)
        self.assertEqual(self.fetch(base, "/board", "")[0], 403)
        self.stop(child)
        self.clean_logs()


class Start(BoardCase):
    def test_the_start_sets_the_owners_menu_button_to_the_address_it_is_given(self):
        invalid = [(["--url", "http://plain.example.test", "--port", "0"], {}),
                   *[(["--url", FIRST, "--port", port], {}) for port in ("443", "-1", "65536", "word")],
                   (["--port", "0"], {}),
                   *[(["--url", FIRST], {"TELEGRAM_OWNER_ID": owner}) for owner in (None, "", "abc", "1.5")],
                   (["--url", FIRST], {"TELEGRAM_BOT_TOKEN": None}),
                   (["--url", FIRST], {"TELEGRAM_BOT_TOKEN": ""}),
                   *[(["--url", FIRST, "--every", seconds], {}) for seconds in ("0", "-1", "nan", "inf")]]
        for args, changes in invalid:
            env = {**self.env, **changes}
            env = {key: value for key, value in env.items() if value is not None}
            with self.subTest(args=args, changes=changes):
                done = subprocess.run([sys.executable, "-B", str(EAR), *args], cwd=self.scratch, env=env,
                                      capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertEqual(done.stdout, "")
                self.assertEqual(len(done.stderr.splitlines()), 1, done.stderr)
        self.assertEqual(self.api.snapshot()[0], [])
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            child, log = self.launch(["--url", FIRST, "--port", str(occupied.getsockname()[1])])
            self.assertNotEqual(child.wait(timeout=15), 0, log.read_text())
        self.assertEqual(self.api.snapshot()[0], [])  # binding precedes the menu call
        for url in (FIRST, SECOND):
            child, _ = self.start(url)
            self.stop(child)
            self.assertEqual(child.returncode, 0)
        expected = [("setChatMenuButton", dict(chat_id=OWNER, menu_button=dict(
            type="web_app", text="Board", web_app={"url": url}))) for url in (FIRST, SECOND)]
        self.assertEqual(self.api.snapshot()[0], expected)
        for code in (200, 500):
            self.api.fail(method="setChatMenuButton", code=code)
            child, log = self.launch(["--url", FIRST, "--port", "0"])
            self.assertNotEqual(child.wait(timeout=15), 0)
            self.assertNotIn("serving", log.read_text())
            self.assertEqual(len(log.read_text().splitlines()), 1)
            if code == 200:
                self.assertIn("<token>", log.read_text())
        self.assertEqual(self.sent(), [])
        self.clean_logs()


class Tell(BoardCase):
    def test_a_newly_waiting_row_is_told_once_across_a_restart_and_nothing_else_is_sent(self):
        # A start before the Mac's first copy, then eight CEOs arriving, is silent too.
        files = {path.name: path.read_bytes() for path in self.folder.glob("*.json")}
        for path in self.folder.glob("*.json"):
            path.unlink()
        child, _ = self.start()
        self.assertEqual(self.memory(), {})
        for name, data in files.items():
            (self.folder / name).write_bytes(data)
        self.wait_for(lambda: len(self.memory()) == 8)
        self.quiet(0)
        rows = self.old + [row("135-new", sentence="A  new\nquestion", question="Agree\tor decline?"),
                           row("136-answered", answered="agree"), row("137-running", "in progress")]
        self.status("kernel", rows)
        self.wait_for(lambda: len(self.sent()) == 1)
        self.quiet(1)
        self.assertEqual(self.sent()[0], dict(chat_id=OWNER, text="kernel: A new question Agree or decline? (135-new)"))
        self.stop(child)
        self.status("kernel", rows + [row("138-offline")])
        child, _ = self.start(SECOND)
        self.quiet(2)
        self.assertIn("138-offline", self.sent()[1]["text"])
        self.wait_for(lambda: "138-offline" in self.memory()["kernel"])

        # Missing or partial files do not erase a CEO's memory.
        (self.folder / "kernel.json").write_text('{"ceo":')
        self.quiet(2)
        (self.folder / "kernel.json").unlink()
        self.quiet(2)
        self.status("kernel", rows + [row("138-offline")])
        self.quiet(2)

        # Once answered, a row can ask again and buzz again.
        self.status("kernel", self.old + [row("135-new", answered="agree"), row("138-offline")])
        self.wait_for(lambda: "135-new" not in self.memory()["kernel"])
        rows = self.old + [row("135-new"), row("138-offline")]
        self.status("kernel", rows)
        self.wait_for(lambda: len(self.sent()) == 3)
        self.quiet(3)

        # Save each successful send even when the following send fails; retry only the failed one.
        self.api.fail(text="140-failed")
        rows += [row("139-before-failure"), row("140-failed"), row("141-after-failure")]
        self.status("kernel", rows)
        self.wait_for(lambda: any("140-failed" in params.get("text", "") for _, params in self.api.snapshot()[0]))
        self.quiet(4)
        self.assertIn("139-before-failure", self.memory()["kernel"])
        self.assertNotIn("140-failed", self.memory()["kernel"])
        self.stop(child)
        child, _ = self.start()
        self.quiet(4)
        self.api.fail()
        self.wait_for(lambda: len(self.sent()) == 6)
        self.quiet(6)
        self.assertIn("140-failed", self.sent()[4]["text"])
        self.assertIn("141-after-failure", self.sent()[5]["text"])

        rows += [row("142-long", sentence="ж " * 3000)]
        self.status("kernel", rows)
        self.wait_for(lambda: len(self.sent()) == 7)
        expected = " ".join(("kernel: " + "ж " * 3000 + " Agree or decline? (142-long)").split())[:4096]
        self.assertEqual(self.sent()[-1], dict(chat_id=OWNER, text=expected))
        self.quiet(7)
        self.stop(child)
        calls, sent = self.api.snapshot()
        self.assertLessEqual({method for method, _ in calls}, {"setChatMenuButton", "sendMessage"})
        self.assertTrue(all(set(message) == {"chat_id", "text"} and message["chat_id"] == OWNER for message in sent))
        self.clean_logs()


if __name__ == "__main__":
    unittest.main()
