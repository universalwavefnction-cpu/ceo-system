#!/usr/bin/env python3
"""Collect, on the owner's Mac, the proof that <ssh-host> carried its scratch order to a merged pull
request by itself, as tools/bootstrap-host.sh set it up there: results/019/<host>.json. Nothing is
written unless all of it holds; else one sentence says what does not.

    tools/collect-host.py <ssh-host>
"""
import json
import re
import subprocess
import sys
from pathlib import Path

# Run there with the PATH bootstrap-host.sh gives the kernel, so its python3 is the tick's.
PYTHON = 'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH" python3 -'
# Given the host's name: what the host is, and what its own tick recorded for the scratch order.
THERE = r"""
import json, os, platform, socket, subprocess, sys
from pathlib import Path
try:
    listed = Path(os.environ.get("CEO_HOME") or "~/.ceo").expanduser() / "projects.txt"
    found = [Path(line.strip()) for line in listed.read_text().splitlines()
             if Path(line.strip()).name == "ceo-trial-" + sys.argv[1]]
    if not found:
        sys.exit(f"{listed} lists no ceo-trial-{sys.argv[1]}")
    status = (found[0] / "STATUS.md").read_text().splitlines()
    print(json.dumps({
        "hostname": socket.gethostname(), "uname": " ".join(os.uname()), "python": platform.python_version(),
        "origin": subprocess.run(["git", "remote", "get-url", "origin"], cwd=found[0], capture_output=True,
                                 text=True).stdout.strip(),
        "status": [line for line in status if line.startswith(("Last tick: ", "Health: ", "Free: "))],
        "result": json.loads((found[0] / ".ceo" / "runs" / "001-hello" / "result.json").read_text())}))
except OSError as err:
    sys.exit(f"{err.strerror}: {err.filename}")
"""


def main(host):
    name = host.rsplit("@", 1)[-1]
    there = subprocess.run(["ssh", host, f"{PYTHON} {name}"], input=THERE, capture_output=True, text=True)
    if there.returncode:
        return f"collect-host: {name}: its proof cannot be read there: {' '.join(there.stderr.split())}"
    seen = json.loads(there.stdout)
    result, health = seen["result"], " ".join(line for line in seen["status"] if line.startswith("Health: "))
    github = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/*$", seen["origin"])
    if not github:
        return f"collect-host: {name}: its scratch project's origin is not on GitHub: {seen['origin']}."
    repository, pr = github.group(1), {}
    if result.get("pr"):  # as GitHub has it now, not as the host last saw it
        view = subprocess.run(["gh", "pr", "view", str(result["pr"]), "-R", repository, "--json",
                               "state,mergeCommit"], capture_output=True, text=True)
        pr = json.loads(view.stdout) if view.returncode == 0 else {"state": " ".join(view.stderr.split())}
    merged = (pr.get("mergeCommit") or {}).get("oid")
    wrong = [why for holds, why in (
        (result.get("state") == "done", f"its run of order 001 is {result.get('state')}"),
        (pr.get("state") == "MERGED", f"pull request #{result['pr']} is {pr.get('state')}"
         if result.get("pr") else "it has no pull request yet"),
        (merged and merged == result.get("merged"), "the merge commit is not the one its tick recorded"),
        ("login ok" in health, f"its STATUS.md says {health or 'no Health line'}")) if not holds]
    if wrong:
        return f"collect-host: {name}: no proof yet: {'; '.join(wrong)}."
    out = Path(__file__).resolve().parent.parent / "results" / "019" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    proof = {"host": name, "hostname": seen["hostname"], "uname": seen["uname"], "python": seen["python"],
             "repository": repository, "pr": result["pr"], "merged_commit": merged,
             "status": seen["status"], "result": result}
    out.write_text(json.dumps(proof, indent=2, ensure_ascii=False) + "\n")
    print(f"collect-host: {name}: wrote {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: tools/collect-host.py <ssh-host>")
    sys.exit(main(sys.argv[1]))
