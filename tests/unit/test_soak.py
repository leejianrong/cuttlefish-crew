"""The soak harness's scorer and driver (CUT-136): no daemon, no model, no real time."""

from __future__ import annotations

from typing import Any

import pytest

from cuttlefish import soak, soak_run
from cuttlefish.soak import Intervention, Sample


def _run(*rows: tuple[float, str, int | None, int]) -> list[Sample]:
    """(t, phase, tokens, waiting) rows."""
    return [Sample(t=t, phase=p, tokens=tok, waiting=w) for t, p, tok, w in rows]


def test_time_is_spent_in_the_earlier_samples_state() -> None:
    result = soak.score(
        _run(
            (0, "planning", 0, 0),
            (60, "awaiting_approval", 10, 0),
            (360, "running", 10, 0),
            (3960, "running", 900, 1),
            (4260, "paused", 900, 0),
            (4560, "stuck", 900, 0),
            (4860, "done", 900, 0),
        )
    )
    assert result.planning_s == 60
    assert result.awaiting_approval_s == 300
    assert result.working_s == 3600
    assert result.blocked_s == 300
    assert result.paused_s == 300
    assert result.stuck_s == 300
    assert result.wall_s == 4860
    assert result.final_phase == "done"


def test_tokens_standing_still_past_the_threshold_count_as_stalled() -> None:
    samples = _run(
        (0, "running", 100, 0),
        (300, "running", 200, 0),
        (600, "running", 200, 0),
        (900, "running", 200, 0),
        (1500, "running", 200, 0),
        (1800, "running", 300, 0),
        (2100, "done", 300, 0),
    )
    result = soak.score(samples, stall_after_s=600)
    # Flat from 300 to 1500 (1200 s); the first 600 s are allowed, the other 600 s are stalled.
    assert result.stalled_s == 600
    assert result.useful_s == result.working_s - 600


def test_unknown_tokens_are_never_called_stalled() -> None:
    result = soak.score(
        _run((0, "running", None, 0), (5000, "running", None, 0), (6000, "done", None, 0))
    )
    assert result.stalled_s == 0
    assert result.tokens is None
    assert result.tokens_per_day is None


def test_a_blocked_stretch_resets_a_stall() -> None:
    samples = _run(
        (0, "running", 5, 0),
        (300, "running", 5, 0),
        (600, "running", 5, 1),  # a permission waits: blocked, not stalled
        (900, "running", 5, 0),
        (1200, "running", 5, 0),
        (1500, "done", 5, 0),
    )
    result = soak.score(samples, stall_after_s=600)
    assert result.blocked_s == 300
    assert result.stalled_s == 0


def test_expected_interventions_are_kept_apart_from_human_ones() -> None:
    result = soak.score(
        _run((0, "running", 0, 0), (100, "done", 10, 0)),
        [
            Intervention(5, "approve_plan"),
            Intervention(50, "message", "try the other way"),
            Intervention(90, "harness_stop"),
        ],
    )
    assert (result.expected, result.human) == (2, 1)


def test_tokens_and_cost_per_day_scale_to_a_day() -> None:
    samples = [
        Sample(0, "running", tokens=0),
        Sample(3600, "done", tokens=400_000),
    ]
    result = soak.score(samples, usd_per_mtok=5.0)
    assert result.tokens == 400_000
    assert result.tokens_per_day == 9_600_000
    assert result.cost_per_day_usd == pytest.approx(48.0)
    assert soak.score(samples).cost_per_day_usd is None


def test_no_samples_cannot_be_scored() -> None:
    with pytest.raises(ValueError, match="no samples"):
        soak.score([])


def test_a_saved_run_loads_back_and_scores_the_same() -> None:
    samples = _run((0, "running", 1, 0), (120, "done", 9, 0))
    notes = [Intervention(10, "message", "hi")]
    loaded, loaded_notes = soak.load_run("not json\n" + soak.dump_run(samples, notes) + "[1]\n")
    assert loaded == samples
    assert loaded_notes == notes
    assert soak.score(loaded, loaded_notes) == soak.score(samples, notes)


