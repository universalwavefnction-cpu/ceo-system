# CEO kernel

## What it is

A small Python program that takes an approved order from a failing test to a reviewed,
tested merge. You write the goal and the command that proves it; agents build and review
the change in separate copies. Git holds the orders, approvals and results.
The [root README](../README.md) is the command and configuration reference.

## What it does on day one

Three behaviors surprise a stranger, so they are said here first, each with its switch:

- **Agents run without asking permission.** The default worker is
  `claude -p --permission-mode bypassPermissions --output-format stream-json --verbose`. To run another command, pass
  `--worker "<cmd>"`, name one in `ceo.toml`'s `worker`, or give an order a `## Worker`.
- **The night is on.** Between `02:00-06:00` local time the tick runs a read-only finder over the project, then
  commits and pushes what it finds to `main`: drafts, `waiting` rows and `routines/night-<date>.md`. Set
  `night = ""` in `ceo.toml` to turn it off.
- **The kernel's own `./gate.sh` touches your GitHub account.** Its `RealGitHub` test creates a private repository,
  `<your-login>/ceo-kernel-door-test`, on the account `gh` is logged into when it is missing, and never deletes it.
  No switch turns this test off today; it is skipped only when `gh` is not logged in, so run the gate with `gh`
  logged out:

  ```bash
  env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$(mktemp -d)" ./gate.sh
  ```

## Install

You need Python 3.10 or newer, git, GitHub's `gh` CLI logged in, and an agent CLI
(`claude` by default). Codex works as well: set `worker` in `ceo.toml` to
`codex exec --dangerously-bypass-approvals-and-sandbox -`, and
[HARNESSES.md](../HARNESSES.md) has a whole `ceo.toml` for Claude Code, Codex and Gemini.
Any command can be supplied through `--worker`, `--reviewer` and `--checker`. The kernel
uses Python's standard library; nothing else to install.

Clone this repository, replacing the URL below with its clone URL:

```bash
git clone <kernel-repository-url> ceo-kernel
kernel=$(cd ceo-kernel && pwd)
```

For the `ceo` command, link it into `~/.local/bin` and check that your shell finds it:

```bash
mkdir -p ~/.local/bin
ln -s "$kernel/tools/launch.py" ~/.local/bin/ceo
command -v ceo
```

When `command -v ceo` prints nothing, add `~/.local/bin` to your `PATH`.

In a project's folder, `ceo` reopens its CEO's last conversation in your agent CLI; `ceo new` starts a new one (the
first one too, in a project set up by hand). In a new folder it first runs `git init`, makes a private GitHub
repository with `gh`, and pushes `ceo.py init`'s commit there, one line a step; it starts no tick. From any
folder, `ceo <name>` does the same for the listed project of that name, or for `~/dev/<name>`, made when missing.
It never sets up your home folder, and asks before it commits into a repository that has history already. In a
terminal, a wave of dots collapses into the project's name while it sets up, and for one second as it reopens.

## A first project in five minutes

Use a separate git project with a `main` branch, an `origin` on GitHub you can push to,
and no existing `VISION.md`. From that project's root, run:

```bash
python3 "$kernel/ceo.py" init
```

Fill in `VISION.md` with the project's purpose and limits. Choose your agent commands.
For an order that names a `## Worker`, the reviewer must be of another vendor unless you set
`same_vendor_review = true` in `ceo.toml` for a trial with one provider. An order that names
no `## Worker` is reviewed by `--reviewer`, or else by the same default command as the worker,
and nothing refuses that. Save this as `orders/001-hello.md`. An order is
drafted in `drafts/orders/`, which the kernel never reads, and filed by moving it into `orders/`;
this first one goes straight there:

~~~markdown
# A hello file

## Goal
Add HELLO.md with one line of greeting.

## Done when
```bash
test -s HELLO.md
```

## Not asked
Anything else.
~~~

Filing is what starts it: commit it, push, and run the tick:

```bash
git add VISION.md ceo.toml orders/001-hello.md
git commit -m "File the hello order"
git push -u origin main
python3 "$kernel/ceo.py" tick --every 2
python3 "$kernel/ceo.py" status
```

The tick stays running until you stop it. Use a second terminal in the project for
status, or run status after stopping the tick. The project can supply `./gate.sh`
for checks that every merge must pass.

## How an order runs

The order's test must fail first in a clean clone. A worker builds the change in
`.ceo/wt/<slug>`, commits it and opens a pull request. A fresh reviewer checks it;
a finding counts only as a command that fails in another clean copy.

The merge door joins `main` in a clean clone, runs the order's test and `./gate.sh`
when the project has one, and pushes the merge. A checker compares the merge with
`VISION.md`. Orders have three rounds at most by default.

## What it never does

- Write code to `main` except through the merge door.
- Start a draft (`drafts/orders/`, `drafts/plans/`), or start a cut order.
- Load the service agents that `ceo.py keep` writes; it prints the load command for you.
- Pass `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_BASE_URL` to a model
  unless `keep_env` names them.

## License

[MIT](../LICENSE). Copyright (c) 2026 the ceo-kernel authors.
