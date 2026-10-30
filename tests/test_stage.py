"""Presenter stage endpoint (dashboard/stage.py): allowlist, header gate, no request text in any command.

Nothing here runs hyprctl, tmux or the real stage.sh against the desktop: handle() gets a recording runner, the server
tests patch handle's default runner, and the shell tests use a copy of stage.sh next to a fake `hyprctl`.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_dashboard import DEMO, Clock

from dashboard import __main__ as dash_main
from dashboard import stage
from dashboard.server import make_server
from dashboard.state import DashboardState

REPO = Path(__file__).resolve().parent.parent
HDR = {stage.STAGE_HEADER: "1"}

# Frozen copy: if someone makes an action depend on anything else, this no longer matches.
FROZEN = {
    "show-terminal": ["bash", "demo/raw/stage.sh", "show-terminal"],
    "show-dashboard": ["bash", "demo/raw/stage.sh", "show-dashboard"],
    "term-ollama-ps": ["tmux", "send-keys", "-t", "demoterm", "bash demo/raw/demo.sh 0", "Enter"],
    "term-curl-guard-off": ["tmux", "send-keys", "-t", "demoterm", "bash demo/raw/demo.sh 2", "Enter"],
    "term-curl-guard-on": ["tmux", "send-keys", "-t", "demoterm", "bash demo/raw/demo.sh 3", "Enter"],
    "term-tests": ["tmux", "send-keys", "-t", "demoterm", "bash demo/raw/demo.sh 5", "Enter"],
}


class Recorder:
    def __init__(self, returncode=0, stderr="", exc=None):
        self.calls: list[tuple[list, dict]] = []
        self.returncode, self.stderr, self.exc = returncode, stderr, exc

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


# ------------------------------------------------------------------------------------------------ handle(): success

def test_six_actions_exist_and_match_frozen_copy():
    assert set(stage.ACTIONS) == set(FROZEN)
    assert len(stage.ACTIONS) == 6
    for name, argv in FROZEN.items():
        assert stage.ACTIONS[name][1] == argv


@pytest.mark.parametrize("name", list(FROZEN))
def test_each_action_runs_its_constant_argv_without_a_shell(name):
    rec = Recorder()
    code, body = stage.handle({"action": name}, HDR, runner=rec)
    assert (code, body["ok"], body["label"]) == (200, True, stage.ACTIONS[name][0])
    assert len(rec.calls) == 1
    argv, kw = rec.calls[0]
    assert argv == FROZEN[name]
    assert kw["shell"] is False
    assert Path(kw["cwd"]) == REPO
    assert kw["timeout"] == stage.TIMEOUT_S


def test_labels_cover_all_actions():
    assert set(stage.labels()) == set(FROZEN)
    assert all(isinstance(v, str) and v for v in stage.labels().values())


# ------------------------------------------------------------------------------------------------ handle(): refusals

@pytest.mark.parametrize("headers", [None, {}, {stage.STAGE_HEADER: "0"}, {stage.STAGE_HEADER: ""},
                                     {stage.STAGE_HEADER: "true"}, {"X-Other": "1"}])
def test_missing_or_wrong_header_is_403_and_runs_nothing(headers):
    rec = Recorder()
    code, body = stage.handle({"action": "show-terminal"}, headers, runner=rec)
    assert code == 403 and "error" in body
    assert rec.calls == []


@pytest.mark.parametrize("action", [
    "nope", "", "show-terminal; rm -rf ~", "term-tests && curl x", "$(id)", "`id`", "show-terminal\n",
    " show-terminal", "SHOW-TERMINAL", "show-terminal|id", "../stage", "show-terminal\x00",
])
def test_unknown_or_injected_action_names_are_400_and_run_nothing(action):
    rec = Recorder()
    code, body = stage.handle({"action": action}, HDR, runner=rec)
    assert code == 400 and body["error"] == "unknown stage action"
    assert rec.calls == []


@pytest.mark.parametrize("body", [["show-terminal"], "show-terminal", None, 5, {}, {"act": "show-terminal"}])
def test_body_not_a_dict_with_action_is_400_and_runs_nothing(body):
    rec = Recorder()
    code, _ = stage.handle(body, HDR, runner=rec)
    assert code == 400
    assert rec.calls == []


@pytest.mark.parametrize("action", [123, ["show-terminal"], None, {"x": 1}, True, b"show-terminal"])
def test_action_not_a_string_is_400_and_runs_nothing(action):
    rec = Recorder()
    code, _ = stage.handle({"action": action}, HDR, runner=rec)
    assert code == 400
    assert rec.calls == []


def test_extra_keys_never_change_the_argv():
    rec = Recorder()
    code, _ = stage.handle({"action": "term-tests", "cmd": "rm -rf /", "args": ["x"], "cwd": "/", "shell": True,
                            "argv": ["id"]}, HDR, runner=rec)
    assert code == 200
    argv, kw = rec.calls[0]
    assert argv == FROZEN["term-tests"]
    assert kw["shell"] is False and Path(kw["cwd"]) == REPO


def test_handle_does_not_leak_a_mutable_argv():
    """The runner may mutate what it gets; the allowlist must survive."""
    before = copy.deepcopy(stage.ACTIONS)

    def mutating(argv, **kw):
        argv.append("; id")
        return subprocess.CompletedProcess(argv, 0, "", "")

    stage.handle({"action": "show-terminal"}, HDR, runner=mutating)
    assert stage.ACTIONS == before


# ------------------------------------------------------------------------------------------------ failures

def test_nonzero_exit_is_500_not_ok():
    code, body = stage.handle({"action": "show-terminal"}, HDR, runner=Recorder(returncode=1, stderr="boom"))
    assert code == 500 and body["ok"] is False and body["error"] == "boom"


@pytest.mark.parametrize("exc", [OSError("no tmux"), FileNotFoundError("tmux"),
                                 subprocess.TimeoutExpired(["tmux"], 10)])
def test_runner_raising_is_500_and_handle_never_raises(exc):
    code, body = stage.handle({"action": "term-tests"}, HDR, runner=Recorder(exc=exc))
    assert code == 500 and body["ok"] is False
    assert body["error"] == type(exc).__name__


# ------------------------------------------------------------------------------------------------ no request text in argv

def test_every_argv_element_is_a_module_constant_string():
    for name, (label, argv) in stage.ACTIONS.items():
        assert argv == FROZEN[name]
        assert all(isinstance(a, str) for a in argv)
        assert not any(c in " ".join(argv) for c in ";&|`$<>\n") or name.startswith("term-")  # only 'bash demo...' text
        for a in argv:
            assert "\n" not in a and "$(" not in a and "`" not in a


def test_request_text_is_not_in_the_argv_it_triggered():
    marker = "ZZ-REQUEST-MARKER"
    rec = Recorder()
    stage.handle({"action": "show-dashboard", "note": marker, "args": [marker]}, {**HDR, "X-Note": marker}, runner=rec)
    assert marker not in json.dumps(rec.calls[0][0])


# ------------------------------------------------------------------------------------------------ server

def _serve(state):
    server = make_server(state, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post(base, payload, headers=None, raw=None):
    data = raw if raw is not None else json.dumps(payload).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base + "/api/stage", data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw_body = e.read()
        try:
            return e.code, json.loads(raw_body)
        except ValueError:
            return e.code, raw_body


@pytest.fixture
def stage_server(monkeypatch):
    """Real server in a thread. handle()'s default runner is bound at def time, so patching subprocess.run alone
    would not reach it: patch both, so the real subprocess can never be reached from these tests."""
    rec = Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setitem(stage.handle.__kwdefaults__, "runner", rec)
    state = DashboardState(DEMO, live=None, mode="replay", clock=Clock(1000.0))
    server, base = _serve(state)
    yield state, base, rec
    server.shutdown()
    server.server_close()


def test_server_stage_is_404_when_not_enabled(stage_server):
    state, base, rec = stage_server
    assert not getattr(state, "stage_enabled", False)
    code, _ = _post(base, {"action": "show-terminal"}, HDR)
    assert code == 404
    state.stage_enabled = False
    assert _post(base, {"action": "show-terminal"}, HDR)[0] == 404
    assert rec.calls == []


def test_server_stage_enabled_valid_unknown_and_no_header(stage_server):
    state, base, rec = stage_server
    state.stage_enabled = True
    code, body = _post(base, {"action": "term-tests"}, HDR)
    assert code == 200 and body["ok"] is True
    assert [c[0] for c in rec.calls] == [FROZEN["term-tests"]]
    assert rec.calls[0][1]["shell"] is False

    n = len(rec.calls)
    assert _post(base, {"action": "rm -rf /"}, HDR)[0] == 400
    assert _post(base, {"action": "term-tests"})[0] == 403
    assert _post(base, None, HDR, raw=b"not json{")[0] == 400
    assert _post(base, ["term-tests"], HDR)[0] == 400
    assert len(rec.calls) == n


# ------------------------------------------------------------------------------------------------ __main__

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::"])
def test_main_refuses_stage_on_a_non_loopback_host(host, capsys):
    assert dash_main.main(["--stage", "--host", host, "--replay-only", "--port", str(_free_port())]) == 2
    assert "loopback" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ shell scripts

BASH = shutil.which("bash") or "/bin/bash"


def test_demo_sh_parses_and_case_accepts_step_0():
    assert subprocess.run([BASH, "-n", str(REPO / "demo/raw/demo.sh")], capture_output=True).returncode == 0
    text = (REPO / "demo/raw/demo.sh").read_text()
    assert "0) step0" in text and "step0()" in text.replace("step0() {", "step0()")
    assert "bash demo/raw/demo.sh 0|1|2|3|4|5|all" in text


@pytest.fixture
def fake_stage_env(tmp_path):
    """A copy of stage.sh in an empty dir (so no real stage.env), and a fake hyprctl that records its calls."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(REPO / "demo/raw/stage.sh", scripts / "stage.sh")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "hyprctl.log"
    hypr = fakebin / "hyprctl"
    hypr.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    hypr.chmod(0o755)
    env = {"PATH": f"{fakebin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return scripts / "stage.sh", log, env


def test_stage_sh_unknown_action_exits_2_without_hyprctl(fake_stage_env):
    script, log, env = fake_stage_env
    done = subprocess.run([BASH, str(script), "bogus"], capture_output=True, text=True, env=env, timeout=10)
    assert done.returncode == 2 and "usage" in done.stderr
    assert not log.exists()


@pytest.mark.parametrize("action", ["show-terminal", "show-dashboard"])
def test_stage_sh_without_stage_env_exits_3_without_hyprctl(fake_stage_env, action):
    script, log, env = fake_stage_env
    assert not (script.parent / "stage.env").exists()
    done = subprocess.run([BASH, str(script), action], capture_output=True, text=True, env=env, timeout=10)
    assert done.returncode == 3
    assert not log.exists()


def test_stage_sh_with_env_calls_only_hyprctl_workspace_with_digits(fake_stage_env):
    script, log, env = fake_stage_env
    (script.parent / "stage.env").write_text("TERMINAL_WS=3\nDASHBOARD_WS=2; touch pwned\n")
    done = subprocess.run([BASH, str(script), "show-terminal"], capture_output=True, text=True, env=env, timeout=10)
    assert done.returncode == 0
    assert log.read_text().strip() == "dispatch workspace 3"
    # a non-digit value is ignored (never sourced), so show-dashboard is refused
    done = subprocess.run([BASH, str(script), "show-dashboard"], capture_output=True, text=True, env=env, timeout=10)
    assert done.returncode == 3
    assert not (script.parent / "pwned").exists()
    assert log.read_text().strip() == "dispatch workspace 3"
