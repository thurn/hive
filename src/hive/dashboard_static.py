"""Static assets are opened below one build, refusing every symlink component."""

import os
import stat
from pathlib import Path

MIME: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def asset(state: Path, tree: str, name: str) -> tuple[bytes, str] | None:
    mime = MIME.get(Path(name).suffix)
    parts = name.split("/")
    if mime is None or any(p in {"", ".", ".."} for p in parts):
        return None
    opened: list[int] = []
    try:
        root = os.open(
            state / "dashboard-builds", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        opened.append(root)
        directory = os.open(
            tree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
        )
        opened.append(directory)
        for part in parts[:-1]:
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            opened.append(directory)
        file = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        opened.append(file)
        info = os.fstat(file)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
            return None
        data = bytearray()
        while chunk := os.read(file, 65536):
            data.extend(chunk)
            if len(data) > 16 * 1024 * 1024:
                return None
        os.utime(opened[1], None)
        return bytes(data), mime
    except OSError:
        return None
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)
