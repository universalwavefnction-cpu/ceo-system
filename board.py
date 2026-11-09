"""The wave board: each CEO's status file, and one page rendered from such files by code, never by a model.

`ceo.py board --name <ceo>` writes the status file of the project it runs in: the CEO, its host, the time,
STATUS.md's last tick, what `ceo.py status --json` prints, its wave, the records it cannot read and its bells of
the last day (`ring` in ceo.py), which are no rows: the page shows them apart on its Board, after memory too long.
`ceo.py board --page <file>...` renders the page from those files alone. The wave is the newest filed plan and
every filed plan that still has an order neither settled (merged, picked or landed by hand) nor cut; each order
family counts once, however many of its rounds the plans list, as it stands. Each falls in the first class that
holds: merged; cut; needs you (its latest round's row waits for the owner's word); parked, which is the kernel's
`needs attention` (stopped with a reason: its test fails, a reviewer found problems, the door cannot merge it);
stuck, which is the kernel's stall record, .ceo/stalls/<slug>.json (`record_stalls` in ceo.py); else running. A
CEO is quiet when its last tick is missing or more than 15 minutes older than the page; a host is quiet when any
CEO of the files on it is, in every view that shows it, though a view's strip names only its own CEOs' hosts."""
import html
import json
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import desk
from ceo_score import utc
from ceo_tick import (STAMPS, Refused, added, agents, awake, now, order_families, order_snapshot, plan_snapshot,
                      read_json, slugify, toplevel, write_whole)

TEMPLATE = Path(__file__).resolve().parent / "board" / "page.html"  # the page's fixed CSS and script
MARK = "<!-- views -->"  # where the template takes the views
QUIET = timedelta(minutes=15)  # a pass under load takes minutes; a quarter of an hour without one is a stop
COUNTS = ("orders", "merged", "need-you", "stuck", "parked", "questions")
LISTS = ("need-you", "stuck", "parked", "merged", "question")

def last_tick(repo):
    """The time on STATUS.md's `Last tick:` line, or None."""
    path = repo / "STATUS.md"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    return next((line.split(": ", 1)[1].strip() for line in lines if STAMPS["tick"].match(line)), None)

def wave(repo, live=()):
    """The plans of the wave, the newest filed first, and their listed orders once per family, as it stands: number,
    latest round's slug, title and round, merged (on main), cut, stalled, picked (a mock, unmerged) and stage: none
    once settled, building, in review while live lists its reviewer, queued before a run record, else its state."""
    snapshot = plan_snapshot(repo)
    plans, rounds, filed = snapshot["plans"], snapshot["orders"], added(repo, "refs/heads/main", "plans")
    families = order_families(order_snapshot(repo))  # complete as recorded: cut and a settled plan mean what they did
    reviewing = {row["slug"] for row in live if row["role"] == "reviewer"}

    def standing(one):
        family = families.get(one["path"])
        latest = family["latest"] if family else one["path"]
        own, slug = rounds.get(latest, {}), slugify(Path(latest).stem)
        title = re.search(r"^# (\S.*)$", own.get("text", ""), re.M)
        merged = bool(family and any(rounds[path]["merged"] for path in family["members"]))
        picked = bool(family and not merged and any(rounds[path]["picked"] for path in family["members"]))
        cut = bool(family and own["cut"] and family["finished"] and not family["complete"])
        return dict(number=one["number"], order=one["path"], latest=slug, round=own.get("round", 1),
                    title=title[1].strip() if title else one["sentence"] or slug, merged=merged, cut=cut,
                    stalled=(repo / ".ceo" / "stalls" / f"{slug}.json").exists(), picked=picked,
                    stage="" if merged or picked or cut else "building" if own.get("phase") == "running" else
                    "in review" if slug in reviewing else own.get("state") or "queued")
    times = {path: filed.get(path) or (repo / path).stat().st_mtime for path in plans}  # filed on main, else written
    kept, orders, seen = [], [], set()
    for at, path in enumerate(sorted(plans, key=lambda path: (times[path], path), reverse=True)):
        listed = [standing(one) for one in plans[path]["orders"]]
        if at and all(one["cut"] or families.get(one["order"], {}).get("complete") for one in listed):
            continue  # an older plan settled through, merged, picked, landed by hand or cut, is no longer the wave
        kept.append(dict(plan=path, title=plans[path]["title"],
                         filed=datetime.fromtimestamp(times[path], timezone.utc).isoformat()))
        for one in listed:  # a family once, at the first place met, whichever of its rounds a plan lists
            if one["latest"] not in seen:
                seen.add(one["latest"])
                orders.append(one)
    return dict(plans=kept, orders=orders)

