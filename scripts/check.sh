#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
command -v fzf >/dev/null
command -v openssl >/dev/null
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m unittest discover -s tests -v
bash -n scripts/check.sh
sh -n .githooks/commit-msg
test -x scripts/check.sh
test -x .githooks/commit-msg
git diff --check

# Exercise the built distribution in a separate tool environment.
package_dir=$(mktemp -d "${TMPDIR:-/tmp}/erc20-balance-check.XXXXXX")
trap 'rm -rf -- "$package_dir"' EXIT
uv build --out-dir "$package_dir/dist"
UV_TOOL_DIR="$package_dir/tools" UV_TOOL_BIN_DIR="$package_dir/bin" \
  uv tool install "$package_dir"/dist/*.whl
"$package_dir/bin/erc20-balance" --version
"$package_dir/bin/erc20-balance" --help >/dev/null
ERC20_BALANCE_TEST_BINARY="$package_dir/bin/erc20-balance" \
  uv run --locked python -m unittest \
    tests.test_cli.Tests.test_real_terminal_flow \
    tests.test_cli.Tests.test_https_terminal_session_leaves_no_logs_or_cache -v
printf '\nChecks passed.\n'
