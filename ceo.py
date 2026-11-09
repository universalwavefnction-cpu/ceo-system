#!/usr/bin/env python3
"""The CEO kernel: run, pick, review and merge (door) orders; keep them moving (tick)."""
import sys
sys.dont_write_bytecode = True  # never cached, as ceo.py never was
from ceo_tick import *  # every module of the kernel, each on the one below it
EXIT = {"done": 0, "survives": 0, "failed": 1, "fails": 1, "blocked": 2, "retry": 2, "waiting": 3}

def main(argv=None):
    parser = argparse.ArgumentParser(prog="ceo.py", description="Run, review, merge orders.")
    sub = parser.add_subparsers(dest="command", required=True)
    clock = argparse.ArgumentParser(add_help=False)
    clock.add_argument("--timeout", type=int, default=1800, help="seconds of each phase")
    for name, flag in (("run", "--worker"), ("review", "--reviewer")):
        cmd = sub.add_parser(name, parents=[clock])
        cmd.add_argument("order")
        cmd.add_argument(flag)
        cmd.add_argument("--source", help=argparse.SUPPRESS)  # the tick's immutable order and run base
    sub.add_parser("door", parents=[clock])
    sub.add_parser("decide", parents=[clock]).add_argument("key")
    sub.add_parser("sweep").add_argument("--dry-run", action="store_true")
    cmd = sub.add_parser("keep")
    cmd.add_argument("--remove", action="store_true")
    sub.add_parser("init").add_argument("path", nargs="?")
    sub.add_parser("status").add_argument("--json", action="store_true")
    cmd = sub.add_parser("pick")
    cmd.add_argument("slug")
    cmd.add_argument("spin", type=int)
    sub.add_parser("idea").add_argument("words", nargs="+")
    cmd = sub.add_parser("bug")
    cmd.add_argument("words", nargs="+")
    cmd.add_argument("--from", dest="source", choices=("owner", "server"), default="owner")
    cmd.add_argument("--attach", action="append", default=[], help="a text file to quote with the words")
    sub.add_parser("session").add_argument("--continue", dest="again", action="store_true")  # its last conversation
    sub.add_parser("open").add_argument("project")
    cmd = sub.add_parser("answer")
    cmd.add_argument("slug")
    cmd.add_argument("choice")
    cmd.add_argument("--comment")
    cmd = sub.add_parser("tick", parents=[clock])
    cmd.add_argument("--every", type=float)
    for flag in ("--worker", "--reviewer"):
        cmd.add_argument(flag)
    cmd.add_argument("--checker", help="accepted and ignored: the kernel makes no vision check")
    cmd = sub.add_parser("night")
    cmd.add_argument("--model")
    cmd.add_argument("--timeout", type=int, help="seconds at most; the night's window ends it anyway")
    cmd.add_argument("--max-findings", type=int, default=3)
    cmd = sub.add_parser("dream", parents=[clock])  # one pass of the cleaner over a page or a folder of notes
    cmd.add_argument("path")
    cmd.add_argument("--model")
    cmd = sub.add_parser("board")  # the wave board: a CEO's status file with --name, the page with --page
    cmd.add_argument("--name", help="the CEO whose status file this project writes")
    cmd.add_argument("--host", help="its host; the machine's short name by default")
    cmd.add_argument("--page", nargs="+", help="the status files the page is made of, in their order")
    cmd.add_argument("--out", help="the file to write whole; stdout by default")
    cmd = sub.add_parser("score")  # one week of each repository, measured from its git history
    cmd.add_argument("--from", dest="since", help="its first UTC day, YYYY-MM-DD; six days before --to by default")
    cmd.add_argument("--to", dest="until", help="its last UTC day, YYYY-MM-DD; yesterday by default")
    cmd.add_argument("paths", nargs="*", default=["."], help="a folder in each repository; this one by default")
    args = parser.parse_args(argv)
    if getattr(args, "timeout", None) is not None and args.timeout <= 0:  # none: the night's window
        parser.error("--timeout must be a positive number of seconds")
    if args.command == "tick" and args.every is not None and args.every <= 0:
        parser.error("--every must be a positive number of seconds")
    if args.command == "night" and args.max_findings < 1:
        parser.error("--max-findings must be 1 or more")
    for flag in ("worker", "reviewer", "model"):
        # Checked here, split only when started: the log names the command as it was given.
        given = getattr(args, flag, None)
        try:
            empty = given is not None and not shlex.split(given)
        except ValueError as err:
            parser.error(f"--{flag} is not a valid command: {err}")
        if empty:
            parser.error(f"--{flag} is empty")
    try:
        # Every command makes the owner's file, and run and review tell their model; but the owner's
        # own word, an idea, a bug or an answer, is kept with nothing else read.
        told = b"" if args.command in ("idea", "bug", "answer", "decide", "score") else owner()
        if args.command in ("status", "answer"):
            import desk  # the owner's surfaces, apart from the machinery that moves the work
            return desk.main(args)
        if args.command == "decide":
            ceo_decisions.run(toplevel(), args.key, args.timeout)
            return 0
        if args.command == "idea":
            print(idea(toplevel(), " ".join(args.words)))
            return 0
        if args.command == "night":
            print(f"ceo: {night(args, told)}.")
            return 0
        if args.command == "dream":
            print(f"ceo: dream: {(done := dream(args.path, args.model, args.timeout))[1]}.")
            return done[0]
        if args.command == "session":
            return session(args.again)
        if args.command == "open":
            return open_project(args.project)
        if args.command == "bug":
            print(f"ceo: {bug(toplevel(), ' '.join(args.words), args.source, args.attach)}.")
            return 0
        if args.command in ("tick", "door", "keep"):
            return {"tick": tick, "door": door, "keep": keep}[args.command](args)
        if args.command == "sweep":
            print(f"ceo: {sweep(toplevel(), args.dry_run)}")
            return 0
        if args.command == "init":
            return init(args.path)
        if args.command == "pick":
            print(f"ceo: {pick(toplevel(), args.slug, args.spin)}.")
            return 0
        if args.command == "board":
            import board
            return board.main(args)
        if args.command == "score":
            import ceo_score  # nothing else loads it
            return ceo_score.main(args)
        outcome = run(args, told) if args.command == "run" else inspect(args, told)
    except Refused as why:
        if args.command == "run":  # refused before its run began: a pass its order did not start
            refused(args.order, why)
        print(f"ceo: refused: {why}.", file=sys.stderr)
        return 2
    except Stopped as why:
        print(f"ceo: interrupted by {why}, so nothing is recorded and the next tick settles this "
              f"{args.command}: {LIMIT}", file=sys.stderr)
        return 2
    state = outcome.get("verdict") or outcome["state"]
    if outcome["reason"]:
        print(f"ceo: {outcome['reason'].rstrip('.')}.", file=sys.stderr)
    print(f"ceo: {state}: {Path(outcome['log']).with_name(RECORD[args.command])}")
    return EXIT[state]

if __name__ == "__main__":
    sys.exit(main())
