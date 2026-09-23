"""Real noisy and stalled observation processes remain bounded and stoppable."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from mcp_fixture import assert_stopped

from hive.collection_transport import batch


class CollectionTransportTests(unittest.TestCase):
    def test_output_limits_and_timeout_drain_observation_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "child.py"
            launcher.write_text(
                "import sys\n"
                "sys.stdout.write('o' * 200000)\n"
                "sys.stderr.write('e' * 200000)\n"
            )
            result = asyncio.run(batch(launcher, root / "unused", 1))
            self.assertEqual(result["exit_code"], 0)
            self.assertTrue(result["truncated"])
            self.assertEqual(result["output"], "o" * 65_536)
            self.assertEqual(result["error"], "e" * 65_536)
            marker = root / "child-pid"
            launcher.write_text(
                "import os, sys, subprocess, time\nfrom pathlib import Path\n"
                "subprocess.Popen([sys.executable, '-c', "
                f'"import os, time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"])\n'
                "time.sleep(60)\n"
            )
            result = asyncio.run(batch(launcher, root / "unused", 1, timeout=2))
            self.assertTrue(result["timed_out"])
            self.assertNotEqual(result["exit_code"], 0)
            assert_stopped(int(marker.read_text()))
