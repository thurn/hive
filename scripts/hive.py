"""Stable standard-library launcher; application imports happen after selection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hive_bootstrap.launcher import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
