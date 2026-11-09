from ceo_init import *  # Orders and plans as the kernel reads them: parsed, snapshotted, in families; which may start.
SECTIONS = ("Goal", "Done when", "Not asked")
FILED = re.compile(r"orders/[0-9]+-[a-z0-9-]+\.md")  # an order; any other file in orders/ never starts

def parse_order(text):
    """Return title, test, variants, preview, worker and whether these are mocks; refuse bad orders."""
    sections, current, fenced, title = {}, None, False, ""
    for line in text.splitlines():
        # A '#' line inside a code fence is example text or a shell comment, not a heading.
        if line.startswith("```"):
            fenced = not fenced
        if not (fenced or title) and re.match(r"# \S", line):
            title = line[2:].strip()
        if not fenced and line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    if not title:
        raise Refused("the order has no '# Title' line")
    for name in SECTIONS:
        if not "\n".join(sections.get(name, [])).strip():
            raise Refused(f"the order's '## {name}' section is missing or empty")
    done_when = "\n".join(sections["Done when"]) + "\n"
    blocks = re.findall(r"^```bash[ \t]*\n(.*?)^```[ \t]*$", done_when, re.M | re.S)
    if len(blocks) != 1:
        raise Refused("the '## Done when' section must hold exactly one fenced bash block")
    if not blocks[0].strip():
        raise Refused("the '## Done when' block is empty")
    # One line, in backticks or not: a name under [workers] in ceo.toml, or a command.
    worker = list(filter(None, (line.strip().strip("`") for line in sections.get("Worker", []))))
    if "Worker" in sections and len(worker) != 1:
        raise Refused("the '## Worker' section must hold one line, a name under [workers] or a command")
    worker = worker[0] if worker else ""
    mocks = "Mocks" in sections
    if mocks and "Spins" in sections:
        raise Refused("an order cannot have both '## Mocks' and '## Spins'")
    if not mocks and "Spins" not in sections:
        return title, blocks[0], [], "", worker, False
    kind, minimum = ("Mocks", 1) if mocks else ("Spins", 2)
    spins = [re.sub(r"^([-*+]|\d+[.)])\s+", "", line.strip())
             for line in sections[kind] if line.strip()]
    paths = [Path(line.strip().strip("`")) for line in sections.get("Preview", []) if line.strip()]
    if not minimum <= len(spins) <= 6:
        raise Refused(f"the '## {kind}' section must hold {minimum} to 6 lines, one for each variant")
    if (len(paths) != 1 or paths[0].is_absolute() or ".." in paths[0].parts
            or paths[0].suffix.lower() not in (".html", ".png")):
        raise Refused("the '## Preview' section must name one .html or .png path inside the worktree")
    return title, blocks[0], spins, str(paths[0]), worker, mocks

def parse_plan(text):
    """Read landing order and dependencies; omitted concurrency means one order at a time."""
    title, limit, orders = "", None, []
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        if line.startswith("at once"):
            if limit is not None or not re.fullmatch(r"at once [0-9]+", line) or int(line[8:]) < 1:
                raise Refused("at once must be one whole number above 0")
            limit = int(line[8:])
        if not line.startswith("- "):
            continue
        found = re.fullmatch(r"- (orders/([0-9]{3})-[a-z0-9][a-z0-9-]*\.md)"
                            r"(?: after ([0-9]{3}(?:, *[0-9]{3})*))?(?: — (\S.*))?", line)
        if not found:
            raise Refused("every '- ' line must name an order, optional after numbers and a sentence")
        path, number, after, sentence = found.groups()
        if any(one["number"] == number for one in orders):
            raise Refused(f"order number {number} occurs twice in the plan")
        orders.append(dict(path=path, number=number, after=(after or "").replace(" ", "").split(",")
                           if after else [], sentence=sentence or ""))
    if not title or not orders:
        raise Refused("a plan needs a '# Title' line and at least one order")
    pending = {one["number"]: set(one["after"]) for one in orders}
    for after in pending.values():
        if missing := after - pending.keys():
            raise Refused(f"after {min(missing)} names an order outside the plan")
    visited = set()
    while pending:
        ready = {number for number, after in pending.items() if after <= visited}
        if not ready:
            raise Refused("the plan's after lines make a cycle")
        visited.update(ready)
        pending = {number: after for number, after in pending.items() if number not in ready}
    earlier = set()
    for one in orders:
        if not set(one["after"]) <= earlier:
            raise Refused("after must name earlier lines of the plan")
        earlier.add(one["number"])
    return dict(title=title, limit=limit or 1, orders=orders)

