# How an order is written
One order is one Markdown file here, `<n>-<name>.md` in lowercase, built on branch `order/<n>-<name>`. An order or plan not yet filed is a draft in `drafts/orders/` or `drafts/plans/`, which the kernel never reads; filing moves it into `orders/` or `plans/`, in a commit, and that alone starts its work; a plan's `after` names its own earlier lines or an order of another filed plan, and its order starts once those merge; no other file here is an order.
It holds `# Title`, `## Goal`, `## Done when` (one fenced `bash` block, failing before work and passing after it) and `## Not asked`; malformed orders get a one-sentence refusal and create nothing.
A worker gets the order on standard input in its own worktree; the done-when runs with bash from the top after it. A first run's test before the worker runs once in a clean clone of its base, with no origin and no borrowed environment; passing there refuses the order.
`## Worker` may name its worker in one line: `<name>` of `workers.<name>` in `ceo.toml` or a command.
An order with `## Mocks` or `## Spins` is refused: the owner's picks between variants run on a pick-sheet page outside the kernel.
A sibling `.cut` (`001-login.cut` beside `001-login.md`) cuts it; the tick starts each filed order with no result, oldest filed first, never a cut one, or says why under `## Needs attention`.
