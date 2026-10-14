"""Process teardown: leaving ``avid web`` must not drop the runs still in flight.

Failure list pinned here: nothing but the app's lifespan ends the assembled services (a bare
``uvicorn.run`` with no shutdown step leaves the index worker and the session handles behind);
a run in flight is cancelled and waited for *before* the layers it writes through are closed;
teardown is safe to run twice, since a caller may have closed the services itself already.
"""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient
from support import ScriptedChat, create_session, make_turn, new_session, tool_call, wait_for

from avid.services import Services
from avid.web import create_app

#: Points nowhere, so no local frontend build can make an assertion environment-dependent.
NO_STATIC = "static-not-built"
BASE_URL = "http://127.0.0.1:8765"


def _services(sandbox, **kwargs) -> Services:
    return Services(root=sandbox / ".avid" / "sessions", **kwargs)


def test_shutdown_cancels_the_run_in_flight_and_waits_for_it(sandbox):
    """Ctrl-C reaches the app as a lifespan shutdown, and it must cancel what is running and wait:
    the session store is closed right after, so a run still alive loses its last write.
    """
    started = threading.Event()

    def gated(arguments, **kwargs):
        started.set()
        assert wait_for(lambda: kwargs["state"].cancelled, 5), "取消没到工具"
        return "ok"

    services = _services(
        sandbox,
        chat=ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("完")),
        tool_registry={"read_file": gated},
    )
    app = create_app(services=services, static_dir=sandbox / NO_STATIC)

    with TestClient(app, base_url=BASE_URL) as client:
        session = create_session(client).json()
        created = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "跑一下", "auto_approve": True},
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        assert started.wait(5), "运行没走到工具那一步"

    record = services.runs.get(run_id)
    assert record.status == "cancelled", record.status
    assert services.runs.active_runs() == []


def test_the_session_store_closes_only_after_the_run_has_settled(sandbox, monkeypatch):
    """The wait is the point, not the cancellation: a run settling after the store closed would
    lose the write it makes on the way out, so the store must go last.
    """
    started = threading.Event()

    def gated(arguments, **kwargs):
        started.set()
        assert wait_for(lambda: kwargs["state"].cancelled, 5), "取消没到工具"
        return "ok"

    services = _services(
        sandbox,
        chat=ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("完")),
        tool_registry={"read_file": gated},
    )
    record = services.runs.start(new_session(services), "跑一下")
    assert started.wait(5), "运行没走到工具那一步"

    settled_at_close: list[bool] = []
    repository_close = type(services.workspaces).close

    def spy(self) -> None:
        settled_at_close.append(record.terminal)
        repository_close(self)

    monkeypatch.setattr(type(services.workspaces), "close", spy)
    services.close()

    assert settled_at_close == [True], settled_at_close


def test_teardown_runs_twice_without_complaint(sandbox):
    """The layers under close() are all safe to end twice, so a caller that closed the services
    itself still survives the lifespan's own pass."""
    services = _services(sandbox)
    app = create_app(services=services, static_dir=sandbox / NO_STATIC)

    services.close()
    with TestClient(app, base_url=BASE_URL) as client:
        assert client.get("/api/meta").status_code == 200
    services.close()
