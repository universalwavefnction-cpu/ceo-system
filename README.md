# CEO kernel

You write what you want and the test that proves it. Agents do the rest, and nothing merges until it passes.
It is a small Python program, standard library only, that takes a written order to a reviewed, tested merge.

```text
  your order --> its test fails --> an agent builds --> a second agent checks
                                                                  |
     merged <-- pushed to main <-- the test passes again <--------+
```

The test must fail first, in a clean copy. One agent builds the change in a copy of its own and commits it. A fresh
agent reviews it in another copy, and a finding counts only as a command that fails there. Then main is joined in a
clean copy and the test runs again, with your own checks if you have them. Only then is it pushed. Merges need GitHub.

## Install and try it

```bash
git clone https://github.com/universalwavefnction-cpu/ceo-system ~/ceo-system && ~/ceo-system/install.sh
ceo try
```

The installer checks for Python 3.10 or newer, git, a logged-in GitHub CLI and an agent: Claude Code or Codex.
It names each fix for what is missing. It installs no package and edits no shell file. It links one command, ceo.
The try asks first. Then it makes a private GitHub repository and a folder for one ready-made order: a hello file.
You watch each step as it happens, from the failing test to the merge. At the end it prints the line that undoes it
all. Where this machine can build a wall around Claude, the try runs behind it. Otherwise it asks before going on.

## Your own project

```bash
ceo my-app
cd ~/dev/my-app
python3 ~/ceo-system/ceo.py tick --every 60
```

The first line makes the folder, a git repository and a private GitHub copy. It pushes the CEO's files and opens
the CEO. It never sets up your home folder. An existing repository with history is touched only on your yes. The last
line, run in a second terminal, starts the loop that runs your orders. It runs until you stop it.

## What happens on day one

- Agents act without asking. The default worker runs Claude Code with its permission prompts off, and with no wall.
- Every night from 02:00 to 06:00, a reader hunts for bugs. It files each one it proves, asking no one. Set
  `night = ""` in ceo.toml to stop it. Only a nightly tidy of the lessons page stays.
- A failed review gets a fix, filed by the kernel itself. Three tries at most.
- Only the merge step writes code to main. The kernel also records ideas, bug reports and its own orders there.
- The CEO files orders itself, under an intent you sign.

## What it never does without asking

- The CEO asks you three things only: your signature on an intent, money, and a new version going live on a server.
- It never starts a draft or an order you cut.
- It never loads the background service it writes for you. It prints the command instead.
- It never hands your Anthropic key, token or base URL to a model, unless you name them.

## Read on

[The reference](REFERENCE.md) has every command and setting. [HARNESSES.md](HARNESSES.md) sets up other agents.
Read [the CEO's rules](CEO.md), [how to write an order](orders/README.md) and the [MIT license](LICENSE).
