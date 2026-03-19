#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
	source "$ROOT_DIR/.venv/bin/activate"
elif [ -f "$ROOT_DIR/.venv/Scripts/activate" ]; then
	source "$ROOT_DIR/.venv/Scripts/activate"
else
	echo "Error: .venv activation script not found (.venv/bin/activate or .venv/Scripts/activate)." >&2
	exit 1
fi

python3 -m main -a "$SCRIPT_DIR/welcome.yaml"