def source_orders(repo, source, cache=None):
    """Read top-level order blobs in two git processes; reuse immutable bytes within one pass."""
    key = (repo, source)
    if cache is not None and key in cache:
        return cache[key]
    tree = git("ls-tree", "-z", source, "--", "orders/", cwd=repo).stdout
    files, blobs = {}, []
    for entry in filter(None, tree.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, kind, oid = meta.split()
        if kind == "blob" and mode in ("100644", "100755") and Path(path).parent == Path("orders"):
            files[path] = b""
            if path.endswith(".md"):
                blobs.append((path, oid))
    if blobs:
        found = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, capture_output=True,
                               input="".join(oid + "\n" for _, oid in blobs).encode())
        if found.returncode:
            raise Refused("the source order blobs could not be read")
        data = io.BytesIO(found.stdout)
        for path, _ in blobs:
            size = int(data.readline().split()[2])
            files[path] = data.read(size)
            data.read(1)  # batch framing is byte-sized, even for Unicode or embedded newlines
    if cache is not None:
        cache[key] = files
    return files

def order_snapshot(repo, source=None, cache=None):
    """Round facts from the selected order tree and retained local execution records."""
    ceo, orders = repo / ".ceo", {}
    files = (source_orders(repo, source, cache) if source else
             {str(p.relative_to(repo)) for p in (repo / "orders").glob("*") if p.is_file()})
    for path in sorted(filter(FILED.fullmatch, files)):
        file, slug = repo / path, slugify(Path(path).stem)
        text = (files[path] if source else file.read_bytes()).decode("utf-8", errors="replace")
        header = text.split("\n## ", 1)[0]
        replaces = re.search(r"^REPLACES: (orders/[^\s]+\.md)\s*$", header, re.M)
        revises = re.search(r"^Revises: (orders/[^\s]+\.md)\s*$", header, re.M)
        round_ = re.search(r"^ROUND: ([0-9]+)\s*$", header, re.M)
        result = read_json(ceo / "runs" / slug / "result.json") or read_json(ceo / "swept" / slug)
        review = read_json(ceo / "runs" / slug / "review.json")
        picked = bool(result.get("mocks") and result.get("picked"))
        orders[path] = dict(replaces=replaces[1] if replaces else "", round=int(round_[1]) if round_ else 1,
                            revises=revises[1] if revises else "", text=text,
                            cut=str(Path(path).with_suffix(".cut")) in files or file.with_suffix(".cut").exists(),
                            merged=result.get("merged"), state=result.get("state", ""), picked=picked,
                            reviewed=bool(result.get("head") and not any(result.get(k) for k in ("merged", "attention", "cleared"))
                                          and review.get("verdict") == "survives"
                                          and review.get("commit") == result["head"]),
                            complete=(ceo / "landed-by-hand" / slug).exists() or picked)
    return orders

def order_families(orders):
    """Connected components by number or REPLACES, with one latest round per component."""
    groups, numbers = {}, {}
    for path, row in orders.items():
        groups.setdefault(path, set()).add(path)
        number = re.match(r"([0-9]+)-", Path(path).name)
        links = [row.get("replaces", "")]
        if number:
            links.append(numbers.setdefault(int(number[1]), path))
        for link in filter(None, links):
            joined = groups[path] | groups.get(link, {link})
            for member in joined:
                groups[member] = joined
    families = {}
    for path in orders:
        if path in families:
            continue
        members = groups[path] & orders.keys()
        pending = set(members)
        while pending:
            ready = [p for p in pending if orders[p].get("replaces") not in pending]
            latest = min(ready or pending, key=lambda p: (orders[p]["round"], p))
            pending.remove(latest)
        complete = any(orders[p].get("merged") or orders[p].get("complete") for p in members)
        pending = any(orders[p].get("reviewed") and not orders[p]["cut"] for p in members)
        family = dict(members=members, latest=latest, complete=bool(complete),
                      finished=bool(complete or orders[latest]["cut"] and not pending))
        families.update(dict.fromkeys(members, family))
    return families

