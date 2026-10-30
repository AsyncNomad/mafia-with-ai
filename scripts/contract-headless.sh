#!/usr/bin/env bash
# Re-run the M0 harness contract against the installed Claude Code, headless (`claude -p`).
# Run this after every Claude Code update: the isolation guarantees rest on hook behaviour
# that is partly undocumented (see docs/harness-contract.md).
#
# Usage: scripts/contract-headless.sh [path/to/claude]
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
claude_bin="${1:-$(ls -d "$HOME"/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | sort -V | tail -1)}"
if [[ ! -x "$claude_bin" ]]; then
  echo "claude binary not found; pass its path as the first argument" >&2
  exit 2
fi

export PATH="$HOME/.local/bin:$PATH"
cd "$repo"
out_dir="runs/spike/contract-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out_dir"

uv run mafia spike-reset >/dev/null
rm -f runs/logs/hooks.jsonl
uv run mafia setup --spike --mode enforce >/dev/null

echo "claude: $claude_bin ($("$claude_bin" --version 2>/dev/null || echo unknown))"
# A clean environment keeps this run independent of any Claude Code session it is launched from.
(cd play && timeout 600 env -i HOME="$HOME" USER="$USER" PATH="/usr/local/bin:/usr/bin:/bin" \
  LANG=C.UTF-8 TERM=dumb "$claude_bin" -p "$(cat ../tests/contract/spike_prompt.md)" \
  --output-format json --model sonnet >"../$out_dir/main.json" 2>"../$out_dir/stderr.txt")

python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('result',''))" \
  "$out_dir/main.json" >"$out_dir/main.txt"
cp runs/logs/hooks.jsonl "$out_dir/hooks.jsonl"

uv run mafia spike-report --main-output "$out_dir/main.txt" | tee "$out_dir/report.md"