def test_the_report_names_the_measures_and_lists_interventions() -> None:
    result = soak.score(
        _run((0, "running", 0, 0), (7200, "done", 1000, 0)),
        [Intervention(60, "message", "stop it")],
    )
    text = soak.render_report(result, {"title": "demo", "backend": "claude-code"})
    for needle in (
        "Soak run: demo",
        "Useful work",
        "2 h 00 min",
        "Human interventions | 1",
        "stop it",
    ):
        assert needle in text
    assert "None." in soak.render_report(soak.score(_run((0, "done", 0, 0), (60, "done", 0, 0))))


# -- the driver, against a fake daemon -----------------------------------------------------------


class FakeDaemon:
    """Plays back Autopilot phases, one per autopilot read."""

    def __init__(
        self, phases: list[dict[str, Any]], chat: list[dict[str, Any]] | None = None
    ) -> None:
        self.phases = phases
        self.reads = 0
        self.posts: list[tuple[str, dict[str, Any] | None]] = []
        self.pending: list[list[dict[str, Any]]] = []
        self.chat = chat or []
        self.stopped = False

    def get(self, path: str) -> dict[str, Any]:
        if path.endswith("/autopilot"):
            row = self.phases[min(self.reads, len(self.phases) - 1)]
            self.reads += 1
            return {"plan": {"done": row.get("done", 0), "total": 3}, **row}
        if path.endswith("/requests"):
            index = min(max(self.reads - 1, 0), len(self.pending) - 1) if self.pending else 0
            return {"pending": self.pending[index] if self.pending else []}
        if "/overview" in path:
            return {"timeline": [{"at": "2099-01-01T00:00:00Z", "kind": "commit", "text": "x"}]}
        if path.endswith("/chat"):
            return {"items": self.chat}
        raise AssertionError(path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.posts.append((path, body))
        if path.endswith("/stop"):
            self.stopped = True
        return {}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _drive(daemon: FakeDaemon, **plan: Any) -> soak_run.Outcome:
    clock = Clock()
    run_plan = soak_run.RunPlan(
        project_id="p1",
        goal="Build the thing",
        backend="claude-code",
        hours=plan.pop("hours", 1.0),
        tokens=1_000_000,
        nudges=50,
        interval_s=600,
        slow_every=1,
    )
    return soak_run.run_soak(
        daemon, run_plan, clock=clock, sleep=clock.sleep, now_iso=lambda: "2026-01-01T00:00:00Z"
    )


def test_a_run_approves_the_plan_once_and_ends_when_autopilot_does() -> None:
    daemon = FakeDaemon(
        [
            {"phase": "off"},
            {"phase": "planning"},
            {"phase": "awaiting_approval"},
            {"phase": "running", "tokens_used": 10, "nudges": 1, "done": 1},
            {"phase": "running", "tokens_used": 99, "nudges": 2, "done": 2},
            {"phase": "done", "tokens_used": 150, "nudges": 3, "done": 3},
        ]
    )
    outcome = _drive(daemon)
    approvals = [p for p, _ in daemon.posts if p.endswith("/autopilot/approve")]
    assert len(approvals) == 1
    goal_post = next(b for p, b in daemon.posts if p.endswith("/goal"))
    assert goal_post and goal_post["autopilot"] == {"hours": 1.0, "tokens": 1_000_000, "nudges": 50}
    assert outcome.result.final_phase == "done"
    assert (outcome.result.expected, outcome.result.human) == (1, 0)
    assert outcome.result.steps_done == 3
    assert outcome.result.commits == 1
    assert not daemon.stopped


def test_the_deadline_stops_autopilot_and_says_so() -> None:
    daemon = FakeDaemon([{"phase": "off"}, {"phase": "running", "tokens_used": 5}])
    outcome = _drive(daemon, hours=1.0)
    assert daemon.stopped
    assert [i.kind for i in outcome.interventions] == ["harness_stop"]
    assert outcome.result.final_phase == "stopped"
    assert outcome.result.wall_s >= 3600


def test_what_a_person_did_is_seen_and_the_driver_answers_nothing() -> None:
    daemon = FakeDaemon(
        [
            {"phase": "off"},
            {"phase": "running"},
            {"phase": "running"},
            {"phase": "paused"},
            {"phase": "running"},
            {"phase": "done"},
        ],
        chat=[
            {"id": "a", "kind": "user", "text": "Build the thing"},
            {"id": "b", "kind": "user", "text": soak_run.NUDGE_TEXT},
            {"id": "c", "kind": "user", "text": "use sqlite instead"},
            {"id": "d", "kind": "assistant", "text": "ok"},
        ],
    )
    daemon.pending = [[], [], [{"id": "r1"}], [], [], []]
    outcome = _drive(daemon)
    kinds = [i.kind for i in outcome.interventions]
    assert kinds.count("message") == 1  # not the goal, not a nudge, and not counted twice
    assert "resume" in kinds
    assert "permission" in kinds
    assert all(not p.endswith("/answer") for p, _ in daemon.posts)


def test_a_project_already_on_autopilot_is_refused_before_anything_is_sent() -> None:
    daemon = FakeDaemon([{"phase": "running"}])
    with pytest.raises(soak_run.SoakError, match="already has"):
        _drive(daemon)
    assert daemon.posts == []


def test_the_default_daemon_port_needs_asking_for() -> None:
    with pytest.raises(soak_run.SoakError, match="8420"):
        soak_run.check_target("http://127.0.0.1:8420", allow_default_port=False)
    soak_run.check_target("http://127.0.0.1:8420", allow_default_port=True)
    soak_run.check_target("http://127.0.0.1:8431", allow_default_port=False)
    with pytest.raises(soak_run.SoakError, match="http"):
        soak_run.check_target("ftp://x", allow_default_port=False)


def test_the_estimate_says_what_it_spends_and_what_waits() -> None:
    text = soak_run.RunPlan("p1", "g", "claude-code", 8.0, 2_000_000, 100).estimate()
    for needle in ("8 hours", "claude-code", "2,000,000", "waits"):
        assert needle in text


# -- the real HTTP client and the command --------------------------------------------------------


@pytest.fixture
def fake_http() -> Any:
    import http.server
    import json
    import threading

    seen: list[tuple[str, str, str | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length).decode() if length else None
            seen.append((self.command, self.path, self.headers.get("x-cuttlefish-token")))
            if self.path.endswith("/missing"):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"detail": "no such thing"}')
                return
            payload: dict[str, Any] = {"echo": body}
            if self.path.endswith("/autopilot"):
                payload = {"phase": "done", "plan": {"done": 1, "total": 1}, "tokens_used": 5}
            if self.path.endswith("/requests"):
                payload = {"pending": []}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        do_GET = do_POST = _answer  # noqa: N815

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


def test_the_http_client_sends_the_token_and_reports_a_refusal(fake_http: Any) -> None:
    url, seen = fake_http
    api = soak_run.HttpApi(url, "tok")
    assert api.post("/x", {"a": 1}) == {"echo": '{"a": 1}'}
    assert seen[-1] == ("POST", "/x", "tok")
    with pytest.raises(soak_run.SoakError, match=r"404.*no such thing"):
        api.get("/missing")
    with pytest.raises(soak_run.SoakError, match="could not reach"):
        soak_run.HttpApi("http://127.0.0.1:1", "t", timeout=1).get("/x")


def test_the_command_writes_a_report_and_a_run_that_scores_again(
    fake_http: Any, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuttlefish.cli import main

    url, _ = fake_http
    out = tmp_path / "out"
    code = main(
        ["soak", "run", "--base-url", url, "--token", "t", "--project", "p1", "--goal", "g",
         "--hours", "0.1", "--interval", "0.01", "--out", str(out), "--yes"]
    )  # fmt: skip
    assert code == 0
    assert (out / "report.md").read_text().startswith("# Soak run")
    assert "Ended: **done**" in capsys.readouterr().out
    assert main(["soak", "score", str(out / "run.jsonl")]) == 0
    assert "Useful work" in capsys.readouterr().out


def test_the_command_will_not_start_without_being_asked(
    fake_http: Any, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuttlefish.cli import main

    url, seen = fake_http
    args = ["soak", "run", "--base-url", url, "--token", "t", "--project", "p1", "--goal", "g"]
    assert main([*args, "--out", str(tmp_path)]) == 2  # stdin is not a terminal under pytest
    assert "--yes" in capsys.readouterr().err
    assert seen == []
    assert (
        main([*args[:3], "http://127.0.0.1:8420", *args[4:], "--out", str(tmp_path), "--yes"]) == 2
    )
    assert "8420" in capsys.readouterr().err
