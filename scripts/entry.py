"""Executed from a concrete source snapshot with an isolated Python path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if sys.argv[1:] == ["mcp"]:
    from hive.mcp_transport import main
else:
    from hive.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
