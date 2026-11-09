"""`ceo.py score`: one week of each repository, measured the same way every time from its HEAD and its .ceo folder.
A day is the UTC day of a commit's committer date. A merge is a first-parent commit of HEAD whose subject ends in
` (#<pull request>)`, the door's name for it; the title before that links it to its order. Nothing is fetched."""
import contextlib
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ceo_tick import KERNEL, Refused, events, git, read_json
from tools import scorecard

ROWS = ("merges", "changed lines", "order files", "round files", "cut files", "bugs filed", "bugs closed",
        "round-1 share", "send to merge, median", "start to merge, median", "idle days", "waiting rows, now",
        "waiting age, median")
# What a path HEAD's history added counts as, the first that matches: an order file is no round file.
KINDS = {"round files": r"orders/\d+-(?:round-\d+|join-[0-9a-f]{7})\.md", "order files": r"orders/\d+-[^/]+\.md",
         "cut files": r"orders/[^/]+\.cut"}

def read(repo, *args):
    """What git prints for args in repo, its paths unquoted; nothing when it fails, as with no commit yet."""
    done = git("-c", "core.quotePath=false", *args, cwd=repo, encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else ""

def utc(stamp):
    """The UTC time an ISO stamp names, one without a zone read as UTC; None when it names none."""
    with contextlib.suppress(ValueError):
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)

def measure(repo, start, end):
    """Each row's samples in repo over start..end: a count, idle days, a list; None for a .ceo that repo lacks."""
    day = lambda at: datetime.fromtimestamp(int(at), timezone.utc).date()
    week = lambda at: start <= day(at) <= end
    log = [row.split(" ", 2) for row in read(repo, "log", "--first-parent", "--format=%H %ct %s", "HEAD").splitlines()]
    merges = [(commit, int(at), title[1]) for commit, at, subject in log
              if (title := re.fullmatch(r"(.*) \(#\d+\)", subject)) and week(at)]
    changed = sum(int(n) for commit, _, _ in merges  # a binary file's "-" counts 0
                  for row in read(repo, "diff", "--numstat", f"{commit}^1", commit).splitlines()
                  for n in row.split("\t")[:2] if n.isdigit())
    added, adds = {}, {}  # each path HEAD's history adds, at the committer time of its oldest adding commit; and all
    for block in read(repo, "log", "HEAD", "--no-renames", "--diff-filter=A", "--name-only", "--format=%x01%ct").split("\x01")[1:]:
        at, *paths = block.splitlines()
        for path in filter(None, paths):
            added[path] = min(int(at), added.get(path, int(at)))
            adds.setdefault(path, []).append(int(at))
    kinds = [next((kind for kind, pattern in KINDS.items() if re.fullmatch(pattern, path)), None)
             for path, at in added.items() if week(at)]
    trees = []  # the week's net result is the change between two trees of HEAD's first parent, not the walk between
    for test in (lambda at: day(at) < start, lambda at: day(at) <= end):  # them: the last before the week, and of it
        tip = next((commit for commit, at, _ in log if test(at)), None)
        paths = read(repo, "ls-tree", "-r", "--name-only", tip, "--", "bugs").splitlines() if tip else []
        shut = read(repo, "grep", "-lE", "^## Closed[ \t\r]*$", tip, "--", "bugs").splitlines() if tip else []
        bugs = {path for path in paths if re.fullmatch(r"bugs/\d+\.md", path)}
        trees.append((bugs, {path for path in bugs if f"{tip}:{path}" in shut}))  # its bugs, and those it closes
    filed, closed = (later - earlier for earlier, later in zip(*trees))  # a bug missing before is not closed before
    orders = {}  # each order's title, its `# ` line, to the order files that bear it and their heads
    for one in sorted((repo / "orders").glob("*.md")):
        text = one.read_text(encoding="utf-8", errors="replace")
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), None)
        orders.setdefault(title, []).append((one, text.split("\n## ", 1)[0]))
    first, sent = [], []
    for _, at, title in merges:
        if len(orders.get(title, ())) != 1:
            continue  # no order, or more than one, bears this title
        one, head = orders[title][0]
        first.append(not re.search(r"^(?:REPLACES|Revises):", head, re.M)
                     and all(int(n) <= 1 for n in re.findall(r"^ROUND:[ \t]*(\d+)", head, re.M)))
        # Sent: since, its first eligible pass, where this host has its record, else its last filing before the merge.
        ready = utc(read_json(repo / ".ceo" / "ready" / f"{one.stem}.json").get("since"))
        gone = ready.timestamp() if ready else max((t for t in adds.get(f"orders/{one.name}", ()) if t <= at), default=None)
        if gone is not None and gone <= at:
            sent.append((at - gone) / 3600)
    found = {"merges": len(merges), "changed lines": changed, **{kind: kinds.count(kind) for kind in KINDS},
             "bugs filed": len(filed), "bugs closed": len(closed), "round-1 share": first,
             "send to merge, median": sent, "idle days": {start + timedelta(n) for n in range((end - start).days + 1)}
             - {day(at) for _, at, _ in merges}}
    if (repo / ".ceo").is_dir():
        seen = [(one.get("event"), one.get("slug"), when) for one in events(repo)[0] if (when := utc(one.get("time")))]
        began = {slug: when for kind, slug, when in reversed(seen) if kind == "started"}  # each slug's first start
        merged = {slug: when for kind, slug, when in reversed(seen) if kind == "merged" and start <= when.date() <= end}
        found["start to merge, median"] = [(when - began[slug]).total_seconds() / 3600
                                           for slug, when in merged.items() if slug in began and began[slug] <= when]
        rows, clock = sorted((repo / ".ceo" / "waiting").glob("*.json")), datetime.now(timezone.utc)
        found["waiting rows, now"] = len(rows)
        found["waiting age, median"] = [(clock - when).total_seconds() / 3600 for row in rows
                                        if (when := utc(read_json(row).get("time")))]
    return found

