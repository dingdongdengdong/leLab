"""Host-side lifecycle and control adapter for the local Isaac Sim bridge."""

from __future__ import annotations

import json
import math
import os
import secrets
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

import isaacsim_validation
from isaacsim_validation.contracts import PHYSICAL_JOINTS, validate_physical_targets

from .actions import action_to_isaac_targets
from .isaac_distribution import IsaacDistribution, validate_and_extract_distribution
from .isaac_protocol import IsaacBridgeClient
from .mapping import (
    ARM_JOINTS,
    ARM_MAX_RAD,
    ARM_MIN_RAD,
    MUJOCO_FINGERS,
    UI_FINGERS,
    URDF_MOTOR_LIMITS,
    clamp,
)


class IsaacSimRuntime:
    """Managed or external Isaac bridge implementing the SuperArm runtime surface."""

    supports_video = False

    def __init__(
        self,
        distribution_zip: str | Path,
        *,
        bridge_mode: Literal["managed", "external"] = "managed",
        host: str = "127.0.0.1",
        port: int = 8765,
        token: str | None = None,
        expected_sha256: str | None = None,
        cache_root: str | Path | None = None,
        session_root: str | Path | None = None,
        external_run_dir: str | Path | None = None,
        startup_timeout_s: float = 180.0,
        state_callback: Callable[[dict[str, Any]], None] | None = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        client_factory: Callable[..., IsaacBridgeClient] = IsaacBridgeClient,
        distribution_loader: Callable[..., IsaacDistribution] = validate_and_extract_distribution,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        group_signal: Callable[[int, int], None] = os.killpg,
    ) -> None:
        if bridge_mode not in {"managed", "external"}:
            raise ValueError("Isaac bridge mode must be managed or external")
        self.distribution_zip = Path(distribution_zip).expanduser()
        self.bridge_mode = bridge_mode
        self.host = host
        self.port = int(port)
        self._configured_token = token
        self.expected_sha256 = expected_sha256
        self.cache_root = Path(cache_root).expanduser() if cache_root else None
        self._session_root = Path(session_root).expanduser() if session_root else None
        self._external_run_dir = (
            Path(external_run_dir).expanduser() if external_run_dir else None
        )
        self.startup_timeout_s = float(startup_timeout_s)
        self.state_callback = state_callback
        self._process_factory = process_factory
        self._client_factory = client_factory
        self._distribution_loader = distribution_loader
        self._clock = clock
        self._sleep = sleep
        self._group_signal = group_signal
        self._lock = threading.RLock()
        self._connected = False
        self._failure: str | None = None
        self._distribution: IsaacDistribution | None = None
        self._client: IsaacBridgeClient | None = None
        self._process: subprocess.Popen | None = None
        self._log_handle: Any = None
        self._token_path: Path | None = None
        self._targets: dict[str, float] | None = None
        self._hello: dict[str, Any] = {}
        self._latest_state: dict[str, Any] = {}
        self._last_observe = float("-inf")
        self.run_dir = Path()
        self._artifact_root: Path | None = None

    @property
    def connected(self) -> bool:
        return self._connected and self._failure is None

    @property
    def failure(self) -> str | None:
        return self._failure

    @property
    def supports_capture(self) -> bool:
        return False

    @property
    def metadata(self) -> dict[str, Any]:
        metadata = {
            **self._hello,
            "bridge_mode": self.bridge_mode,
            "host": self.host,
            "port": self.port,
            "run_dir": str(self.run_dir) if self.run_dir else None,
            "distribution_sha256": (
                self._distribution.archive_sha256 if self._distribution is not None else None
            ),
        }
        container_metadata = self.run_dir / "container-metadata.json"
        if container_metadata.is_file():
            try:
                container = json.loads(container_metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                container = None
            if isinstance(container, dict):
                metadata["container"] = container
        return metadata

    def _prepare_session(self) -> None:
        if self._session_root is None:
            self._session_root = (
                Path.home()
                / ".cache"
                / "lelab"
                / "superarm_isaac"
                / "sessions"
                / uuid.uuid4().hex
            )
        self._session_root = self._session_root.resolve()
        self._session_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._session_root, 0o700)
        self.run_dir = self._session_root / "run"
        self.run_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.run_dir, 0o700)
        if self.bridge_mode == "external" and self._external_run_dir is not None:
            artifact_root = self._external_run_dir.resolve()
            if not artifact_root.is_dir():
                raise RuntimeError(
                    f"external Isaac run directory does not exist: {artifact_root}"
                )
            self._artifact_root = artifact_root
        else:
            self._artifact_root = self.run_dir

    def _start_managed(self, token: str) -> None:
        if self._distribution is None or self._session_root is None:
            raise RuntimeError("Isaac distribution/session was not prepared")
        self._token_path = self._session_root / "bridge-token"
        self._token_path.write_text(token + "\n", encoding="utf-8")
        os.chmod(self._token_path, 0o600)
        launcher = Path(isaacsim_validation.__file__).parent / "run_isaacsim60_control_bridge.sh"
        if not launcher.is_file():
            raise RuntimeError(f"Isaac control launcher is missing: {launcher}")
        command = [
            str(launcher),
            "--asset-root",
            str(self._distribution.root),
            "--entrypoint",
            str(self._distribution.entrypoint),
            "--run-dir",
            str(self.run_dir),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--token-file",
            str(self._token_path),
        ]
        self._log_handle = (self.run_dir / "launcher.log").open("ab", buffering=0)
        self._process = self._process_factory(
            command,
            shell=False,
            start_new_session=True,
            stdout=self._log_handle,
            stderr=self._log_handle,
        )

    @staticmethod
    def _bounded_log_lines(path: Path, max_bytes: int = 65_536) -> list[str]:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            body = handle.read(max_bytes)
        return body.decode("utf-8", errors="replace").splitlines()[-20:]

    def _log_tail(self) -> str:
        lines: list[str] = []
        roots = [self.run_dir]
        if self._artifact_root is not None and self._artifact_root != self.run_dir:
            roots.append(self._artifact_root)
        for path in (root / name for root in roots for name in ("launcher.log", "container.log")):
            if path.is_file():
                lines.extend(self._bounded_log_lines(path))
        return "\n".join(lines[-20:])[-4000:]

    @staticmethod
    def _targets_from_state(state: Mapping[str, Any]) -> dict[str, float]:
        positions: dict[str, float] = {}
        for section in ("arm", "hand"):
            values = state.get(section)
            if not isinstance(values, Mapping):
                raise RuntimeError(f"Isaac observation is missing {section} joint state")
            for name, joint in values.items():
                if not isinstance(joint, Mapping) or "position" not in joint or "target" not in joint:
                    raise RuntimeError(f"Isaac observation is missing position/target for {name}")
                position = joint["position"]
                target = joint["target"]
                if (
                    isinstance(position, bool)
                    or not isinstance(position, int | float)
                    or not math.isfinite(float(position))
                ):
                    raise RuntimeError(f"Isaac observation position is not finite for {name}")
                positions[str(name)] = target
        try:
            return validate_physical_targets(positions)
        except ValueError as exc:
            raise RuntimeError(f"Isaac observation does not match the 13-joint contract: {exc}") from exc

    def _refresh_targets(self) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Isaac runtime is disconnected")
        state = self._client.observe()
        self._targets = self._targets_from_state(state)
        self._latest_state = dict(state)
        self._last_observe = self._clock()
        return dict(state)

    def connect(self) -> None:
        if self.connected:
            return
        phase = "validating distribution"
        try:
            self._distribution = self._distribution_loader(
                self.distribution_zip,
                cache_root=self.cache_root,
                expected_sha256=self.expected_sha256,
            )
            self._prepare_session()
            token = self._configured_token or os.environ.get("SUPERARM_ISAAC_BRIDGE_TOKEN")
            if self.bridge_mode == "managed":
                token = secrets.token_hex(32)
                phase = "starting managed container"
                self._start_managed(token)
            elif not token:
                raise RuntimeError(
                    "external Isaac bridge requires SUPERARM_ISAAC_BRIDGE_TOKEN or server token"
                )
            self._client = self._client_factory(
                self.host,
                self.port,
                token=token,
                timeout_s=min(2.0, self.startup_timeout_s),
                capture_timeout_s=120.0,
            )
            phase = "waiting for authenticated hello"
            deadline = self._clock() + self.startup_timeout_s
            last_error: Exception | None = None
            while True:
                try:
                    hello = self._client.connect()
                    break
                except Exception as exc:
                    last_error = exc
                    if self._process is not None and self._process.poll() is not None:
                        raise RuntimeError(
                            f"managed Isaac bridge exited during {phase}: {self._log_tail()}"
                        ) from exc
                    if self._clock() >= deadline:
                        raise RuntimeError(
                            f"Isaac bridge timeout during {phase}: {last_error}; {self._log_tail()}"
                        ) from exc
                    self._sleep(0.1)
            joint_names = hello.get("joint_names")
            isaac_version = hello.get("isaac_sim_version")
            articulation_root = hello.get("articulation_root")
            if (
                hello.get("runtime") != "isaac_sim"
                or not isinstance(isaac_version, str)
                or not isaac_version.startswith("6.0")
                or not isinstance(articulation_root, str)
                or not articulation_root
                or hello.get("articulation_root_count") != 1
                or hello.get("physical_dof_count") != 13
                or hello.get("logical_action_width") != 6
                or not isinstance(joint_names, list)
                or len(joint_names) != len(PHYSICAL_JOINTS)
                or len(set(joint_names)) != len(PHYSICAL_JOINTS)
                or set(joint_names) != set(PHYSICAL_JOINTS)
            ):
                raise RuntimeError("Isaac bridge hello does not match the exact 13-joint contract")
            self._hello = dict(hello)
            self._refresh_targets()
            self._connected = True
            self._failure = None
        except Exception as exc:
            self._failure = f"Isaac runtime failed during {phase}: {exc}"
            with suppress(Exception):
                self.close()
            raise

    @staticmethod
    def _hand_targets(hand_deg: Mapping[str, list[float]]) -> dict[str, float]:
        unknown = set(hand_deg) - set(UI_FINGERS)
        if unknown:
            raise ValueError(f"unknown hand fingers: {sorted(unknown)}")
        targets: dict[str, float] = {}
        for finger, values in hand_deg.items():
            if len(values) != 2:
                raise ValueError(f"{finger} requires two hand values")
            prefix = MUJOCO_FINGERS[finger]
            for motor, degrees in enumerate(values, start=1):
                degrees = float(degrees)
                if not math.isfinite(degrees):
                    raise ValueError(f"{finger} hand values must be finite")
                if motor == 1:
                    value = 0.05 + degrees * (0.95 - 0.05) / 110.0
                else:
                    value = 0.02 + degrees * (1.10 - 0.02) / 110.0
                lower, upper = URDF_MOTOR_LIMITS[motor]
                targets[f"{prefix}_motor{motor}"] = clamp(value, lower, upper)
        return targets

    def command_partial(
        self,
        *,
        arm_rad: Mapping[str, float] | None = None,
        hand_deg: Mapping[str, list[float]] | None = None,
        hand_speed: Mapping[str, list[int]] | None = None,
    ) -> None:
        del hand_speed
        if not self.connected or self._client is None:
            raise RuntimeError("Isaac runtime is disconnected")
        with self._lock:
            if self._targets is None:
                self._refresh_targets()
            targets = dict(self._targets or {})
            if arm_rad is not None:
                unknown = set(arm_rad) - set(ARM_JOINTS)
                if unknown:
                    raise ValueError(f"unknown arm joints: {sorted(unknown)}")
                normalized_arm = {name: float(value) for name, value in arm_rad.items()}
                if not all(math.isfinite(value) for value in normalized_arm.values()):
                    raise ValueError("arm values must be finite")
                targets.update({
                    name: max(ARM_MIN_RAD, min(ARM_MAX_RAD, value))
                    for name, value in normalized_arm.items()
                })
            if hand_deg is not None:
                targets.update(self._hand_targets(hand_deg))
            targets = validate_physical_targets(targets)
            self._client.command(targets)
            self._targets = targets

    def command_logical(self, values: list[float]) -> None:
        if not self.connected or self._client is None:
            raise RuntimeError("Isaac runtime is disconnected")
        targets = action_to_isaac_targets(values)
        with self._lock:
            self._client.command(targets)
            self._targets = targets

    def observe(self) -> dict[str, Any]:
        if not self.connected or self._client is None:
            return {}
        now = self._clock()
        callback_state: dict[str, Any] | None = None
        with self._lock:
            if not self._latest_state or now - self._last_observe >= 0.05:
                self._latest_state = self._client.observe()
                self._last_observe = now
                callback_state = dict(self._latest_state)
            result = dict(self._latest_state)
        if callback_state is not None and self.state_callback:
            self.state_callback(callback_state)
        return result

    def stop(self) -> None:
        if self.connected and self._client is not None:
            with self._lock:
                self._client.hold()
                try:
                    self._refresh_targets()
                except Exception:
                    self._targets = None

    def frame(self) -> tuple[int, None]:
        return 0, None

    def capture(self, view: Literal["whole", "hand"], name: str) -> dict[str, Any]:
        del view, name
        raise RuntimeError(
            "live capture is disabled; use the separately validated static Isaac USD evidence"
        )

    def close(self) -> None:
        self._connected = False
        cleanup_errors: list[Exception] = []
        client, self._client = self._client, None
        if client is not None:
            try:
                if self.bridge_mode == "managed" and getattr(client, "connected", True):
                    client.shutdown()
                else:
                    client.close()
            except Exception as exc:
                cleanup_errors.append(exc)
                with suppress(Exception):
                    client.close()
        process, self._process = self._process, None
        if process is not None:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    self._group_signal(process.pid, signal.SIGTERM)
                except Exception as exc:
                    cleanup_errors.append(exc)
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    try:
                        self._group_signal(process.pid, signal.SIGKILL)
                        process.wait(timeout=2.0)
                    except Exception as kill_exc:
                        cleanup_errors.append(kill_exc)
                except Exception as exc:
                    cleanup_errors.append(exc)
        try:
            if self._log_handle is not None:
                self._log_handle.close()
        except Exception as exc:
            cleanup_errors.append(exc)
        finally:
            self._log_handle = None
        try:
            if self._token_path is not None:
                self._token_path.unlink(missing_ok=True)
        except Exception as exc:
            cleanup_errors.append(exc)
        finally:
            self._token_path = None
        if cleanup_errors:
            raise RuntimeError(f"Isaac runtime cleanup failed: {cleanup_errors[0]}") from cleanup_errors[0]
