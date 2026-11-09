"""The ledger: what each agent spent, booked as it ends (standard library only). Every agent's end passes
ceo_base.api_error, which hands this module the agent's output; one line of <repo>/.ceo/ledger.jsonl says it,
for `ceo.py score` to read, beside any copied from another host as ledger-<host>.jsonl. The runs' own folders are
swept before their week is scored; the ledger stays."""
import contextlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RESULT = re.compile(rb'^\{.*"type": ?"result".*$', re.M)  # the Claude CLI's line at each turn's end
# Codex's last words: a line exactly "tokens used", the count on the next, grouped as the host's locale
# writes it (74,913 on one Linux host, 128.800 on the Mac). A log quoted back ("3346: tokens used") is no such line.
TOKENS = re.compile(rb"^tokens used\r?$\n?(?=(.*))", re.M)  # the count read, not taken: it may be one
COUNT = re.compile(r"\d+(?:[.,'\u2019\s]\d+)*")

def spent(raw):
    """What an agent's output bytes say it spent, (usd, tokens), each None when it does not say: usd sums each
    session's last total_cost_usd, which counts every turn before it; tokens is Codex's last count."""
    last = {}
    for line in RESULT.findall(raw):
        with contextlib.suppress(ValueError):
            found = json.loads(line)
            cost = found.get("total_cost_usd")
            if found.get("type") == "result" and isinstance(cost, (int, float)) and not isinstance(cost, bool):
                last[str(found.get("session_id"))] = cost  # a retry's new session adds to the one before it
    counts = TOKENS.findall(raw)
    count = counts[-1].decode("utf-8", "replace").strip() if counts else ""
    return (sum(last.values()) if last else None,
            int(re.sub(r"\D", "", count)) if COUNT.fullmatch(count) else None)

def book(log, raw):
    """Append what raw, the output of the agent writing log, spent to the ledger of the repository whose
    .ceo folder holds log: the nearest .ceo above it. A log with no name there books nothing, and a ledger
    that cannot be written is said on stderr: the agent's end stands without its line."""
    if not isinstance(getattr(log, "name", None), str):
        return  # an unnamed temporary file, as the login probe's
    path = Path(os.path.abspath(log.name))
    ceo = next((folder for folder in path.parents if folder.name == ".ceo"), None)
    if ceo is None:
        return
    usd, tokens = spent(raw)
    line = json.dumps({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "log": str(path.relative_to(ceo.parent)), "usd": usd, "tokens": tokens},
                      ensure_ascii=False)
    try:
        with open(ceo / "ledger.jsonl", "a", encoding="utf-8") as ledger:
            ledger.write(line + "\n")
    except OSError as err:
        print(f"ceo: the ledger did not book {line}: {err}", file=sys.stderr, flush=True)

def spend(repo, start, end):
    """What repo's ledgers, every .ceo/ledger*.jsonl, say its agents spent: (the UTC day of their earliest line, then
    over the lines of the UTC days start..end the usd summed, the tokens summed and the count saying neither); None
    with no ledger or no line read. A line that is no booking, as a torn last line, is skipped."""
    first, usd, tokens, unpriced = None, 0.0, 0, 0
    for path in sorted((Path(repo) / ".ceo").glob("ledger*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                found = json.loads(line)
                when = datetime.fromisoformat(found["time"].replace("Z", "+00:00"))
                cost, count = found["usd"], found["tokens"]
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if not all(value is None or isinstance(value, kind) and not isinstance(value, bool)
                       for value, kind in ((cost, (int, float)), (count, int))):
                continue
            day = (when.astimezone(timezone.utc) if when.tzinfo else when).date()
            first = min(day, first or day)
            if start <= day <= end:
                usd, tokens = usd + (cost or 0), tokens + (count or 0)
                unpriced += cost is None and count is None
    return None if first is None else (first, usd, tokens, unpriced)
