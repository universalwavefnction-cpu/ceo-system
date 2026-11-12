# Harnesses: Claude Code, Codex, Gemini

The kernel starts an agent CLI as a plain command: the brief on stdin, the verdicts as files and commits.
Any CLI that reads its task on stdin and works in the folder it starts in can build or review. Claude Code
and Codex are first-class; Gemini is an example, untested.

`worker` in `ceo.toml` is the default worker, the reviewer's fallback, the night's model and, without a
`ceo`, the CEO that decides stalls. Without it the worker is
`claude-auto -p --permission-mode bypassPermissions --output-format stream-json --verbose` while `claude-auto`
(which picks the Claude account that answers) is on PATH, else the same with `claude` while `claude` is.
`--worker`, `--reviewer` and `--model` on the command line win over it, and an order's `## Worker`
over them all. Each block below is a whole `ceo.toml`; `ceo.py init` writes the `name`.

## Claude Code

```toml
name = "my-project"
worker = "claude -p --permission-mode bypassPermissions --output-format stream-json --verbose"
session = "claude"
[workers]
opus = "claude -p --permission-mode bypassPermissions --output-format stream-json --verbose --model opus"
codex = "codex exec --dangerously-bypass-approvals-and-sandbox -"
[reviewers]
claude = "codex exec --dangerously-bypass-approvals-and-sandbox -"
openai = "claude -p --permission-mode bypassPermissions --output-format stream-json --verbose"
```

## Codex

```toml
name = "my-project"
worker = "codex exec --dangerously-bypass-approvals-and-sandbox -"
session = "codex"
[workers]
codex = "codex exec --dangerously-bypass-approvals-and-sandbox -"
opus = "claude -p --permission-mode bypassPermissions --output-format stream-json --verbose --model opus"
[reviewers]
openai = "claude -p --permission-mode bypassPermissions --output-format stream-json --verbose"
claude = "codex exec --dangerously-bypass-approvals-and-sandbox -"
```

`codex exec ... -` reads the brief on stdin. `--dangerously-bypass-approvals-and-sandbox` lets it run commands
and commit without asking, as a worker must; the kernel gives it a worktree or a clone of its own.

## Gemini (untested)

```toml
# untested: no run of the kernel has used Gemini yet
name = "my-project"
worker = "gemini --yolo"
session = "gemini"
[workers]
gemini = "gemini --yolo"
codex = "codex exec --dangerously-bypass-approvals-and-sandbox -"
[reviewers]
google = "codex exec --dangerously-bypass-approvals-and-sandbox -"
openai = "gemini --yolo"
```

## Vendors and cross-vendor review

A program's vendor is `vendor.<program>` in `ceo.toml`, named by the command's first word without its folder;
without it, `codex` is `openai`, `gemini` is `google`, and every other program `claude`. A wrapper with a
name of its own, such as `codex-auto`, needs its line: `vendor.codex-auto = "openai"`. An order whose
`## Worker` names a worker is reviewed by `reviewers.<worker name>`, else `reviewers.<vendor>`, else by the
worker itself, and a reviewer of the worker's own vendor is refused before anything starts unless
`same_vendor_review = true`; so each block maps every vendor it builds with to another. An order with no
`## Worker` is reviewed by `--reviewer`, else by `worker`, with no vendor check.

## API errors

A run or review that meets an API error is `retry` on the next pass, three times a day, then `blocked`.
Claude's: the last result line of its `stream-json` output says `terminal_reason` `api_error`. Every
harness, Codex and Gemini too: it exits in its first minute without an answer (a worker's commit or change,
a reviewer's `REVIEW.json`).

## Quota floor

`quota_cmd.<vendor>` is a command that prints the share of that vendor's quota left, 0 to 100; below
`quota_floor_pct` (5) no run, review or night of it starts. Unset, the vendor has no floor: Codex and Gemini have none until
you set `quota_cmd.openai` or `quota_cmd.google`.

## Login

Only claude's and claude-auto's login is probed: one turn, trusted ten minutes, and a failure holds only
that program's starts (a program you give `isolation.<program>` flags in `ceo.toml` is probed too). Any other
program, Codex and Gemini too, is said once in `.ceo/tick.log` to have no login probe, and a failed
login shows only as its runs failing. The kernel starts claude and claude-auto with
`--setting-sources project`; Codex and Gemini start with their user's own settings. `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_BASE_URL` and `GOOGLE_GEMINI_BASE_URL` are taken out of every agent's
environment unless `keep_env` names them; other API keys stay, as a Codex or Gemini user's own login.

## The wall

`tools/box CMD ARGS...` runs a command behind a wall, in the same folder, with the same input, output and exit
code; it is opt-in, and the kernel's code and default worker do not change. Behind it the command:
- writes only its own folder (the order's worktree), the git folders a commit there needs (the worktree's own
  folder in the main repository's `.git`, with its `objects`, `refs`, `logs` and `packed-refs`) and the temp
  folders; the main repository's `config`, `hooks` and index stay read-only;
- cannot read `~/.ssh`, gh's config folder, `~/.git-credentials`, `~/.netrc` or the SSH agent's socket, and gets
  no `SSH_AUTH_SOCK`, `GH_TOKEN` or `GITHUB_TOKEN`; the network stays open.

`sandbox-exec` builds the wall on macOS and `bwrap` (bubblewrap) on Linux. A host with neither, or whose bwrap may
not make user namespaces, refuses: exit 125, one line on stderr, the command never run unwalled. That is a
harness exiting in its first minute, so the kernel retries it as any such exit (see API errors), then blocks.
`BOX_TOOL=none` refuses anywhere.

```toml
worker = "/path/to/ceo-system/tools/box claude -p --permission-mode bypassPermissions --output-format stream-json --verbose"
isolation.box = "--setting-sources project"
```

The `worker` line names the box by an absolute path, in a checkout no worker writes: a relative `tools/box` would
start the worktree's own copy, which the worker may rewrite before its next round. The `isolation.box` line is
there because the kernel keys a command by its first word without its folder, and that word is now `box`: with
no line for it, claude loses `--setting-sources project`, so the user's own settings and hooks load in the
worker, and its login is never probed. A boxed command's vendor is `box`'s: `claude` unless `vendor.box` names
another, so a project boxes one vendor's programs.

What the wall does not stop:
- On macOS the login keychain stays open, Claude Code's own login being read through it, and so does a GitHub
  token kept there; only git's keychain helper is shut.
- Claude Code saves none of its state but its login file; its settings and hooks stay read-only, since the user's
  own sessions would run what a worker wrote there.
- Any other file the user can read stays readable.
- Any branch of the repository can still be moved, the refs being shared.
- What the kernel runs in the worktree afterwards (the done-when, the door's check, a reviewer's command) runs
  outside the wall.

## The session

`ceo.py session` opens the owner's session with `ceo.toml`'s `session`, `claude` by default, and gives it
`SESSION.md`:
- Claude, and any program not named `codex` or `gemini`: `--append-system-prompt <SESSION.md>`, and with
  `--continue` the last conversation of this folder, when Claude has one.
- Codex: `-c developer_instructions=<SESSION.md>`, its text as a JSON string, which `-c` reads as TOML;
  codex-cli 0.159.2 shows it as a developer message (`codex debug prompt-input`). `--continue` is
  `codex resume --last`.
- Gemini, with no system-prompt flag: `-i <SESSION.md>`, its first message, and the session stays open; always new.
