#!/usr/bin/env bash
# Quality gate (docs/design.md §10.4). Run before every commit.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

echo "== format";  uv run ruff format --check .
echo "== lint";    uv run ruff check .
echo "== types";   uv run pyright
echo "== layers";  uv run lint-imports
echo "== tests";   uv run pytest -q
