from __future__ import annotations

import math
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaacsim_validation.contracts import PHYSICAL_JOINTS
from lelab.superarm.isaac_runtime import IsaacSimRuntime


class FakeProcess:
    pid = 4321

    def __init__(self):
        self.returncode = None
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.returncode = 0
        return 0


class HungProcess(FakeProcess):
    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if len(self.wait_calls) < 3:
            raise subprocess.TimeoutExpired("bridge", timeout)
        self.returncode = -signal.SIGKILL
        return self.returncode


class FakeClient:
    def __init__(self, hello=None, positions=None):
        self.hello = hello or {
            "runtime": "isaac_sim",
            "isaac_sim_version": "6.0.0",
            "articulation_root": "/superarm_amazinghand",
            "articulation_root_count": 1,
            "physical_dof_count": 13,
            "logical_action_width": 6,
            "joint_names": list(reversed(PHYSICAL_JOINTS)),
        }
        self.commands = []
        self.shutdown_calls = 0
        self.close_calls = 0
        self.observe_calls = 0
        self.capture_response = None
        self.positions = positions or {
            name: (index + 1) / 100 for index, name in enumerate(PHYSICAL_JOINTS)
        }
        self.hold_positions = None
        self.observe_failures = 0

    def connect(self):
        return dict(self.hello)

    def command(self, targets):
        self.commands.append(dict(targets))
        return {"accepted": True}

    def observe(self):
        if self.observe_failures:
            self.observe_failures -= 1
            raise ConnectionError("observe failed")
        self.observe_calls += 1
        return {
            "runtime": "isaac_sim",
            "arm": {
                name: {"position": self.positions[name], "target": self.positions[name]}
                for name in PHYSICAL_JOINTS[:5]
            },
            "hand": {
                name: {"position": self.positions[name], "target": self.positions[name]}
                for name in PHYSICAL_JOINTS[5:]
            },
        }

    def hold(self):
        if self.hold_positions is not None:
            self.positions = dict(self.hold_positions)
        return {"accepted": True}

    def capture(self, view, name):
        if self.capture_response is not None:
            return dict(self.capture_response)
        return {"path": f"captures/{view}-{name}.png", "bytes": 7}

    def shutdown(self):
        self.shutdown_calls += 1
        return {"accepted": True}

    def close(self):
        self.close_calls += 1


class UnavailableClient(FakeClient):
    def connect(self):
        raise ConnectionError("bridge not ready")


class DisconnectedClient(FakeClient):
    connected = False

    def shutdown(self):
        raise AssertionError("shutdown must not run after the bridge disconnected")


def _distribution(tmp_path: Path):
    root = tmp_path / "asset"
    entrypoint = root / "usd" / "robot.usda"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#usda 1.0\n", encoding="utf-8")
    return SimpleNamespace(
        root=root,
        entrypoint=entrypoint,
        archive_sha256="a" * 64,
        robot_contract={"physical_dof_count": 13, "logical_action_width": 6},
    )


def test_managed_runtime_launches_with_file_token_and_sends_complete_named_targets(tmp_path):
    distribution = _distribution(tmp_path)
    process = FakeProcess()
    client = FakeClient()
    process_calls = []
    client_calls = []

    def process_factory(args, **kwargs):
        process_calls.append((args, kwargs))
        return process

    def client_factory(*args, **kwargs):
        client_calls.append((args, kwargs))
        return client

    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        session_root=tmp_path / "session",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        process_factory=process_factory,
        client_factory=client_factory,
    )
    runtime.connect()

    args, kwargs = process_calls[0]
    assert args[0].endswith("run_isaacsim60_control_bridge.sh")
    assert args[args.index("--asset-root") + 1] == str(distribution.root)
    assert args[args.index("--entrypoint") + 1] == str(distribution.entrypoint)
    token_path = Path(args[args.index("--token-file") + 1])
    run_dir = Path(args[args.index("--run-dir") + 1])
    assert token_path.is_file()
    assert not token_path.is_relative_to(run_dir)
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert token_path.read_text(encoding="utf-8").strip() not in repr(args)
    assert kwargs["shell"] is False
    assert kwargs["start_new_session"] is True
    assert client_calls[0][1]["timeout_s"] == 5.0
    assert client_calls[0][1]["capture_timeout_s"] == 120.0

    runtime.command_partial(arm_rad={"joint_rev_1": 0.25})
    runtime.command_partial(
        hand_deg={"pointer": [110.0, 110.0]},
        hand_speed={"pointer": [3, 3]},
    )
    runtime.command_logical([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])

    assert all(list(command) == list(PHYSICAL_JOINTS) for command in client.commands)
    assert client.commands[0]["joint_rev_1"] == pytest.approx(0.25)
    assert client.commands[1]["finger1_motor2"] == pytest.approx(1.10)
    assert client.commands[2]["finger4_motor2"] == pytest.approx(1.10)
    assert runtime.frame() == (0, None)
    assert runtime.supports_video is False
    assert runtime.supports_capture is False
    assert runtime.metadata["isaac_sim_version"] == "6.0.0"
    assert runtime.metadata["articulation_root"] == "/superarm_amazinghand"
    assert runtime.metadata["articulation_root_count"] == 1
    assert runtime.metadata["logical_action_width"] == 6
    assert runtime.metadata["physical_dof_count"] == 13
    assert runtime.metadata["run_dir"] == str(run_dir)

    runtime.close()
    assert client.shutdown_calls == 1
    assert process.wait_calls
    assert not token_path.exists()


