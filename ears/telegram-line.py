#!/usr/bin/env python3
"""One plain line to the owner's phone when a question waits, a plan closes or a bell rings (standard library only).

Run: python3 ears/telegram-line.py (--once | --every <seconds>) [--morning HH:MM] [--evening HH:MM] [--now <ISO time>]
Reads $CEO_HOME/board/*.json (CEO_HOME defaults to ~/.ceo), the status files `ceo.py board` writes, and skips any
file that is not one. Sends with the Bot API's sendMessage to TELEGRAM_OWNER_ID at TELEGRAM_API (default
https://api.telegram.org); both TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID come from the environment only, and a
missing one sends nothing and exits 2. --once is one pass, for a timer; --every a pass, a sleep, a pass, until stopped.
An event is a waiting row not answered and not seen waiting before; then a plan of `every` seen open that has every
order landed or cut; then a bell whose key it has not seen. A project's first sight is silent. A local day
holds at most 4 event lines and 2 digests (--morning, default 09:00; --evening, 21:00); an event past the 4 is
counted into the next digest. --now fixes the time of every pass (local when it names no offset), for tests.
Memory is $CEO_HOME/telegram-line.json, replaced whole after each send, held event and project; each local date
keeps its own lines and digests, so a clock stepped back across midnight reopens nothing; of the dates it has
let go, the newest is kept, and that date and every one before it is spent. A failed send stops the pass: one line
on stderr, exit 1 for --once, and what was not recorded is tried on the next pass.
"""
import argparse
import json
import math
import os
import re
import signal
import sys
import time
import urllib.request
from datetime import datetime, time as clock
from pathlib import Path

EVENTS, LINE, KEPT = 4, 300, 500  # event lines a local day, the most characters of one, and bell keys remembered
NEWS = ("asked", "open", "bells")  # what a project's memory keeps: waiting slugs, open plans, bell keys

def report(error, token):
    text = str(error).replace(token, "<token>") if token else str(error)
    print("telegram-line: " + " ".join(text.split()), file=sys.stderr, flush=True)

def cut(text, size):
    """text on one line, cut at a word to at most size characters and `…`."""
    text = " ".join(str(text or "").split())
    if len(text) <= size:
        return text
    head = text[:size - 1]
    if text[size - 1] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip() + "…"

def count(number, noun):
    return f"{number} {noun}{'s' * (number != 1)}"

def shape(found, key, kind):
    """The dicts of found[key] that hold a string under `kind`."""
    return [one for one in found.get(key) or [] if isinstance(one, dict) and isinstance(one.get(kind), str)]

def number(one, key):
    return one[key] if isinstance(one.get(key), int) else 0

def settled(plan):
    return number(plan, "landed") + number(plan, "cut")

class Project:
    """What one board says: its questions, plans, bells and titles, and the lines it makes."""
    def __init__(self, name, board):
        self.name, rows = name, shape(board["status"], "rows", "slug")
        self.asking = [row for row in rows if row.get("state") == "waiting" and row.get("answered") is None]
        self.plans, self.bells = shape(board["wave"], "every", "plan"), shape(board, "bells", "key")
        self.titles = {row["slug"]: str(row.get("sentence") or "").removesuffix(".") for row in rows}
        self.titles.update({one["latest"]: one["title"] for one in shape(board["wave"], "orders", "latest")
                            if one.get("title")})
        self.live = [plan for plan in self.plans if settled(plan) < number(plan, "orders")]
        k, newest = len(self.asking), (self.live or [None])[0]
        self.needs = "nothing needs you" if not k else f"{count(k, 'question')} {'waits' if k == 1 else 'wait'} for you"
        self.standing = " · ".join([f"{cut(newest.get('title') or newest['plan'], 60)} · {number(newest, 'landed')} of "
                                    f"{number(newest, 'orders')} in · {number(newest, 'building')} building"
                                    if newest else "no open plan", self.needs])

    def title(self, slug):
        return cut(self.titles.get(slug) or slug, 60)

    def fit(self, head, body, size):
        """One event line: head, body cut to size and to what the 300 characters leave, then the standing."""
        tail = f" · {self.standing}"
        return cut(head + cut(body, min(size, LINE - len(head) - len(tail))) + tail, LINE)

    def news(self, state):
        """Prune what state has seen that no longer holds; return the events of the board it has not seen as
        (rank, field, value, text): a question, then a closed plan, then a bell."""
        plans = {plan["plan"]: plan for plan in self.plans}
        state["asked"] = [slug for slug in state["asked"] if slug in [row["slug"] for row in self.asking]]
        here = {bell["key"] for bell in self.bells}  # a key is of one bell: one seen once is never told again, so
        state["bells"] = [key for n, key in enumerate(state["bells"]) if key in here or n >= len(state["bells"]) - KEPT]
        state["open"] = [path for path in state["open"] if path in plans and number(plans[path], "orders")]
        state["open"] += [plan["plan"] for plan in self.live if plan["plan"] not in state["open"]]
        found = [(0, "asked", row["slug"], self.fit(f"{self.name} · “{self.title(row['slug'])}” asks you: ",
                                                    row.get("question"), 120))
                 for row in self.asking if row["slug"] not in state["asked"]]
        for path in state["open"]:
            plan = plans[path]
            cuts = f" · {number(plan, 'cut')} cut" if number(plan, "cut") else ""
            if settled(plan) >= number(plan, "orders"):
                found.append((1, "open", path, cut(
                    f"{self.name} · {cut(plan.get('title') or path, 60)} closed · {number(plan, 'landed')} of "
                    f"{number(plan, 'orders')} in{cuts} · {self.needs}", LINE)))
        for bell in self.bells:
            if bell["key"] not in state["bells"]:
                words = re.sub(r"`([^`]*)`", lambda one: f"“{self.title(one[1])}”" if self.titles.get(one[1]) else one[1],
                               str(bell.get("line") or bell["key"]))
                found.append((2, "bells", bell["key"], self.fit(f"{self.name} · ", words, 160)))
        return found

    def digest(self):
        if self.live or self.asking or self.bells:
            return f"{self.name} · {self.standing}" + (f" · {count(len(self.bells), 'bell')} in the last day"
                                                       if self.bells else "")