def status_file(repo, name, host=None):
    """The status file of the CEO name of repo, which is read and never written."""
    live = agents(repo)
    return dict(ceo=name, host=host or socket.gethostname().split(".")[0], time=now(), last_tick=last_tick(repo),
                status={"rows": desk.rows(repo), "agents": live, "awake": awake(repo),  # as `status --json`
                        "memory": desk.memory(repo)},
                wave=wave(repo, live), bells=rung(repo), unreadable=unreadable(repo))

def unreadable(repo):
    """The files of .ceo/*/*.json and .ceo/runs/*/*.json that hold no JSON object, which read_json reads as none."""
    paths = {*repo.glob(".ceo/*/*.json"), *repo.glob(".ceo/runs/*/*.json")}
    return sorted(str(path.relative_to(repo)) for path in paths if path.is_file() and read_json(path, None) is None)

def rung(repo):
    """The bells ring() wrote in the last day, the newest first."""
    found = [read_json(path) for path in (repo / ".ceo" / "bells").glob("*.json")]
    day = datetime.now(timezone.utc) - timedelta(days=1)
    return sorted((one for one in found if (utc(one.get("time")) or day) > day), key=lambda one: utc(one["time"]),
                  reverse=True)

def placed(order, row):
    """The one class of a wave order, the first that holds."""
    state = row.get("state")
    return ("merged" if order["merged"] else "cut" if order["cut"] else "need-you" if state == "waiting" else
            "parked" if state == "needs attention" else "stuck" if order["stalled"] else "running")

def one_line(text):
    return " ".join(str(text or "").split())

def ceo_of(one, at):
    """One CEO's own counts and rows, from its status file alone."""
    name, rows = one["ceo"], {row.get("slug"): row for row in one["status"]["rows"]}
    tick = utc(one.get("last_tick"))
    quiet = tick is None or at - tick > QUIET
    lists = {key: [] for key in LISTS}
    for order in one["wave"]["orders"]:
        row = rows.get(order["latest"]) or {}
        kind = placed(order, row)
        said = row.get("question") if kind == "need-you" else order["title"] if kind == "merged" else row.get("detail")
        if kind in lists:
            lists[kind].append(dict(ceo=name, number=order["number"], round=order["round"],
                                    line=one_line(said or order["title"]), quiet=quiet))
    for row in one["status"]["rows"]:
        if row.get("state") == "waiting":
            number = re.match(r"[0-9]+", str(row.get("slug")))
            lists["question"].append(dict(ceo=name, number=number[0] if number else str(row.get("slug")),
                                          round=None, line=one_line(row.get("question")), quiet=quiet))
    counts = {"orders": len(one["wave"]["orders"]), "questions": len(lists["question"]),
              **{key: len(lists[key]) for key in ("merged", "need-you", "stuck", "parked")}}
    plans = [dict(ceo=name, title=plan["title"]) for plan in one["wave"]["plans"]] or [dict(ceo=name, title=None)]
    bells = [dict(ceo=name, number=bell.get("kind"), round=None, line=one_line(bell.get("line")), quiet=quiet)
             for bell in one.get("bells", [])]  # none in the status file of a kernel before bells
    memory = [dict(ceo=name, number=Path(file["file"]).name, round=None, line=desk.too_long(file), quiet=False)
              for file in one["status"].get("memory", [])]  # red when quiet too; none from a kernel before it
    return dict(name=name, host=str(one.get("host")), tick=tick, quiet=quiet, counts=counts, lists=lists,
                plans=plans, bells=bells, memory=memory)

