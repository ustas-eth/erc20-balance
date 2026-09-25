# Contributing

The project uses Python 3.9+, `fzf`, and [uv](https://docs.astral.sh/uv/).
Install `openssl` for the local HTTPS integration test.

```sh
uv sync --group dev
git config core.hooksPath .githooks
./scripts/check.sh
```

`uv run erc20-balance` runs the checkout. `uv tool install --force .` updates a
local installation after a change is merged. Production code has no Python
runtime dependencies; development and screenshot tools stay in dependency groups.

## Commits and review

Commit headers use `type(scope): description`:

```text
feat(cli): make RPC selection explicit
fix(privacy): keep query details out of subprocess arguments
docs(repo): clarify installation requirements
```

Scopes are `cli`, `privacy`, `repo`, and `release`. The hook prints accepted types
and scopes when rejecting a message. Git-generated merge, revert, and autosquash
messages remain valid. Explain the problem solved or behavior protected; put
non-obvious reasons and tradeoffs in the commit body.

Run `./scripts/check.sh` before committing code. It checks formatting, lint,
local-fixture tests, and installation from a built wheel. Tests must not query
live RPCs or use personal wallet addresses or credentials.

Use a branch and pull request for behavior, compatibility, packaging, or version
changes. Squash-merge by default and delete the merged branch. Direct commits to
`main` are reserved for repository bootstrap and small maintenance that cannot
affect installed behavior. Publish by merging the reviewed change and updating
local installations; add tags/releases only when distribution needs them.

## Screenshots

```sh
uv sync --group dev --group screenshots
uv run --group screenshots python scripts/screenshots.py
```

The capture script drives real `fzf` menus against synthetic public metadata and
a local mock RPC. It renders captured terminal cells to PNG; balances are demo
data. Inspect the images before committing them.