def shared_numbers(orders):
    """STATUS.md lines: each first round a namesake holds back in one family, never run, cut or merged itself;
    a family has one first round of its own, so a second, however its later rounds link, is another order."""
    families = order_families(orders)
    for path, row in orders.items():
        family = families[path]
        firsts = sorted(p for p in family["members"] if orders[p]["round"] == 1 and not orders[p]["replaces"])
        if (path not in firsts or len(firsts) < 2 or path == family["latest"] and not family["finished"]
                or any(row.get(key) for key in ("merged", "complete", "cut", "state", "started", "phase"))):
            continue
        yield (f"- `{path}` is filed and cannot start: it shares number {Path(path).name.split('-', 1)[0]} with "
               + ", ".join(f"`{p}`" for p in firsts if p != path) + ", so they are read as rounds of one order"
               + ("" if family["finished"] else f" whose latest is `{family['latest']}`") + ": renumber it")

def order_active(repo, slug, source=None, for_merge=False, cache=None):
    path = f"orders/{slug}.md"
    if source and not order_active(repo, slug, for_merge=for_merge):
        return False  # a run's pinned source may predate a later round's landing or cut
    orders = order_snapshot(repo, source, cache)
    family = order_families(orders).get(path)
    if family is None:
        return True
    if family["finished"] or orders[path]["cut"]:
        return False
    if for_merge and orders[family["latest"]]["cut"] and orders[path].get("reviewed"):
        return True
    return family["latest"] == path

def added(repo, ref, folder, cache=None):
    """{path: committer time of ref's newest commit that added it} of each file under folder, a move included."""
    cache, key, at = {} if cache is None else cache, ("added", ref, folder), 0
    if key not in cache:
        cache[key] = {}
        for line in git("log", "--no-renames", "--diff-filter=A", "--format=time %ct", "--name-only", ref, "--",
                        f"{folder}/", cwd=repo).stdout.splitlines():
            if line.startswith("time "):
                at = int(line[5:])
            elif line:
                cache[key].setdefault(line, at)
    return cache[key]

def plan_snapshot(repo, source=None, cache=None):
    """Read once at the boundary; the decision below does no I/O and owns no process state. Given source,
    origin's main as the tick fetched it, the plans and their orders are as they stand in its tree, else in the
    folder. An order is filed when the commit that last added it to orders/ was made, else as its file was written."""
    ceo = repo / ".ceo"
    texts = {path: blob(repo, source, path) if source else (repo / path).read_text(encoding="utf-8", errors="replace")
             for path in sorted(listed(repo, source, "plans").splitlines() if source else
                                (f"plans/{p.name}" for p in (repo / "plans").glob("*.md") if p.is_file()))
             if re.fullmatch(r"plans/[^/]+\.md", path)}
    rounds = order_snapshot(repo, source, cache)
    families, filed = order_families(rounds), added(repo, source or "HEAD", "orders", cache)
    active = {(kind, slug) for kind, slug, _ in at_work(repo)}
    orders = {}
    for path, row in rounds.items():
        text = row["text"]
        slug = slugify(Path(path).stem)
        folder, invalid, capped = ceo / "runs" / slug, "", ""
        try:
            parse_order(text)
        except Refused as why:
            invalid = str(why)
        with contextlib.suppress(Refused):  # a round_cap that cannot be read refuses at the start, which says so
            capped = round_reason(repo, text) if families[path]["latest"] == path else ""
        phase = ("in review" if ("review", slug) in active or row["state"] == "done"
                 and not (folder / "review.json").exists() and not families[path]["finished"] else
                 "running" if ("run", slug) in active or held(ceo / "plan-starts" / slug) else "")
        # The door merges into origin's main, which the tick fetches; nobody pulls the folder's main at night.
        orders[path] = dict(row, valid=not invalid, invalid=invalid, capped=capped, phase=phase,
                            merged=bool(row["merged"] and any(git("merge-base", "--is-ancestor", row["merged"], ref,
                                                                 cwd=repo).returncode == 0
                                                             for ref in ("refs/heads/main", "refs/remotes/origin/main"))),
                            started=run_recorded(repo, slug), waiting=(ceo / "waiting" / f"{slug}.json").exists(),
                            filed=filed.get(path) or (0 if source else (repo / path).stat().st_mtime))
    plans, members = {}, set()
    for path, text in texts.items():
        # A plan that cannot be read still holds the orders it lists: none of them starts alone.
        members.update(re.findall(r"^- (orders/[0-9]{3}-[a-z0-9-]+\.md)", text, re.M))
        try:
            plans[path] = parse_plan(text)
        except Refused as why:
            plans[path] = dict(title=Path(path).stem, orders=[], limit=1, error=str(why))
    return dict(plans=plans, orders=orders, members=sorted(members))

