"""Prepare pinned test binaries without installing or starting host services."""

import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]


def ensure(binary: str, version: str, url: str) -> None:
    destination = ROOT / ".test-tools/bin" / binary
    existing = str(destination) if destination.exists() else shutil.which(binary)
    if existing is not None:
        result = subprocess.run(
            [existing, "version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and re.search(
            r"\b" + re.escape(version) + r"\b", result.stdout
        ):
            print(f"Using {binary} {version}: {existing}")
            return
    with tempfile.TemporaryDirectory(prefix="hive-test-tools-") as temporary:
        archive = Path(temporary) / "release.tar.gz"
        subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--retry",
                "2",
                "--silent",
                "--show-error",
                "--output",
                str(archive),
                url,
            ],
            check=True,
            timeout=120,
        )
        with tarfile.open(archive, "r:gz") as source:
            members = [
                item
                for item in source.getmembers()
                if item.isfile() and Path(item.name).name == binary
            ]
            if len(members) != 1:
                raise RuntimeError(f"Expected one {binary} executable in release")
            stream = source.extractfile(members[0])
            if stream is None:
                raise RuntimeError(f"Missing {binary} content")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with stream, destination.open("wb") as output:
                shutil.copyfileobj(stream, output)
        destination.chmod(0o755)
        subprocess.run([str(destination), "version"], check=True, timeout=10)


def main() -> None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = {"x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine
    )
    if system not in {"linux", "darwin"} or architecture is None:
        raise RuntimeError(f"Unsupported test platform: {system}/{machine}")
    ensure(
        "bd",
        "1.2.2",
        "https://github.com/gastownhall/beads/releases/download/v1.2.2/"
        f"beads_1.2.2_{system}_{architecture}.tar.gz",
    )
    ensure(
        "dolt",
        "2.2.0",
        "https://github.com/dolthub/dolt/releases/download/v2.2.0/"
        f"dolt-{system}-{architecture}.tar.gz",
    )


if __name__ == "__main__":
    main()
