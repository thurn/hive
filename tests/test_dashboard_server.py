"""Real source-selected HTTP and build processes against a disposable UI toolchain."""

import concurrent.futures
import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from test_source_selection import ROOT, commit, fixture, git

from hive.jsonvalue import parse, record
from hive.usage_store import UsageStore


def setup(root: Path) -> tuple[Path, dict[str, str]]:
    repository, environment = fixture(root)
    for package in ("hive", "hive_bootstrap"):
        shutil.copytree(
            ROOT / "src" / package,
            repository / "src" / package,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    shutil.copyfile(ROOT / "scripts/hive.py", repository / "scripts/hive.py")
    dashboard = repository / "dashboard"
    dashboard.mkdir()
    (dashboard / ".nvmrc").write_text("24.16.0\n")
    (dashboard / "package.json").write_text('{"name":"fixture","version":"1.0.0"}')
    (dashboard / "package-lock.json").write_text('{"lockfileVersion":3}')
    (dashboard / "message.txt").write_text("first")
    tools = root / "tools"
    tools.mkdir()
    scripts = {
        "node": "print('v24.16.0')\n",
        "npm": "import os,shutil,sys\nfrom pathlib import Path\nif Path(os.environ['TEST_NPM_FAILURE']).exists():sys.exit(1)\np=Path('node_modules/.bin');p.mkdir(parents=True)\nfor n in ('tsc','vite'):shutil.copyfile(Path(__file__).with_name(n),p/n);(p/n).chmod(0o755)\n",
        "tsc": "from pathlib import Path\nimport sys\nsys.exit(1 if Path('message.txt').read_text()=='typefail' else 0)\n",
        "vite": "import os,sys,time\nfrom pathlib import Path\nwith Path(os.environ['TEST_BUILDS']).open('a') as f:f.write('build\\n')\ntime.sleep(.15)\nmessage=Path('message.txt').read_text()\nif message=='codefail':sys.exit(1)\nout=Path(sys.argv[sys.argv.index('--outDir')+1]);out.mkdir();(out/'assets').mkdir()\nbase=sys.argv[sys.argv.index('--base')+1]\n(out/'index.html').write_text('<!doctype html><html><head><title>Fixture</title></head><body>'+message+'<script src=\"'+base+'assets/main.js\"></script></body></html>')\n(out/'assets/main.js').write_text('window.fixture=true')\n",
    }
    for name, source in scripts.items():
        path = tools / name
        path.write_text("#!/usr/bin/env python3\n" + source)
        path.chmod(0o755)
    environment.update(
        PATH=str(tools) + os.pathsep + environment["PATH"],
        TEST_BUILDS=str(root / "builds.txt"),
        TEST_NPM_FAILURE=str(root / "npm-failure"),
    )
    commit(repository, "test: dashboard fixture")
    with UsageStore(root / "local-state/telemetry.sqlite3").connect():
        pass
    return repository, environment


def call(environment: dict[str, str], *args: str) -> dict[str, object]:
    value = subprocess.run(
        [sys.executable, str(ROOT / "scripts/hive.py"), "dashboard", *args, "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if value.returncode:
        raise AssertionError(value.stderr + value.stdout)
    return record(parse(value.stdout))


def get(
    port: int,
    path: str = "/",
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=25)
    try:
        connection.request(method, path, headers=headers or {})
        value = connection.getresponse()
        return value.status, {k.lower(): v for k, v in value.getheaders()}, value.read()
    finally:
        connection.close()


@contextmanager
def server(root: Path, environment: dict[str, str]) -> Iterator[int]:
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    with (root / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts/hive.py"),
                "dashboard",
                "serve",
                "--port",
                str(port),
                "--json",
            ],
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    get(port, "/unknown")
                    break
                except OSError:
                    if process.poll() is not None:
                        raise AssertionError(
                            (root / "server.log").read_text()
                        ) from None
                    time.sleep(0.03)
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.returncode != 0:
            raise AssertionError((root / "server.log").read_text())


def ready(port: int, text: bytes) -> bytes:
    deadline = time.monotonic() + 10
    latest = b""
    while time.monotonic() < deadline:
        status, _, latest = get(port)
        if status == 200 and text in latest:
            return latest
        time.sleep(0.05)
    raise AssertionError(latest)


class DashboardServerTests(unittest.TestCase):
    def test_loopback_build_cache_security_and_source_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = setup(root)
            initial = git(repository, "rev-parse", "HEAD")
            tree = git(repository, "rev-parse", "HEAD:dashboard")
            with server(root, environment) as port:
                for headers in (
                    {"Host": "attacker.invalid"},
                    {"Sec-Fetch-Site": "cross-site"},
                    {"Sec-Fetch-Site": "same-site"},
                ):
                    status, response_headers, _ = get(port, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertIn(
                        "frame-ancestors 'none'",
                        response_headers["content-security-policy"],
                    )
                    self.assertNotIn("access-control-allow-origin", response_headers)
                self.assertEqual(get(port, method="POST")[0], 405)
                self.assertFalse((root / "builds.txt").exists())
                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    list(pool.map(lambda _: get(port), range(2)))
                ready(port, b"first")
                self.assertEqual((root / "builds.txt").read_text(), "build\n")
                self.assertEqual(get(port, "/bead/hv-abc")[0], 200)
                self.assertEqual(get(port, "/unknown")[0], 404)
                self.assertEqual(get(port, "/api/bead/../../secret")[0], 404)
                status, headers, body = get(port, f"/c/{tree}/assets/main.js")
                self.assertEqual((status, body), (200, b"window.fixture=true"))
                self.assertIn("immutable", headers["cache-control"])
                self.assertEqual(
                    get(port, f"/c/{tree}/assets/main.js", method="HEAD")[2], b""
                )
                built = root / "local-state/dashboard-builds" / tree
                (built / "escape.js").symlink_to(root / "bootstrap.json")
                (built / "linked").symlink_to(root)
                for path in (
                    f"/c/{tree}/%2e%2e/bootstrap.json",
                    f"/c/{tree}/%252e%252e/secret.js",
                    f"/c/{tree}/escape.js",
                    f"/c/{tree}/linked/secret.js",
                    f"/c/{tree}//assets/main.js",
                ):
                    self.assertEqual(get(port, path)[0], 404, path)
                status, _, body = get(port, "/api/status")
                self.assertEqual(status, 200)
                self.assertEqual(record(parse(body.decode()))["source_commit"], initial)
                with (repository / "src/hive/dashboard_api.py").open("a") as stream:
                    stream.write("\n# Python source update.\n")
                updated = commit(repository, "test: Python only")
                body = get(port, "/api/status")[2]
                self.assertEqual(record(parse(body.decode()))["source_commit"], updated)
                ready(port, b"first")
                self.assertEqual((root / "builds.txt").read_text(), "build\n")
                (repository / "dashboard/message.txt").write_text("second")
                commit(repository, "test: new UI tree")
                ready(port, b"second")
                self.assertEqual((root / "builds.txt").read_text().count("build"), 2)
                (repository / "dashboard/message.txt").write_text("codefail")
                broken = commit(repository, "test: broken UI tree")
                ready(port, b"failed")
                self.assertIn(b"second", get(port)[2])
                count = (root / "builds.txt").read_text().count("build")
                for _ in range(3):
                    get(port)
                self.assertEqual(
                    (root / "builds.txt").read_text().count("build"), count
                )
                self.assertEqual(
                    call(environment, "build", broken, "--retry")["code"],
                    "DashboardBuildFailed",
                )
                self.assertEqual(
                    (root / "builds.txt").read_text().count("build"), count + 1
                )

    def test_environment_failure_backoff_and_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = setup(root)
            source = git(repository, "rev-parse", "HEAD")
            tree = git(repository, "rev-parse", "HEAD:dashboard")
            (root / "npm-failure").touch()
            value = call(environment, "build", source)
            self.assertEqual(value["code"], "DashboardBuildFailed")
            self.assertTrue(value["retryable"])
            self.assertGreater(float(str(value["after"])), time.time() + 50)
            self.assertEqual(
                call(environment, "build", source)["code"], "DashboardBuildDeferred"
            )
            (root / "npm-failure").unlink()
            self.assertEqual(
                call(environment, "build", source, "--retry")["code"], "DashboardBuilt"
            )
            self.assertFalse(
                (
                    root / "local-state/dashboard-builds" / (tree + ".retry-after")
                ).exists()
            )
            self.assertFalse(
                (
                    root / "local-state/sources" / source / "dashboard/node_modules"
                ).exists()
            )
            self.assertEqual(call(environment, "build", source)["cached"], True)

    def test_header_api_timeouts_and_connection_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = setup(root)
            path = repository / "src/hive/dashboard_api.py"
            source = path.read_text().replace(
                "    envelope: dict[str, object] =",
                "    import time\n    time.sleep(12)\n    envelope: dict[str, object] =",
            )
            path.write_text(source)
            commit(repository, "test: slow selected API")
            with server(root, environment) as port:
                with concurrent.futures.ThreadPoolExecutor(5) as pool:
                    pending = [pool.submit(get, port, "/api/status") for _ in range(5)]
                    time.sleep(0.5)
                    self.assertEqual(get(port, "/unknown")[0], 404)
                    states = [future.result()[0] for future in pending]
                self.assertTrue(all(status in {503, 504} for status in states), states)
                self.assertIn(504, states)
                sockets = [
                    socket.create_connection(("127.0.0.1", port), timeout=7)
                    for _ in range(16)
                ]
                try:
                    for connection in sockets:
                        connection.sendall(b"GET / HTTP/1.1\r\n")
                    time.sleep(0.1)
                    self.assertEqual(get(port)[0], 503)
                    self.assertIn(b"408", sockets[0].recv(2048))
                finally:
                    for connection in sockets:
                        connection.close()

    def test_shutdown_reaps_build_descendants_and_temporary_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = setup(root)
            vite = root / "tools/vite"
            vite.write_text(
                "#!/usr/bin/env python3\nimport os,time\nfrom pathlib import Path\nPath(os.environ['TEST_BUILDS']).write_text(str(os.getpid()))\ntime.sleep(60)\n"
            )
            with server(root, environment) as port:
                get(port)
                deadline = time.monotonic() + 10
                while (
                    not (root / "builds.txt").exists() and time.monotonic() < deadline
                ):
                    time.sleep(0.03)
                pid = int((root / "builds.txt").read_text())
                os.kill(pid, 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            self.assertEqual(
                list((root / "local-state/dashboard-builds/tmp").iterdir()), []
            )
