#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/prototype${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -c "from main import main; raise SystemExit(main())" "$@"
