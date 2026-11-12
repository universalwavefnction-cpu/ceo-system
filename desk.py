"""The owner's status and answer commands over what the kernel records: the one line and the rows."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

from ceo_tick import (Refused, agents, asked_choices, awake, claude_home, events, git, groups_alive, held, now,
                      origin, read_json, toplevel, write_json)

def rows(repo):
    """One row for each order the kernel keeps a record of in repo: first those waiting for the owner's word,
    the oldest first, as their waiting rows were written; then the others, the newest first. Each says its
    state, one sentence from its title, what happens to it now in plain words, and since when."""
    ceo, remote = repo / ".ceo", origin(repo)
    slugs = {path.name for path in (ceo / "runs").glob("*") if path.is_dir()}
    slugs |= {path.stem for path in (ceo / "waiting").glob("*.json")}
    # By name first, so that rows of one time keep one order whatever the hash seed of this process.
    found = sorted((row(repo, slug, remote) for slug in sorted(slugs)), key=lambda one: one["since"] or "",
                   reverse=True)
    waiting = sorted((one for one in found if one["state"] == "waiting"), key=lambda one: str(one["since"] or ""))
    return waiting + [one for one in found if one["state"] != "waiting"]

def row(repo, slug, remote):
    ceo, run = repo / ".ceo", repo / ".ceo" / "runs" / slug
    result, review, meta, waiting = map(read_json, (run / "result.json", run / "review.json",
        ceo / "locks" / f"{slug}.json", ceo / "waiting" / f"{slug}.json"))
    title = str(result.get("title") or waiting.get("title") or meta.get("title") or slug)
    order = result.get("order") or waiting.get("order") or meta.get("order")
    times = [meta.get("started"), result.get("ended"), review.get("ended"), result.get("cleared")]
    one = {"slug": slug, "order": order, "sentence": title if title[-1:] in ".!?" else f"{title}.",
           "since": max(filter(None, times), default=None)}
    # A bug or a CEO's stop waits for a word; a row of mocks or spins, from before they left, asks nothing.
    if choices := asked_choices(waiting):
        answer = read_json(ceo / "answers" / f"{slug}.json")
        return {**one, "since": waiting.get("time") or one["since"], "state": "waiting",  # since it was asked
                "question": waiting.get("question"), "detail": waiting.get("detail"),
                "answered": answer.get("choice") if answer else None, "choices": choices}
    state, detail = situation(repo, slug, order, result, review, meta, remote)
    return {**one, "state": state, "detail": " ".join(str(detail).split()).rstrip(".")}

def situation(repo, slug, order, result, review, meta, remote):
    """(state, what happens now) of an order, as its records say."""
    if order and (repo / order).with_suffix(".cut").exists():
        return "finished", "cut; no more work goes into it"
    if not result:
        alive = held(repo / ".ceo" / "locks" / f"{slug}.run") or groups_alive(meta)
        return "in progress", "a worker is building it" if alive else "cut off; the next tick settles it"
    if result.get("state") == "waiting":  # its owner's pick, of mocks or spins, from before they left the kernel
        return "needs attention", f"{'mocks' if result.get('mocks') else 'spins'} left the kernel, so its pick is taken no more: cut it"
    if result.get("state") != "done":  # a bug's failed reproduction the owner answered is closed
        return ("finished", result["closed"]) if result.get("closed") else (
            "needs attention", result.get("reason") or "its test does not pass")
    if result.get("mocks"):
        return "finished", f"mock {result['picked']} picked; branch {result['branch']} kept for a new order"
    if result.get("attention"):
        return "needs attention", f"the door cannot merge it: {result['attention']}"
    if result.get("merged"):
        return "finished", "merged into main"
    verdict = review.get("verdict")
    found = sum(bool(finding.get("counts")) for finding in review.get("findings", []))
    if verdict == "fails":
        return "needs attention", f"a fresh reviewer found {found} problem{'s' * (found != 1)}"
    if verdict == "blocked":
        return "needs attention", f"its review is blocked: {review.get('reason')}"
    if verdict == "survives" and not remote:
        return "finished", "reviewed; with no origin, it is merged by hand"
    if verdict == "survives":
        return "in progress", "reviewed; the door merges it next"
    return "in progress", "a fresh reviewer checks it"

def line(repo, found, unread=0, holding=False):
    """The repository's one line: what runs, waits for the owner, needs attention, whether its tick holds
    the machine awake, or cannot be read."""
    count = Counter(one["state"] for one in found)
    said = [f"{count[state]} {words}" for state, words in (
        ("in progress", "running"), ("waiting", "waiting for you"),
        ("needs attention", f"need{'s' * (count['needs attention'] == 1)} attention")) if count[state]]
    lost = [f"{unread} unreadable line{'s' * (unread != 1)} in .ceo/events.jsonl"] if unread else []
    hold = ["holding the machine awake"] if holding else []
    return " · ".join([repo.name, *(said or ["nothing running"]), *hold, *lost])

def memory(repo):
    """repo's LESSONS.md, then the index of the Claude Code notebook its main folder and worktrees share, each
    over what a session loads, 200 lines or 25,000 bytes; read, never written. A missing file is not over."""
    common = (repo / git("rev-parse", "--git-common-dir", cwd=repo).stdout.strip()).resolve()
    main = common.parent if common.name == ".git" else repo  # its metadata kept apart (--separate-git-dir)
    notebook = claude_home() / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(main)) / "memory" / "MEMORY.md"
    sizes = [{"file": str(path), "lines": len(data.splitlines()), "bytes": len(data)}
             for path in (repo / "LESSONS.md", notebook) for data in [path.read_bytes() if path.is_file() else b""]]
    return [one for one in sizes if one["lines"] > 200 or one["bytes"] > 25000]

def too_long(one):
    """What status and the board say of a file memory() lists."""
    return (f"{Path(one['file']).name} is {one['lines']:,} line{'s' * (one['lines'] != 1)} and {one['bytes']:,} bytes, "
            f"over the 200 lines or 25,000 bytes a session loads: only its top loads ({one['file']})")

def answer(repo, slug, choice, comment=None):
    """Write the owner's choice for a row `status --json` shows waiting where the tick reads it,
    .ceo/answers/<slug>.json in its repository, from `ceo.py answer`; refuse
    any other row or choice. Return what was sent."""
    asked = next((one for one in rows(repo) if one["slug"] == slug and one["state"] == "waiting"), None)
    if asked is None:
        raise Refused(f"no row {slug} waits for your word in {repo.name}")
    chosen = next((one for one in asked["choices"] if str(one["pick"]) == str(choice)), None)
    if chosen is None:
        raise Refused(f"{choice} is not a choice of {slug}, whose choices are "
                      + ", ".join(str(one["pick"]) for one in asked["choices"]))
    if chosen.get("ask") and not (comment or "").strip():  # an answer to a CEO is its words
        raise Refused(f"{chosen['text']} needs your words: --comment \"<text>\"")
    (answers := repo / ".ceo" / "answers").mkdir(exist_ok=True)
    write_json(answers / f"{slug}.json", {"pick": chosen["pick"], "choice": chosen["text"], "time": now(),
                                          **({"comment": comment} if comment else {})})
    return f"sent {chosen['pick']} ({chosen['text']}) for {slug}; the next tick takes it"

def main(args):
    """`status` and `answer`, as ceo.py dispatches them."""
    repo = toplevel()
    if args.command == "answer":
        print(f"ceo: {answer(repo, args.slug, args.choice, args.comment)}.")
        return 0
    # --json: the rows, the agents at work now, the hold, the memory over what a session loads
    found, holding, over = rows(repo), awake(repo), memory(repo)
    red = ("\033[31m{}\033[0m" if sys.stdout.isatty() else "{}").format  # red only on a terminal
    print(json.dumps({"rows": found, "agents": agents(repo), "awake": holding, "memory": over}, indent=2,
                     ensure_ascii=False) if args.json
          else "\n".join([line(repo, found, events(repo)[1], holding), *(red(too_long(one)) for one in over)]))
    return 0