def pool(values):
    """One row's samples over every repository: counts summed, the days idle in each, samples put together."""
    known = [value for value in values if value is not None]
    if not known or isinstance(known[0], int):
        return sum(known) if known else None
    return set.intersection(*known) if isinstance(known[0], set) else [one for value in known for one in value]

def cell(label, value):
    if isinstance(value, (int, set)):
        return str(len(value) if isinstance(value, set) else value)  # a set: the idle days
    if not value:
        return "–"  # no sample, or no .ceo folder to read the row from
    if label == "round-1 share":
        return f"{round(100 * sum(value) / len(value))} % ({sum(value)} of {len(value)})"
    return f"{statistics.median(value):.1f} h"

def main(args):
    given = lambda text: date.fromisoformat(text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else "-")
    try:
        end = given(args.until) if args.until else datetime.now(timezone.utc).date() - timedelta(days=1)
        start = given(args.since) if args.since else end - timedelta(days=6)
    except ValueError:
        raise Refused("--from and --to take a day as YYYY-MM-DD") from None
    if start > end:
        raise Refused("--from must not be after --to")
    repos = []
    for path in args.paths:
        if not (top := read(Path.cwd(), "-C", path, "rev-parse", "--show-toplevel").strip()):
            raise Refused(f"{path} is not a git repository")
        repos.append(Path(top).resolve())
    columns, names = [measure(repo, start, end) for repo in repos], [repo.name for repo in repos]
    if len(repos) > 1:
        columns.append({label: pool([column.get(label) for column in columns]) for label in ROWS})
        names.append("total")
    print(f"Week {start} to {end}, UTC days of the committer date, from each repository's HEAD.\n")
    print(f"| measure | {' | '.join(names)} |\n" + "|---" * (len(names) + 1) + "|")
    for label in ROWS:
        print(f"| {label} | {' | '.join(cell(label, column.get(label)) for column in columns)} |")
    print("".join(f"{line}\n" for line in scorecard.lines(repos, columns, start, end, KERNEL.parent)), end="")
    return 0
