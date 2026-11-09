"""`ceo.py score`'s last six rows, then the kernel's share (standard library and git only): escaped defects per
100 merges, owner touches per merge and the minutes main was red, measured from each repository's HEAD, then what the
agents cost per merge, read from its ledger. A day is the UTC day of a commit's committer date and a merge a
first-parent commit of HEAD whose subject ends in ` (#<pull request>)`, as for the rows above them. Each bug the week
files is a defect found on main after a merge. Claude's dollars and Codex's tokens are never put in one unit."""
import re
import subprocess
from datetime import datetime, time, timezone

from tools import ledger

ROWS = ("escaped per 100 merges", "owner touches per merge", "red-main minutes", "cost per merge, Claude",
        "tokens per merge, Codex", "agent runs unpriced")
MERGE = re.compile(r".* \(#\d+\)")  # the door's subject for the merge it makes
NIGHT = re.compile(r"routines/night-\d{4}-\d{2}-\d{2}\.md")
TESTS = "\n## The project's own tests, in a fresh clone of main\n"
# the section's opening: its command, as code() spans it over any lines it has, then how it ended
SAID = re.compile(r"(`+)(?:(?!\1).)+\1 (passed\.|exited |ran past )", re.S)

def read(repo, *args):
    """What git prints for args in repo, its paths unquoted; nothing when it fails, as with no commit yet."""
    done = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=repo, capture_output=True,
                          encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else ""

def passed(note):
    """True when a night note says the project's own tests passed, False when they exited or ran past their bound,
    None when it names none: no proof either way."""
    found = SAID.match(note.partition(TESTS)[2].lstrip())
    return found[2] == "passed." if found else None

def measure(repo, begin, finish, now):
    """repo's owner touches over begin..finish, UTC seconds, and the minutes main was red in it, cut to now: main is
    red from a night note whose tests did not pass to the next proof of green, a night note whose tests passed or,
    where HEAD has ./gate.sh, a door merge. The minutes are None when HEAD's history adds no night note."""
    log = [row.split("\0", 2) for row in read(repo, "log", "--first-parent", "--format=%ct%x00%cn%x00%s",
                                               "HEAD").split("\n") if row]
    touches = sum(begin <= int(at) < finish and name != "CEO kernel" and not MERGE.fullmatch(subject)
                  for at, name, subject in log)
    nights = {}  # each night note HEAD's history adds, at its oldest adding commit: (committer time, commit)
    for block in read(repo, "log", "HEAD", "--diff-filter=A", "--name-only", "--format=%x01%ct %H", "--",
                      "routines").split("\x01")[1:]:
        head, *paths = block.split("\n")
        at, commit = head.split()
        for path in filter(NIGHT.fullmatch, paths):
            nights[path] = min((int(at), commit), nights.get(path, (int(at), commit)))
    if not nights:
        return touches, None
    stop = min(finish, now)
    proofs = [(at, passed(read(repo, "show", f"{commit}:{path}"))) for path, (at, commit) in nights.items()
              if at < stop]
    if read(repo, "ls-tree", "--name-only", "HEAD", "--", "gate.sh").strip() == "gate.sh":
        proofs += [(int(at), True) for at, _, subject in log if int(at) < stop and MERGE.fullmatch(subject)]
    seconds, since = 0, None  # since: when main last turned red, None while it is green
    for at, green in sorted(proofs, key=lambda proof: proof[0]) + [(stop, True)]:
        if green and since is not None:
            seconds, since = seconds + max(0, at - max(since, begin)), None
        elif green is False and since is None:
            since = at
    return touches, round(seconds / 60)

def cells(bugs, merges, touches, minutes):
    """One folder's three cells, or their total's: each ratio a dash with no merge, then the counts it comes from."""
    ratio = lambda part, shown: f"{shown(part / merges) if merges else '–'} ({part} in {merges})"
    return (ratio(bugs, lambda share: round(100 * share)), ratio(touches, lambda share: f"{share:.1f}"),
            "no night" if minutes is None else str(minutes))

def dollars(usd):
    return f"${usd:.2f}"

def grouped(tokens):
    return f"{round(tokens):,}"

def percent(part, whole):
    return round(100 * part / whole) if whole else 0

def costs(spent, merges, start):
    """One folder's three cost cells, or their total's, from ledger.spend's spent: not recorded with no ledger, nor
    for the total unless each folder's ledger spans the week, whose first day is start; a ledger begun later is no
    part of it. Each ratio is a dash with no merge, then the sum it comes from."""
    if spent is None:
        return ("not recorded",) * 3
    first, usd, tokens, unpriced = spent
    if first > start:
        return (f"recorded from {first}",) * 3
    ratio = lambda part, shown: f"{shown(part / merges) if merges else '–'} ({shown(part)} in {merges})"
    return ratio(usd, dollars), ratio(tokens, grouped), str(unpriced)

def lines(repos, columns, start, end, kernel):
    """The lines ceo_score prints after its own rows: one row for each label of ROWS, a cell for each of repos and,
    for two or more, their total, then the kernel's share when kernel, its folder, is among two or more. columns
    are what ceo_score measured of each of repos, then of their total when there are two or more."""
    begin = datetime.combine(start, time(), timezone.utc).timestamp()
    finish, now = begin + 86400 * ((end - start).days + 1), datetime.now(timezone.utc).timestamp()
    found = [(column["bugs filed"], column["merges"], *measure(repo, begin, finish, now))
             for repo, column in zip(repos, columns)]
    spent = [ledger.spend(repo, start, end) for repo in repos]
    whole = all(one and one[0] <= start for one in spent)  # each folder's ledger spans the week
    if len(repos) > 1:  # red over the folders with nights alone, cost over all of them or none
        red = [minutes for *_, minutes in found if minutes is not None]
        found.append((*(sum(one[n] for one in found) for n in range(3)), sum(red) if red else None))
        spent.append((start, *(sum(one[n] for one in spent) for n in range(1, 4))) if whole else None)
    rows = [f"| {label} | {' | '.join(row)} |" for label, row in zip(ROWS, zip(*(
        cells(*one) + costs(sums, column["merges"], start) for one, sums, column in zip(found, spent, columns))))]
    mine = next(((column, sums) for repo, column, sums in zip(repos, columns, spent) if repo == kernel), None)
    if len(repos) > 1 and mine:  # the kernel is the instrument: its share is a cost, never output
        (column, sums), total, paid = mine, columns[-1], spent[-1]
        share = lambda row: f"{column[row]} of {total[row]} {row} ({percent(column[row], total[row])} %)"
        cost = lambda n, shown: f"{shown(sums[n])} of {shown(paid[n])} ({percent(sums[n], paid[n])} %)"
        tail = (f", {cost(1, dollars)} Claude cost, {cost(2, grouped)} Codex tokens." if whole
                else "; the agents' cost is not recorded for every folder.")
        rows += ["", f"The kernel's share, a cost: {share('merges')}, {share('changed lines')}{tail}"]
    return rows