def decide_plans(snapshot):
    """Select starts in landing order and show states from a restartable, file-derived snapshot; alone, the latest
    round of each filed family no plan lists, oldest filed first; capped, rounds over round_cap, which hold no slot
    and start() says why once; invalid, orders alone that cannot be read."""
    orders, plans = snapshot["orders"], snapshot["plans"]
    families = order_families(orders)
    merged, cut, phases, started = {}, {}, {}, {}
    for path, family in families.items():
        latest = family["latest"]
        merged[path] = family["complete"]
        cut[path] = orders[latest]["cut"] and family["finished"] and not any(
            orders[p].get("merged") or orders[p].get("complete") for p in family["members"] if p != latest)
        phases[path] = "" if family["complete"] else next((orders[p]["phase"] for p in sorted(
            family["members"]) if orders[p]["phase"]), "")
        started[path] = family["finished"] or orders[latest]["started"]
    states, candidates, owners, used, targets = {}, [], {}, {}, {}
    for name, plan in plans.items():
        rows = plan["orders"]
        used[name] = sum(bool(phases.get(one["path"])) for one in rows)
        ancestors = {}
        for one in rows:
            number, path = one["number"], one["path"]
            ancestors[number] = set(one["after"]).union(*(ancestors[n] for n in one["after"]))
            before = [row for row in rows if row["number"] in ancestors[number]]
            blocked = next((row["number"] for row in before if cut.get(row["path"])), "")
            waiting = next((row["number"] for row in before if not merged.get(row["path"])), "")
            own = orders.get(path, {})
            target = targets[name, path] = families[path]["latest"] if path in families else path
            states[name, path] = ("cut" if own.get("cut") else "merged" if own.get("merged") else
                                  own.get("phase") or own.get("state") or ("cut" if cut.get(path) else
                                  "blocked by cut " + blocked if blocked else "not in orders/" if path not in orders
                                  else "waiting for " + waiting if waiting else ""))
            owners.setdefault(target, []).append(name)
            if (not (cut.get(path) or blocked or waiting or started.get(path) or phases.get(path))
                    and orders.get(target, {}).get("valid") and not orders[target].get("waiting")):
                candidates.append((name, target))
    starts = []
    for name, path in candidates:
        belonging = owners[path]
        if (path not in starts and not orders[path].get("capped") and all((owner, path) in candidates for owner in belonging)
                and all(used[owner] < plans[owner]["limit"] for owner in belonging)):
            starts.append(path)
            for owner in belonging:
                used[owner] += 1
    for key, state in states.items():  # what an order no state above names waits for
        target = orders[targets[key]] if not state else {}
        states[key] = state or ("eligible" if targets[key] in starts else "started" if started[key[1]] else
                                "waiting for your word" if target.get("waiting") else
                                f"invalid: {target['invalid']}" if target.get("invalid") else
                                "over round_cap" if target.get("capped") else "waiting for its plan's slot")
    members = set(snapshot["members"])
    for path in snapshot["members"]:
        members.update(families.get(path, {}).get("members", ()))
    alone = sorted((path for path, family in families.items() if path == family["latest"] and path not in members
                    and not (started[path] or phases[path] or orders[path]["cut"] or orders[path].get("waiting"))),
                   key=lambda path: (orders[path].get("filed", 0), path))
    invalid = [path for path in alone if not orders[path].get("valid")]  # never eligible: it says why instead
    capped = [path for path in dict.fromkeys([*(path for _, path in candidates), *alone])
              if orders[path].get("capped") and path not in invalid]
    return dict(start=starts, alone=[path for path in alone if path not in capped + invalid], capped=capped,
                invalid=invalid, states=states, targets=targets, members=sorted(members))