def test_managed_close_terminates_then_kills_a_stuck_process_group(tmp_path):
    distribution = _distribution(tmp_path)
    process = HungProcess()
    client = FakeClient()
    signals = []
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        session_root=tmp_path / "session",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        process_factory=lambda *_args, **_kwargs: process,
        client_factory=lambda *_args, **_kwargs: client,
        group_signal=lambda pid, sent_signal: signals.append((pid, sent_signal)),
    )
    runtime.connect()

    runtime.close()

    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert process.wait_calls == [5.0, 3.0, 2.0]


def test_managed_close_cleans_up_without_shutdown_after_transport_disconnect(tmp_path):
    distribution = _distribution(tmp_path)
    process = FakeProcess()
    client = DisconnectedClient()
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        session_root=tmp_path / "session",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        process_factory=lambda *_args, **_kwargs: process,
        client_factory=lambda *_args, **_kwargs: client,
    )
    runtime.connect()

    runtime.close()

    assert client.close_calls == 1
    assert process.wait_calls == [5.0]


@pytest.mark.parametrize(
    "hello",
    [
        {"runtime": "isaac_sim", "physical_dof_count": 12, "logical_action_width": 6, "joint_names": list(PHYSICAL_JOINTS)},
        {"runtime": "isaac_sim", "physical_dof_count": 13, "logical_action_width": 6, "joint_names": [*PHYSICAL_JOINTS[:-1], "wrong"]},
        {"runtime": "isaac_sim", "physical_dof_count": 13, "logical_action_width": 6, "joint_names": [*PHYSICAL_JOINTS, PHYSICAL_JOINTS[-1]]},
    ],
)
def test_runtime_rejects_inexact_hello_contract(tmp_path, hello):
    distribution = _distribution(tmp_path)
    client = FakeClient(hello)
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
    )

    with pytest.raises(RuntimeError, match="13-joint contract"):
        runtime.connect()

    assert runtime.connected is False


def test_external_runtime_closes_socket_without_shutting_down_server(tmp_path):
    distribution = _distribution(tmp_path)
    client = FakeClient()
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
    )
    runtime.connect()
    runtime.close()

    assert client.shutdown_calls == 0
    assert client.close_calls == 1


def test_observe_polls_bridge_at_no_more_than_twenty_hertz(tmp_path):
    distribution = _distribution(tmp_path)
    client = FakeClient()
    now = [10.0]
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
        clock=lambda: now[0],
    )
    runtime.connect()

    first = runtime.observe()
    runtime.observe()
    now[0] += 0.049
    runtime.observe()
    initial_calls = client.observe_calls
    assert initial_calls >= 1
    assert len(first["arm"]) + len(first["hand"]) == 13

    now[0] += 0.002
    runtime.observe()
    assert client.observe_calls == initial_calls + 1
    runtime.close()


def test_partial_command_seeds_and_rebases_unspecified_targets_from_observed_state(tmp_path):
    distribution = _distribution(tmp_path)
    initial = {name: (index + 1) / 10 for index, name in enumerate(PHYSICAL_JOINTS)}
    held = {name: -(index + 1) / 20 for index, name in enumerate(PHYSICAL_JOINTS)}
    client = FakeClient(positions=initial)
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
    )
    runtime.connect()

    runtime.command_partial(arm_rad={"joint_rev_1": 0.25})
    assert client.commands[-1]["joint_rev_2"] == pytest.approx(initial["joint_rev_2"])
    assert client.commands[-1]["finger4_motor2"] == pytest.approx(initial["finger4_motor2"])

    runtime.command_logical([0.1, 0.2, 0.3, 0.4, 0.5, 1.0])
    client.hold_positions = held
    runtime.stop()
    runtime.command_partial(arm_rad={"joint_rev_1": 0.15})
    assert client.commands[-1]["joint_rev_2"] == pytest.approx(held["joint_rev_2"])
    assert client.commands[-1]["finger4_motor2"] == pytest.approx(held["finger4_motor2"])
    runtime.close()


