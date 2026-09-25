"""Source-selected UI builds use disposable copies and content-addressed dependencies."""

import hashlib
import html
import io
import json
import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn, TextIO

from hive.dashboard_routes import TREE
from hive.dashboard_static import asset
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, parse, record
from hive.launch_context import LaunchContext
from hive.locking import file_lock


class BuildFailure(Exception):
    def __init__(self, message: str, *, code: bool = False) -> None:
        super().__init__(message)
        self.code = code


def git(context: LaunchContext, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(context.repository), *args],
        check=True,
        capture_output=True,
        timeout=15,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout


def tree(context: LaunchContext, commit: str) -> str:
    if TREE.fullmatch(commit) is None:
        raise HiveError(ErrorCode.INVALID_INPUT, "Build requires a full commit hash")
    if git(context, "cat-file", "-t", commit).strip() != b"commit":
        raise HiveError(ErrorCode.INVALID_INPUT, "Build source must be a commit")
    value = git(context, "rev-parse", commit + ":dashboard").decode().strip()
    if TREE.fullmatch(value) is None:
        raise HiveError(ErrorCode.INVALID_INPUT, "No dashboard source tree")
    return value


def metadata(path: Path) -> dict[str, object]:
    try:
        return record(parse(path.read_text()))
    except (OSError, HiveError, ValueError):
        return {}