def plan_lines(snapshot, decision, tried=None):
    """STATUS.md's ## Plans: each plan, each of its orders with its state, and why one eligible did not start (tried)."""
    for name, plan in snapshot["plans"].items():
        yield f"- `{name}` {plan['title']}" + (f": {plan['error']}" if plan.get("error") else "")
        for one in plan["orders"]:
            state, why = decision["states"][name, one["path"]], (tried or {}).get(decision["targets"][name, one["path"]])
            yield f"  - `{one['path']}` — {one['sentence']} — {state}" + (f", not started: {why}" if why else "")

def order_bytes(repo, order, source=None, cache=None):
    if not source:
        return order.read_bytes()
    if cache is not None:
        files = source_orders(repo, source, cache)
        if (path := order.relative_to(repo).as_posix()) not in files:
            raise Refused(f"there is no order file at {source}:{path}")
        return files[path]
    found = subprocess.run(["git", "show", f"{source}:{order.relative_to(repo).as_posix()}"],
                           cwd=repo, capture_output=True)
    if found.returncode:
        raise Refused(f"there is no order file at {source}:{order.relative_to(repo)}")
    return found.stdout

def prepare(order_arg, source=None):
    """Check everything that can be checked before anything exists on disk."""
    order = Path(order_arg).absolute() if source else Path(order_arg).resolve()
    if not source and not order.is_file():
        raise Refused(f"there is no order file at {order_arg}")
    repo = toplevel() if source else None
    raw = order_bytes(repo, order, source)
    try:
        title, done_when = parse_order(raw.decode("utf-8"))[:2]
    except UnicodeDecodeError:
        raise Refused("the order file is not UTF-8 text") from None
    top = git("rev-parse", "--show-toplevel", cwd=repo or order.parent)
    if top.returncode:
        raise Refused("the order file is not inside a git repository")
    repo = Path(top.stdout.strip()).resolve()
    slug = slugify(order.stem)
    if not slug:
        raise Refused("no slug can be made from the order file name")
    return (order, raw, title, done_when, repo, slug, repo / ".ceo" / "wt" / slug,
            repo / ".ceo" / "runs" / slug)

def round_reason(repo, text):
    """Why the round named in the order's header cannot start, or an empty string."""
    found = re.search(r"^ROUND:[ \t]*(\d+)[ \t]*\r?$", text.split("\n## ", 1)[0], re.M)
    cap = settings(repo).get("round_cap", 3)
    if type(cap) is not int or cap < 1:
        raise Refused("round_cap in ceo.toml must be a positive integer")
    return f"ROUND: {found[1]} exceeds round_cap {cap}" if found and int(found[1]) > cap else ""

def ran_before(repo, slug, spins=0):
    """Why a run of slug with its spins cannot begin, a worktree, record or branch of a run before it being there,
    or an empty string: none is, or its worker met an api error and it runs again in its worktree."""
    ceo, names = repo / ".ceo", [f"{slug}-spin-{n}" for n in range(1, spins + 1)] or [slug]
    if read_json(ceo / "runs" / slug / "result.json").get("state") == "retry":
        return ""
    for path in dict.fromkeys((ceo / "wt" / slug, ceo / "runs" / slug, *(ceo / "wt" / name for name in names))):
        if path.exists() and (path != ceo / "runs" / slug or run_recorded(repo, slug)):
            return f"this order has run before: {path.relative_to(repo)} exists"
    return next((f"this order has run before: its branch order/{name} exists" for name in dict.fromkeys((slug, *names))
                 if rev(repo, f"refs/heads/order/{name}")), "")

def at_work(repo):
    """(kind, slug, lock) of each run and review at work: its record is still to make, and
    a process holds its lock or, of a run, a process group it started lives."""
    ceo = repo / ".ceo"
    for kind, record in RECORD.items():
        for lock in sorted((ceo / "locks").glob(f"*.{kind}")):
            if to_make(ceo / "runs" / lock.stem / record) and (
                    held(lock) or kind == "run" and groups_alive(read_json(lock.with_suffix(".json")))):
                yield kind, lock.stem, lock