def seen(ticks, at):
    """The oldest of ticks in at's zone, its day too when not at's; "never" when one never ticked."""
    if None in ticks:
        return "never"
    oldest = min(ticks).astimezone(at.tzinfo)
    return oldest.strftime("%H:%M" if oldest.date() == at.date() else "%d %b %H:%M")

def views(files, at):
    """The page's views from the status files at the aware time at: All, then each CEO in the files' order.
    Every number is counted from the files; All's are the sums of its CEOs', its lists theirs joined."""
    ceos = [ceo_of(one, at) for one in files]
    chips = [dict(chip="all", label="All", count=sum(one["counts"]["orders"] for one in ceos), flag=False)] + [
        dict(chip=one["name"], label=one["name"], count=one["counts"]["orders"],
             flag=one["quiet"] or one["counts"]["need-you"] > 0 or bool(one["bells"] or one["memory"])) for one in ceos]

    def view(name, members):
        strip = []
        for host in dict.fromkeys(one["host"] for one in members):  # the view's hosts, each quiet over all its CEOs
            quiet = [one["tick"] for one in ceos if one["host"] == host and one["quiet"]]
            strip.append(dict(host=host, quiet=bool(quiet), seen=seen(quiet, at) if quiet else None))
        bells = [row for one in members for row in one["bells"]]  # apart from COUNTS and LISTS, as memory is
        memory = [row for one in members for row in one["memory"]]
        return dict(name=name, updated=at.strftime("%H:%M"), hosts=strip, chips=chips,
                    counts={**{key: sum(one["counts"][key] for one in members) for key in COUNTS}, "bells": len(bells),
                            "memory": len(memory)},
                    lists={key: [row for one in members for row in one["lists"][key]] for key in LISTS},
                    plans=[plan for one in members for plan in one["plans"]], bells=bells, memory=memory)
    return [view("all", ceos)] + [view(one["name"], [one]) for one in ceos]

def e(text):
    return html.escape(str(text), quote=True)

def count(view, key):
    return f'<b data-count="{key}">{view["counts"][key]}</b>'

def row_html(row, state, label=""):
    quiet = " data-quiet" if row["quiet"] else ""
    meta = e(row["ceo"]) + (f" · r{e(row['round'])}" if row["round"] else "")
    chip = f'<span class="state {state}">{label}</span>' if label else ""
    return (f'<li class="row" data-row="{e(row["ceo"])}/{e(row["number"])}" data-state="{state}"{quiet}>'
            f'<b class="num">{e(row["number"])}</b><div><small>{meta}</small><p class="line">{e(row["line"])}</p>'
            f'</div>{chip}</li>')

def section(view, title, key, state, label=""):
    """A heading with its count over the rows of a state; nothing when there are none."""
    if not view["lists"][state]:
        return ""
    return (f'<h2>{title} {count(view, key)}</h2><ul class="rows">'
            + "".join(row_html(row, state, label) for row in view["lists"][state]) + "</ul>")