def write(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def command(args: list[str], cwd: Path, log: TextIO, *, code: bool = False) -> None:
    log.write("Running " + args[0] + "\n")
    log.flush()
    try:
        process = subprocess.Popen(
            args, cwd=cwd, stdout=log, stderr=log, process_group=0
        )
        try:
            result = process.wait(timeout=180)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildFailure(str(error)) from error
    if result:
        raise BuildFailure("Build command failed; see log", code=code)


def prune(state: Path) -> None:
    builds = state / "dashboard-builds"
    now = time.time()
    successful = sorted(
        (
            p
            for p in builds.iterdir()
            if TREE.fullmatch(p.name) and p.is_dir() and not p.is_symlink()
        ),
        key=lambda p: float(metadata(builds / (p.name + ".json")).get("built_at", 0)),
        reverse=True,
    )
    keep: set[str] = set()
    for index, path in enumerate(successful):
        if index < 5 or now - path.stat().st_mtime < 86400:
            lock = metadata(builds / (path.name + ".json")).get("lock")
            if isinstance(lock, str):
                keep.add(lock)
        else:
            shutil.rmtree(path)
            (builds / (path.name + ".json")).unlink(missing_ok=True)
    for marker in builds.glob("*.failed"):
        if now - marker.stat().st_mtime > 7 * 86400:
            marker.unlink()
    node = state / "dashboard-node"
    if node.exists():
        for path in node.iterdir():
            if (
                TREE.fullmatch(path.name)
                and path.name not in keep
                and path.is_dir()
                and not path.is_symlink()
            ):
                with file_lock(node / (path.name + ".lock"), timeout=0):
                    shutil.rmtree(path)


@contextmanager
def cancellation() -> Iterator[None]:
    def stop(_signal: int, _frame: object) -> NoReturn:
        raise HiveError(ErrorCode.CANCELLED, "Dashboard build cancelled")

    signals = (signal.SIGTERM, signal.SIGINT)
    previous = {s: signal.getsignal(s) for s in signals}
    try:
        for s in signals:
            signal.signal(s, stop)
        yield
    finally:
        for s in signals:
            signal.signal(s, previous[s])


def build(
    context: LaunchContext, commit: str, *, retry: bool = False
) -> dict[str, object]:
    with cancellation():
        return execute(context, commit, retry=retry)


def execute(
    context: LaunchContext, commit: str, *, retry: bool = False
) -> dict[str, object]:
    key = tree(context, commit)
    builds = context.state / "dashboard-builds"
    builds.mkdir(parents=True, exist_ok=True)
    failed = builds / (key + ".failed")
    delayed = builds / (key + ".retry-after")
    with (
        file_lock(builds / "build.lock", timeout=0),
        file_lock(builds / (key + ".lock"), timeout=0),
    ):
        if retry:
            failed.unlink(missing_ok=True)
            delayed.unlink(missing_ok=True)
        if asset(context.state, key, "index.html") is not None:
            return dict[str, object](code="DashboardBuilt", tree=key, cached=True)
        previous = metadata(delayed)
        if failed.exists() or float(previous.get("after", 0)) > time.time():
            return dict[str, object](
                code="DashboardBuildDeferred",
                tree=key,
                log=str(builds / (key + ".log")),
            )
        log_path = builds / (key + ".log")
        with log_path.open("w") as log:
            try:
                temporary_root = builds / "tmp"
                temporary_root.mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
                    root = Path(temporary)
                    with tarfile.open(
                        fileobj=io.BytesIO(
                            git(context, "archive", commit, "dashboard")
                        ),
                        mode="r:",
                    ) as archive:
                        archive.extractall(root, filter="data")
                    source = root / "dashboard"
                    required = (source / ".nvmrc").read_text().strip().removeprefix("v")
                    version = (
                        subprocess.run(
                            ["node", "--version"],
                            capture_output=True,
                            text=True,
                            check=True,
                            timeout=5,
                        )
                        .stdout.strip()
                        .removeprefix("v")
                    )
                    if version != required:
                        raise BuildFailure(
                            f"Node {required} is required; found {version}"
                        )
                    lock = hashlib.sha256(
                        (source / "package-lock.json").read_bytes()
                    ).hexdigest()
                    modules = context.state / "dashboard-node" / lock
                    with file_lock(modules.parent / (lock + ".lock"), timeout=5):
                        if not (modules / "node_modules").is_dir():
                            with tempfile.TemporaryDirectory(
                                dir=modules.parent
                            ) as dependency_temp:
                                install = Path(dependency_temp)
                                for name in ("package.json", "package-lock.json"):
                                    shutil.copyfile(source / name, install / name)
                                command(["npm", "ci", "--ignore-scripts"], install, log)
                                if not (install / "node_modules").is_dir():
                                    raise BuildFailure(
                                        "npm did not create node_modules"
                                    )
                                install.rename(modules)
                    (source / "node_modules").symlink_to(
                        modules / "node_modules", target_is_directory=True
                    )
                    command(
                        [str(source / "node_modules/.bin/tsc"), "-b"],
                        source,
                        log,
                        code=True,
                    )
                    output = root / "output"
                    command(
                        [
                            str(source / "node_modules/.bin/vite"),
                            "build",
                            "--base",
                            "/c/" + key + "/",
                            "--outDir",
                            str(output),
                        ],
                        source,
                        log,
                        code=True,
                    )
                    if not (output / "index.html").is_file():
                        raise BuildFailure("Build produced no index.html", code=True)
                    output.rename(builds / key)
                    write(
                        builds / (key + ".json"),
                        dict[str, object](
                            lock=lock, built_at=time.time(), commit=commit
                        ),
                    )
                failed.unlink(missing_ok=True)
                delayed.unlink(missing_ok=True)
                prune(context.state)
                return dict[str, object](code="DashboardBuilt", tree=key, cached=False)
            except (
                BuildFailure,
                OSError,
                subprocess.SubprocessError,
                tarfile.TarError,
            ) as error:
                log.write(str(error) + "\n")
                is_code = isinstance(error, BuildFailure) and error.code
                attempts = integer(previous.get("attempts", 0), "attempts") + 1
                value: dict[str, object] = dict[str, object](
                    message=str(error), log=str(log_path)
                )
                if not is_code:
                    value.update(
                        attempts=attempts,
                        after=time.time() + min(3600, 60 * 2 ** min(attempts - 1, 6)),
                    )
                write(failed if is_code else delayed, value)
                return dict[str, object](
                    code="DashboardBuildFailed",
                    tree=key,
                    retryable=not is_code,
                    **value,
                )


def page(context: LaunchContext) -> dict[str, object]:
    builds = context.state / "dashboard-builds"
    try:
        key = tree(context, context.commit)
    except subprocess.CalledProcessError:
        return dict[str, object](
            code="DashboardPage",
            status=503,
            html="<!doctype html><title>Hive</title><h1>Dashboard source is not available yet</h1>",
            build_commit=None,
        )
    current = asset(context.state, key, "index.html")
    if current is not None:
        return dict[str, object](
            code="DashboardPage",
            status=200,
            html=current[0].decode(),
            build_commit=None,
        )
    failure = metadata(builds / (key + ".failed"))
    retry = metadata(builds / (key + ".retry-after"))
    allowed = not failure and float(retry.get("after", 0)) <= time.time()
    build_commit = str(context.commit) if allowed else None
    previous = sorted(
        (p for p in builds.glob("*.json") if TREE.fullmatch(p.stem)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if failure or retry:
        message = f"UI build for {key} failed — showing an earlier version. Log: {builds / (key + '.log')}"
        for item in previous:
            fallback = asset(context.state, item.stem, "index.html")
            if fallback is not None:
                markup = fallback[0].decode()
                banner = '<aside role="alert">' + html.escape(message) + "</aside>"
                markup = markup.replace("<body>", "<body>" + banner, 1)
                return dict[str, object](
                    code="DashboardPage",
                    status=200,
                    html=markup,
                    build_commit=build_commit,
                )
        message += " Next retry: " + str(
            retry.get("after", "new UI tree or explicit retry")
        )
    else:
        message = "Building dashboard…"
    refresh = '<meta http-equiv="refresh" content="3">' if allowed else ""
    return dict[str, object](
        code="DashboardPage",
        status=503,
        html='<!doctype html><meta charset="utf-8">'
        + refresh
        + "<title>Hive</title><h1>"
        + html.escape(message)
        + "</h1>",
        build_commit=build_commit,
    )
