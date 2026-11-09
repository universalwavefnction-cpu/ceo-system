#!/usr/bin/env python3
"""The kernel's public copy, made from one release (standard library only). Run in the private repository:

    tools/publish.py --to <repository> --author "<name> <id+login@users.noreply.github.com>" <release>
    tools/publish.py --dry-run <release>

It exports exactly the files public.txt lists in that commit, refuses if a leak scan finds a key or token, an
email address, an IP address, a home folder or a word of the private list, runs the export's own ./gate.sh in a
fresh folder with no repository around it and no GitHub login, and makes one commit on --to's main, authored and
committed by --author alone. A release already published there makes nothing. --dry-run exports and scans only, and
prints the files that would ship. The private list is $CEO_HOME/public-words.txt (default ~/.ceo), one name a line:
the clients and hosts that must never ship. It lives on the publishing machine, never in a repository; an empty file
says there are none. It runs by hand, on the commit given; no tick runs it. To make the public repository private
again:
    gh repo edit <owner>/<name> --visibility private --accept-visibility-change-consequences
"""
import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

LEAKS = {  # what never ships, whoever wrote it; a test that needs one builds it while it runs
    "a private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "a GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{30,})",
    "an API key": r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,})",
    "a bot token": r"\b\d{8,10}:[A-Za-z0-9_-]{35}(?![\w-])",
    "an email address": r"[\w.%+-]+@(?![\w.-]*(?:\.invalid|example\.(?:com|net|org)|users\.noreply\.github\.com)"
                        r"(?![\w.-]))[\w-]+(?:\.[\w-]+)+",
    "an IP address": r"(?<![\w.])(?!127\.0\.0\.1(?![\w.])|0\.0\.0\.0(?![\w.]))(?:\d{1,3}\.){3}\d{1,3}(?![\w.])",
    "a home folder": r"/(?:Users|home)/[\w.-]+",
}
NOREPLY = re.compile(r"(.+?) <([\w.+-]+@users\.noreply\.github\.com)>")


def git(*args, cwd=None, env=None):
    done = subprocess.run(["git", "--literal-pathspecs", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"publish: git {args[0]} failed: {' '.join(done.stderr.split())}")
    return done.stdout


def words():
    path = Path(os.environ.get("CEO_HOME") or Path.home() / ".ceo") / "public-words.txt"
    if not path.is_file():
        sys.exit(f"publish: no private word list at {path}: write the names that must never ship, one a line "
                 "(an empty file says there are none)")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def export(release, folder):
    """The files public.txt lists in release, written into folder; each must be a file there, never a folder."""
    listed = sorted({line.strip() for line in git("show", f"{release}:public.txt").splitlines()
                     if line.strip() and not line.startswith("#")})
    held = set(git("ls-tree", "-r", "-z", "--name-only", release).split("\0"))
    missing = [path for path in listed if path not in held]
    if missing:
        sys.exit(f"publish: public.txt lists what {release[:12]} holds as no file: {', '.join(missing)}")
    packed = subprocess.run(["git", "--literal-pathspecs", "archive", "--format=tar", release, "--", *listed],
                            capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(packed)) as tar:
        tar.extractall(folder, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return listed


def leaks(folder, listed, names):
    """Every line of the export that holds a leak, as `path:line: what`."""
    found = {what: re.compile(pattern) for what, pattern in LEAKS.items()}
    found.update({f"the private word {name!r}": re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.I)
                  for name in names})
    return [f"{path}:{number}: {what}" for path in listed
            for number, line in enumerate((folder / path).read_text("utf-8", "replace").splitlines(), 1)
            for what, pattern in found.items() if pattern.search(line)]


def tested(folder):
    """The export's ./gate.sh in a fresh copy: no repository above it, no GitHub login, a scratch CEO_HOME."""
    with tempfile.TemporaryDirectory() as away:
        copy = Path(away) / "copy"
        shutil.copytree(folder, copy, symlinks=True)
        env = {key: value for key, value in os.environ.items()
               if key not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GIT_DIR", "GIT_WORK_TREE")}
        env.update(CEO_HOME=str(Path(away) / "home"), GH_CONFIG_DIR=str(Path(away) / "gh"),
                   GIT_CEILING_DIRECTORIES=away)
        done = subprocess.run(["./gate.sh"], cwd=copy, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True)
        return done.returncode, (done.stdout + done.stderr).splitlines()[-40:]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tools/publish.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("release")
    parser.add_argument("--to")
    parser.add_argument("--author")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    author = NOREPLY.fullmatch(args.author or "")
    if not args.dry_run and not (args.to and author):
        sys.exit("publish: give --to <repository> and --author \"<name> <id+login@users.noreply.github.com>\"; "
                 "no other address may sign the public copy")
    release = git("rev-parse", "--verify", f"{args.release}^{{commit}}").strip()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "export"
        listed = export(release, folder)
        found = leaks(folder, listed, words())
        if found:
            sys.exit("publish: the leak scan refuses " + release[:12] + ":\n" + "\n".join(found))
        if args.dry_run:
            print("\n".join(listed))
            print(f"publish: {len(listed)} files of {release[:12]} would ship; the leak scan found nothing")
            return 0
        target, index = Path(tmp) / "public.git", {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("clone", "-q", "--bare", args.to, str(target))  # an empty repository clones too
        tip = subprocess.run(["git", "--git-dir", str(target), "rev-parse", "-q", "--verify", "refs/heads/main"],
                             capture_output=True, text=True).stdout.strip()
        git("--git-dir", str(target), "--work-tree", str(folder), "-c", "core.autocrlf=false", "add", "-f", "--",
            *listed, env=index)
        tree, subject = git("--git-dir", str(target), "write-tree", env=index).strip(), f"Release {release}"
        if tip and git("--git-dir", str(target), "log", "-1", "--format=%T %s", tip).strip() == f"{tree} {subject}":
            print(f"publish: {release[:12]} is already the newest commit of {args.to}; nothing to do")
            return 0
        code, tail = tested(folder)
        if code:
            sys.exit(f"publish: the export's ./gate.sh exited {code} in a fresh folder:\n" + "\n".join(tail))
        print("publish: the export's ./gate.sh passed in a fresh folder: "
              + " ".join(line for line in tail[-4:] if line))
        name, email = author.groups()
        signed = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
                  "GIT_COMMITTER_EMAIL": email}
        commit = git("--git-dir", str(target), "commit-tree", "--no-gpg-sign", tree, *(["-p", tip] if tip else []),
                     "-m", subject, env=signed).strip()
        git("--git-dir", str(target), "push", "-q", "origin", f"{commit}:refs/heads/main")
    print(f"publish: {release[:12]} is now the newest commit of {args.to}, {commit[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
