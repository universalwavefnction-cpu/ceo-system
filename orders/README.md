# How an order is written
One order is one Markdown file here, `<n>-<name>.md` in lowercase, built on branch `order/<n>-<name>`. An order or plan not yet filed is a draft in `drafts/orders/` or `drafts/plans/`, which the kernel never reads; filing moves it into `orders/` or `plans/`, in a commit, and that alone starts its work; no other file here is an order.
It holds `# Title`, `## Goal`, `## Done when` (one fenced `bash` block, failing before work and passing after it) and `## Not asked`; malformed orders get a one-sentence refusal and create nothing.
A worker gets the order on standard input in its own worktree; the done-when runs with bash from the top after it. A first run's test before the worker runs once in a clean clone of its base, with no origin and no borrowed environment; passing there refuses the order.
`## Worker` may name its worker in one line: `<name>` of `workers.<name>` in `ceo.toml` or a command.
Spins add `## Spins` (2 to 6 lines, how each differs) and `## Preview` (the `.html` or `.png` path each spin makes in its worktree); two or more finished lets the owner pick, else the order fails.
For the owner's visual pick, use `## Mocks` instead (1 to 6 lines): ONE worker draws all mocks on the order's branch in one worktree; an order cannot contain both sections.
For mocks, `## Preview` names a base path numbered before its extension (`preview.html` becomes `preview-1.html`, `preview-2.html`, etc., even for one mock); the worker's brief lists every required file.
The mock done-when must pass and every preview must exist, else the order fails; a pick records the choice and branch and clears the waiting row, keeping the branch without review, PR or merge; building it needs a new order.
A sibling `.cut` (`001-login.cut` beside `001-login.md`) cuts it; the tick starts each filed order with no result, oldest filed first, never a cut one, or says why under `## Needs attention`.