class Ear:
    def __init__(self, token, owner, args):
        self.token, self.owner, self.args = token, owner, args
        self.api = os.environ.get("TELEGRAM_API") or "https://api.telegram.org"
        self.home = Path(os.environ.get("CEO_HOME") or "~/.ceo").expanduser()
        self.memory = self.home / "telegram-line.json"

    def send(self, text):
        request = urllib.request.Request(f"{self.api.rstrip('/')}/bot{self.token}/sendMessage",
                                         json.dumps(dict(chat_id=self.owner, text=text[:4096])).encode(),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as answer:
            said = json.load(answer)
        if said.get("ok") is not True:
            raise ValueError(f"sendMessage failed: {said.get('description', 'Bot API refused the call')}")

    def save(self, memory):
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.memory.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(memory, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.memory)

    def boards(self):
        found = {}
        for path in sorted((self.home / "board").glob("*.json")):
            try:
                one = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                continue  # a partial copy, unrelated JSON, or an unreadable file
            if (isinstance(one, dict) and isinstance(one.get("ceo"), str) and one["ceo"]
                    and isinstance(one.get("status"), dict) and isinstance(one.get("wave"), dict)):
                found.setdefault(one["ceo"], Project(one["ceo"], one))
        return found

    def load(self, now):
        """The memory and today's record in it, each local date keeping its own lines and digests, so a clock that
        steps back across midnight finds that date's allowance spent; with no file, today's passed digests are sent."""
        try:
            memory, first = json.loads(self.memory.read_text(encoding="utf-8")), False
        except FileNotFoundError:
            memory, first = {}, True
        if not (isinstance(memory, dict) and all(isinstance(memory.get(key, {}), dict) for key in ("projects", "days"))
                and isinstance(memory.get("before", ""), str)):
            raise ValueError("memory is not a record of projects and days")
        memory = dict(dict(projects={}, held=0, days={}, before=""), **memory)
        today, days = now.strftime("%Y-%m-%d"), sorted(memory["days"].items())
        memory["before"] = max([memory["before"]] + [date for date, _ in days[:-6]])  # the newest date forgotten
        memory["days"] = dict(days[-6:])  # the last six dates and today's; a forgotten date is spent, not fresh
        spent = today <= memory["before"]
        # a digest of an earlier day is never sent late
        memory["days"].setdefault(today, dict(sent=EVENTS if spent else 0, digests=[
            name for name, at in self.args.digests if spent or first and at <= now.time()]))
        return memory, memory["days"][today]

    def tell(self, now):
        (memory, day), todo, projects = self.load(now), [], self.boards()
        for name, project in projects.items():
            state = memory["projects"].get(name)
            if isinstance(state, dict) and all(isinstance(state.get(key), list) for key in NEWS):
                todo += [(*one, state) for one in project.news(state)]
            else:  # the first sight of a project: all of it is recorded, none told
                memory["projects"][name] = dict(asked=[row["slug"] for row in project.asking],
                                                bells=[bell["key"] for bell in project.bells],
                                                open=[plan["plan"] for plan in project.live])
            self.save(memory)
        for _, field, value, text, state in sorted(todo, key=lambda one: one[0]):  # the rank, a stable sort
            if day["sent"] < EVENTS:
                self.send(text)
                day["sent"] += 1
            else:
                memory["held"] += 1
            state[field] = [one for one in state[field] if one != value] if field == "open" else state[field] + [value]
            self.save(memory)
        due = [name for name, at in self.args.digests if name not in day["digests"] and at <= now.time()]
        if due:  # one digest, though both of today's times have passed
            lines = [one for one in (project.digest() for project in projects.values()) if one]
            if memory["held"]:
                lines.append(f"and {memory['held']} more event{'s' * (memory['held'] != 1)}")
            self.send("\n".join(lines or ["Nothing is open and nothing waits for you"]))
            day["digests"] += due
            memory["held"] = 0
        self.save(memory)

    def pass_once(self):
        try:
            self.tell(self.args.now or datetime.now())
        except Exception as why:
            report(why, self.token)
            return False
        return True

def moment(text):
    """The local time an ISO time names: one with an offset is moved to this machine's clock."""
    found = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return found.astimezone().replace(tzinfo=None) if found.tzinfo else found

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true")
    group.add_argument("--every", type=float)
    parser.add_argument("--morning", type=clock.fromisoformat, default=clock(9))
    parser.add_argument("--evening", type=clock.fromisoformat, default=clock(21))
    parser.add_argument("--now", type=moment)
    args = parser.parse_args()
    args.digests = (("morning", args.morning), ("evening", args.evening))
    token, owner = (os.environ.get(name, "").strip() for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"))
    for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_OWNER_ID", owner)):
        if not value or name.endswith("ID") and not value.isdigit():
            report(f"{name} is {'not set' if not value else 'not digits'}: nothing is sent", token)
            return 2
    if args.every is not None and not (math.isfinite(args.every) and args.every > 0):
        report("--every must be a positive number of seconds", token)
        return 2
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    ear = Ear(token, int(owner), args)
    if args.once:
        return 0 if ear.pass_once() else 1
    while True:
        ear.pass_once()
        time.sleep(args.every)

if __name__ == "__main__":
    sys.exit(main())
