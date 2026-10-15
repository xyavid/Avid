"""The launcher: what it serves, what it writes into the document, and what it starts.

Nothing here spawns a real backend — that is the end-to-end evidence script's job. What is pinned
here is the part that would otherwise fail silently: the document's policy, the injected address,
and the origin the child is told to trust.
"""

from __future__ import annotations

import http.server
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

from avid.ui import browser, launch, server

BACKEND = "http://127.0.0.1:8765"
INDEX = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8" />'
    '<link rel="icon" href="/assets/favicon-abc123.svg" /></head>'
    '<body><div id="root"></div><script type="module" src="/assets/index-abc123.js"></script></body></html>'
)


@pytest.fixture
def dist(sandbox: Path) -> Path:
    root = sandbox / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("export const x = 1\n", encoding="utf-8")
    (root / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def ui(dist: Path):
    """A static host on an OS-assigned port, so tests never collide with a real one."""
    instance, stop = server.serve(dist, 0, BACKEND)
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        stop()


# ---------------- 静态宿主 ----------------


def test_the_document_carries_the_injected_address(ui: str):
    """The page learns where the backend is from a file, so the policy can stay script-src 'self'."""
    body = httpx.get(f"{ui}/").text
    assert server.BASE_SCRIPT in body
    # In front of the app's own module script, which is deferred and therefore runs later.
    assert body.index(server.BASE_SCRIPT) < body.index("/assets/index-abc123.js")


def test_the_injected_file_states_the_backend(ui: str):
    response = httpx.get(f"{ui}{server.BASE_SCRIPT}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert f'globalThis.__AVID_API_BASE__ = "{BACKEND}"' in response.text


def test_the_policy_reaches_both_schemes_of_the_backend(ui: str):
    """CSP matches http and ws separately, and the terminal is a WebSocket: missing ws is silent."""
    policy = httpx.get(f"{ui}/").headers["content-security-policy"]
    assert BACKEND in policy
    assert "ws://127.0.0.1:8765" in policy
    assert "connect-src 'self'" in policy
    assert "script-src 'self'" in policy


def test_a_deep_link_gets_the_shell_but_a_missing_asset_does_not(ui: str):
    deep = httpx.get(f"{ui}/settings")
    assert deep.status_code == 200 and "index-abc123" in deep.text
    missing = httpx.get(f"{ui}/assets/stale-000.js")
    assert missing.status_code == 404, "缺的资源不能拿 HTML 顶：浏览器会把它当脚本解析"
    assert "index-abc123" not in missing.text


def test_a_traversal_path_is_not_a_page(ui: str):
    """Percent-encoded dots survive the client and are unquoted here; resolving first is the proof.

    A raw `../` would be normalised away by the client, so this is the form that actually arrives.
    """
    response = httpx.get(f"{ui}/%2e%2e%2f%2e%2e%2fetc%2fpasswd")
    assert response.status_code == 404
    assert "root:" not in response.text


def test_an_unknown_extension_less_path_is_a_route_not_a_file(ui: str):
    """`/etc/passwd` normalised by the client is just another route: the shell is the right answer."""
    response = httpx.get(f"{ui}/etc/passwd")
    assert response.status_code == 200
    assert "root:" not in response.text


def test_fingerprinted_assets_are_cached_and_the_document_never_is(ui: str):
    asset = httpx.get(f"{ui}/assets/index-abc123.js")
    assert asset.headers["cache-control"] == server.ASSET_CACHE
    assert httpx.get(f"{ui}/").headers["cache-control"] == server.DOCUMENT_CACHE


def test_the_manifest_is_served_with_its_own_type(ui: str):
    """A wrong type here means the browser refuses to install the app."""
    response = httpx.get(f"{ui}/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/manifest+json"


def test_head_answers_with_headers_and_no_body(ui: str):
    response = httpx.head(f"{ui}/assets/index-abc123.js")
    assert response.status_code == 200
    assert int(response.headers["content-length"]) > 0
    assert response.content == b""


def test_inject_puts_the_tag_in_the_head_when_there_is_one():
    assert server.inject_base(INDEX).index(server.BASE_SCRIPT) > INDEX.index("<head>")
    headless = server.inject_base("<p>无 head</p>")
    assert headless.startswith("<script src=") and server.BASE_SCRIPT in headless


# ---------------- 找产物 ----------------


def test_find_dist_prefers_the_argument_then_the_environment(sandbox: Path, monkeypatch):
    explicit, environment = sandbox / "a", sandbox / "b"
    for path in (explicit, environment):
        path.mkdir()
        (path / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setenv(launch.DIST_ENV, str(environment))
    assert launch.find_dist(str(explicit)) == explicit
    assert launch.find_dist() == environment


def test_a_directory_without_a_built_index_is_not_a_build(sandbox: Path, monkeypatch):
    empty = sandbox / "empty"
    empty.mkdir()
    monkeypatch.setenv(launch.DIST_ENV, str(empty))
    monkeypatch.setattr(launch, "_CHECKOUT_DIST", sandbox / "nowhere")
    assert launch.find_dist() is None


# ---------------- 起后端 ----------------


def test_the_child_is_told_which_page_may_call_it(monkeypatch):
    """The terminal's allowlist has no loopback default, so the launcher's own page must be named."""
    seen: dict[str, object] = {}

    def fake_popen(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return object()

    monkeypatch.setattr(launch.subprocess, "Popen", fake_popen)
    launch.start_api(8765, "http://127.0.0.1:4173")

    assert seen["args"] == [sys.executable, "-m", "avid", "web", "--port", "8765"]
    env = seen["env"]
    assert isinstance(env, dict) and env[launch.ALLOWED_ORIGINS_ENV] == "http://127.0.0.1:4173"


def test_the_origin_variable_name_still_matches_the_web_layer():
    """The launcher duplicates this name to stay importable without the web extra; pin the copy."""
    from avid.web.app import ALLOWED_ORIGINS_ENV as web_name

    assert web_name == launch.ALLOWED_ORIGINS_ENV


def test_a_port_without_a_backend_is_not_up():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert launch.api_is_up(port) is False


class _Health(http.server.BaseHTTPRequestHandler):
    """Answers anything with 200, so a probe of this port can only be judged by how it got there."""

    def do_GET(self) -> None:  # noqa: N802 - the base class names the methods
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def test_a_running_backend_is_found_even_with_a_proxy_in_the_environment(monkeypatch):
    """The bug this pins: a proxied loopback probe times out, which reads as "nothing is there".

    The launch would then start a second backend and collide with the first — on the machine this
    was written on, that is exactly what happened, and the fix is to ignore `trust_env` here.
    """
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    with socket.socket() as dead:
        dead.bind(("127.0.0.1", 0))
        # A proxy that answers nothing: only a probe that goes through it can fail.
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{dead.getsockname()[1]}")
        monkeypatch.setenv("no_proxy", "")
        try:
            assert launch.api_is_up(int(fake.server_address[1])) is True
        finally:
            fake.shutdown()
            fake.server_close()


# ---------------- 启动与收尾 ----------------


def test_a_missing_build_names_the_command_that_makes_one(
    sandbox: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv(launch.DIST_ENV, str(sandbox / "nowhere"))
    monkeypatch.setattr(launch, "_CHECKOUT_DIST", sandbox / "nowhere")
    assert launch.launch_ui(open_window=False) == 2
    assert "pnpm -C web build" in capsys.readouterr().err


def test_a_taken_ui_port_says_which_flag_to_reach_for(
    dist: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv(launch.DIST_ENV, str(dist))
    # An API is already answering, so no child is spawned and the port collision is the only failure.
    monkeypatch.setattr(launch, "api_is_up", lambda port: True)
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        assert launch.launch_ui(ui_port=holder.getsockname()[1], open_window=False) == 1
    assert "--ui-port" in capsys.readouterr().err


def test_a_port_held_by_something_else_is_named_before_a_child_is_spawned(
    dist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Spawning a child into a taken port only moves the failure; the flag to reach for is the answer.

    This is how the machine this was written on behaved: 8765 was held on the Windows side of WSL,
    invisible to `/proc/net/tcp` and to a plain connect, but `bind` still refused.
    """
    started: list[int] = []
    monkeypatch.setenv(launch.DIST_ENV, str(dist))
    monkeypatch.setattr(launch, "api_is_up", lambda port: False)
    monkeypatch.setattr(launch, "start_api", lambda port, origin: started.append(port))
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        assert launch.launch_ui(api_port=holder.getsockname()[1], open_window=False) == 1
    assert started == [], "端口已被占，不该再去起子进程"
    assert "--port" in capsys.readouterr().err


def test_a_free_port_says_so():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert launch.port_is_free(port) is True


def test_a_child_that_outlives_the_command_would_hold_the_port():
    """Cleanup kills the process group: an orphaned API keeps the port and the next launch fails."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        launch._stop_child(child)
        assert child.poll() is not None
    finally:
        if child.poll() is None:  # pragma: no cover - only on a regression
            child.kill()


def test_reaching_the_exit_path_takes_that_child_down(
    dist: Path, monkeypatch: pytest.MonkeyPatch
):
    """The child it started is the child it stops — pinned by driving the real exit path.

    A real SIGINT arrives while the loop is sleeping, which is what Ctrl-C does; everything that
    would spawn a process is swapped out, so the only real thing here is the cleanup wiring.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    stopped: list[int] = []
    monkeypatch.setenv(launch.DIST_ENV, str(dist))
    monkeypatch.setattr(launch, "api_is_up", lambda port: False)
    # The port question is a different test's business, and leaving it real would make this one
    # depend on what happens to be running on the machine.
    monkeypatch.setattr(launch, "port_is_free", lambda port: True)
    monkeypatch.setattr(launch, "start_api", lambda port, origin: child)
    monkeypatch.setattr(launch, "wait_for_api", lambda port, proc, timeout=0.0: None)
    monkeypatch.setattr(launch, "_stop_child", lambda proc: stopped.append(proc.pid))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        ui_port = probe.getsockname()[1]

    timer = threading.Timer(0.5, lambda: signal.raise_signal(signal.SIGINT))
    timer.start()
    try:
        assert launch.launch_ui(ui_port=ui_port, open_window=False) == 0
        assert stopped == [child.pid]
    finally:
        timer.cancel()
        if child.poll() is None:  # pragma: no cover - the assertion above already failed
            child.kill()


# ---------------- 浏览器 ----------------


def test_an_explicit_browser_command_wins(monkeypatch):
    monkeypatch.setenv(browser.BROWSER_ENV, f"{sys.executable} --profile x")
    assert browser.resolve_command() == [sys.executable, "--profile", "x"]


def test_an_unknown_browser_command_resolves_to_nothing(monkeypatch):
    monkeypatch.setenv(browser.BROWSER_ENV, "definitely-not-a-browser-xyz")
    assert browser.resolve_command() is None


def test_windows_browsers_are_candidates_for_wsl(monkeypatch):
    """On WSL the browser is a Windows executable, and `--app=` crosses the interop layer fine."""
    monkeypatch.delenv(browser.BROWSER_ENV, raising=False)
    monkeypatch.setattr(
        browser, "_find", lambda candidate: candidate if candidate.endswith(".exe") else None
    )
    assert browser.resolve_command() == ["chrome.exe"]


def test_no_browser_at_all_is_reported_rather_than_guessed(monkeypatch):
    monkeypatch.delenv(browser.BROWSER_ENV, raising=False)
    monkeypatch.setattr(browser, "_find", lambda candidate: None)
    assert browser.resolve_command() is None


def test_without_a_chromium_the_system_browser_still_opens(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(browser.webbrowser, "open", lambda url: opened.append(url) or True)
    assert browser.open_window("http://127.0.0.1:4173/", None) == "browser-tab"
    assert opened == ["http://127.0.0.1:4173/"]