def test_failed_post_hold_observe_forces_next_partial_command_to_refresh(tmp_path):
    distribution = _distribution(tmp_path)
    held = {name: -(index + 1) / 30 for index, name in enumerate(PHYSICAL_JOINTS)}
    client = FakeClient()
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
    )
    runtime.connect()
    runtime.command_logical([0.1, 0.2, 0.3, 0.4, 0.5, 1.0])
    client.hold_positions = held
    client.observe_failures = 1

    runtime.stop()
    assert runtime._targets is None
    runtime.command_partial(arm_rad={"joint_rev_1": 0.15})

    assert client.commands[-1]["joint_rev_2"] == pytest.approx(held["joint_rev_2"])
    assert client.commands[-1]["finger4_motor2"] == pytest.approx(held["finger4_motor2"])
    runtime.close()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_partial_command_rejects_nonfinite_direct_values(tmp_path, value):
    distribution = _distribution(tmp_path)
    client = FakeClient()
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
    )
    runtime.connect()

    with pytest.raises(ValueError, match="finite"):
        runtime.command_partial(arm_rad={"joint_rev_1": value})
    with pytest.raises(ValueError, match="finite"):
        runtime.command_partial(hand_deg={"pointer": [0.0, value]})

    runtime.close()


def test_connect_timeout_reports_phase_and_bounded_log_tail(tmp_path):
    distribution = _distribution(tmp_path)
    client = UnavailableClient()
    now = [0.0]
    session_root = tmp_path / "session"

    def sleep(seconds):
        now[0] += seconds
        log_path = session_root / "run" / "container.log"
        log_path.write_text("old line\nfinal bridge detail\n", encoding="utf-8")

    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        session_root=session_root,
        startup_timeout_s=0.2,
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
        clock=lambda: now[0],
        sleep=sleep,
    )

    with pytest.raises(RuntimeError, match="(?s)waiting for authenticated hello.*final bridge detail"):
        runtime.connect()

    assert runtime.connected is False
    assert client.close_calls == 1


def test_capture_is_disabled_without_affecting_control(tmp_path):
    distribution = _distribution(tmp_path)
    client = FakeClient()
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: client,
    )
    runtime.connect()

    assert runtime.supports_capture is False
    with pytest.raises(RuntimeError, match="live capture is disabled"):
        runtime.capture("hand", "hand")
    runtime.command_partial(arm_rad={"joint_rev_1": 0.2})
    assert client.commands[-1]["joint_rev_1"] == pytest.approx(0.2)

    runtime.close()


def test_close_always_removes_token_and_closes_log_after_signal_failure(tmp_path):
    distribution = _distribution(tmp_path)
    process = HungProcess()
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        session_root=tmp_path / "session",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        process_factory=lambda *_args, **_kwargs: process,
        client_factory=lambda *_args, **_kwargs: FakeClient(),
        group_signal=lambda *_args: (_ for _ in ()).throw(ProcessLookupError("gone")),
    )
    runtime.connect()
    token_path = runtime._token_path
    log_handle = runtime._log_handle

    with pytest.raises(RuntimeError, match="cleanup"):
        runtime.close()

    assert token_path is not None and not token_path.exists()
    assert log_handle.closed


def test_log_tail_reads_only_a_bounded_suffix(tmp_path, monkeypatch):
    distribution = _distribution(tmp_path)
    runtime = IsaacSimRuntime(
        tmp_path / "distribution.zip",
        bridge_mode="external",
        token="secret",
        session_root=tmp_path / "session",
        distribution_loader=lambda *_args, **_kwargs: distribution,
        client_factory=lambda *_args, **_kwargs: FakeClient(),
    )
    runtime._distribution = distribution
    runtime._prepare_session()
    log_path = runtime.run_dir / "container.log"
    log_path.write_bytes(b"x" * 100_000 + b"\nlast bounded line\n")
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full read")))

    assert runtime._log_tail().endswith("last bounded line")