def view_html(view, shown):
    """One view: its freshness strip, its chips, its Board and its Report."""
    late = any(host["quiet"] for host in view["hosts"])
    hosts = "".join(f'<span class="host" data-host="{e(host["host"])}"' + (" data-quiet" if host["quiet"] else "")
                    + f'>{e(host["host"])} <i class="dot"></i>'
                    + (f' last seen {e(host["seen"])}' if host["quiet"] else "") + "</span>" for host in view["hosts"])
    chips = "".join(f'<button type="button" class="chip" data-chip="{e(chip["chip"])}" aria-pressed="'
                    + ("true" if chip["chip"] == view["name"] else "false") + '"' + (" data-flag" if chip["flag"] else "")
                    + f'>{e(chip["label"])} <span>{chip["count"]}</span></button>' for chip in view["chips"])
    merged, orders, need = (view["counts"][key] for key in ("merged", "orders", "need-you"))
    bar = "".join('<i class="on"></i>' if at < merged else "<i></i>" for at in range(orders))
    none = "" if any(plan["title"] for plan in view["plans"]) else '<p class="none">No filed plan, so no wave</p>'
    hero = (f'<div class="card hero"><p class="label">Wave</p>{none}<p class="big">{count(view, "merged")} of '
            f'{count(view, "orders")} merged</p><div class="bar">{bar}</div><p class="need">{count(view, "need-you")} '
            + ("needs you" if need == 1 else "need you") + "</p></div>")
    memory = (f'<h2>Memory over what loads {count(view, "memory")}</h2><ul class="rows">' + "".join(
        row_html(row, "memory", "too long") for row in view["memory"]) + "</ul>") if view["memory"] else ""
    bells = (f'<h2>Bell {count(view, "bells")}</h2><ul class="rows">'
             + "".join(row_html(row, "bell", "bell") for row in view["bells"]) + "</ul>") if view["bells"] else ""
    board = (hero + memory + bells + section(view, "Needs you", "need-you", "need-you", "your call")
             + section(view, "Stuck", "stuck", "stuck", "stuck")
             + section(view, "Parked", "parked", "parked", "parked"))
    plans = "".join(f'<li><small>{e(plan["ceo"])}</small>' + (e(plan["title"]) if plan["title"] else "No filed plan")
                    + "</li>" for plan in view["plans"])
    report = (f'<h2>Wave</h2><ul class="plans">{plans}</ul>'
              + section(view, "Questions for you", "questions", "question")
              + section(view, "Merged", "merged", "merged")
              + section(view, "Parked, with reasons", "parked", "parked"))
    return (f'<section class="view" data-view="{e(view["name"])}"' + ("" if shown else " hidden") + ">"
            + '<p class="strip' + (" quiet" if late else "") + f'">Updated {e(view["updated"])}{hosts}</p>'
            + f'<nav class="chips">{chips}</nav><div data-tab="board">{board}</div>'
            + f'<div data-tab="report" hidden>{report}</div></section>\n')

def page(found, template):
    """The page: the template with every view rendered into it, All shown and every other view hidden."""
    head, tail = template.split(MARK)
    return head + "".join(view_html(view, not at) for at, view in enumerate(found)) + tail

def read(path):
    """The status file at path; refused when it cannot be read, is not JSON or is no status file."""
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as why:
        raise Refused(f"{path} cannot be read: {why.strerror}") from None
    except ValueError:
        raise Refused(f"{path} is not JSON") from None
    if not (isinstance(found, dict) and isinstance(found.get("ceo"), str) and found["ceo"]
            and isinstance(found.get("status"), dict) and isinstance(found.get("wave"), dict)):
        raise Refused(f"{path} is not a status file, as board --name writes one")
    return found

def named(name):
    if name.strip().lower() == "all":
        raise Refused("no CEO can be named all, which is the page's name for every CEO")
    return name

def main(args):
    """`ceo.py board`: a CEO's status file with --name, run in its project; the page with --page."""
    if bool(args.name) == bool(args.page):
        raise Refused("board takes either --name <ceo>, run in a project, or --page with the status files")
    if args.name:
        name = named(args.name)
        text = json.dumps(status_file(toplevel(), name, args.host), indent=2, ensure_ascii=False) + "\n"
    else:
        files = [read(path) for path in args.page]
        names = [named(one["ceo"]) for one in files]
        if twice := next((name for name in names if names.count(name) > 1), None):
            raise Refused(f"two of the files are {twice}'s, and a CEO has one file")
        text = page(views(files, datetime.now().astimezone()), TEMPLATE.read_text(encoding="utf-8"))
    if args.out:
        write_whole(Path(args.out), text)
    else:
        sys.stdout.write(text)
    return 0
