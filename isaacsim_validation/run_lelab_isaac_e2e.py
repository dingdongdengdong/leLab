"""API-driven LeLab to Isaac Sim 6.0 acceptance and evidence runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from lelab.superarm.isaac_distribution import validate_and_extract_distribution

from .contracts import ARM_JOINTS, HAND_JOINTS, PHYSICAL_JOINTS, expand_logical_action

CASES = [
    ("neutral_open", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    ("arm_probe_half", [0.12, -0.12, 0.10, -0.10, 0.08, 0.5]),
    ("neutral_close", [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]),
]
ARM_ERROR_LIMIT_RAD = 0.02
HAND_ERROR_LIMIT_RAD = 0.01
VISUAL_NAMES = ("whole", "open", "half-close", "close")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_joint_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, dict[str, Any]] = {}
    for section, expected in (("arm", ARM_JOINTS), ("hand", HAND_JOINTS)):
        values = state.get(section)
        if not isinstance(values, dict) or set(values) != set(expected):
            raise ValueError(f"Isaac state requires exact {section} joints")
        for name in expected:
            metric = values[name]
            if not isinstance(metric, dict):
                raise ValueError(f"Isaac state metric is invalid for {name}")
            position = metric.get("position")
            target = metric.get("target")
            if (
                isinstance(position, bool)
                or isinstance(target, bool)
                or not isinstance(position, int | float)
                or not isinstance(target, int | float)
                or not math.isfinite(float(position))
                or not math.isfinite(float(target))
            ):
                raise ValueError(f"Isaac state position/target is invalid for {name}")
            error = abs(float(target) - float(position))
            metrics[name] = {
                "position": float(position),
                "target": float(target),
                "absolute_error_rad": error,
            }

    arm_error = max(metrics[name]["absolute_error_rad"] for name in ARM_JOINTS)
    hand_error = max(metrics[name]["absolute_error_rad"] for name in HAND_JOINTS)
    return {
        "physics_step": state.get("physics_step"),
        "command_sequence": state.get("command_sequence"),
        "measured_positions": {name: metrics[name]["position"] for name in PHYSICAL_JOINTS},
        "reported_targets": {name: metrics[name]["target"] for name in PHYSICAL_JOINTS},
        "absolute_errors_rad": {
            name: metrics[name]["absolute_error_rad"] for name in PHYSICAL_JOINTS
        },
        "max_arm_error_rad": arm_error,
        "max_hand_error_rad": hand_error,
        "settled": arm_error <= ARM_ERROR_LIMIT_RAD and hand_error <= HAND_ERROR_LIMIT_RAD,
    }


def snapshot_matches_command(
    snapshot: dict[str, Any],
    expected_targets: dict[str, float],
    *,
    previous_sequence: int,
) -> bool:
    sequence = snapshot.get("command_sequence")
    reported = snapshot.get("reported_targets")
    return (
        isinstance(sequence, int)
        and not isinstance(sequence, bool)
        and sequence > previous_sequence
        and snapshot.get("settled") is True
        and isinstance(reported, dict)
        and list(reported) == list(PHYSICAL_JOINTS)
        and all(
            math.isclose(float(reported[name]), expected_targets[name], abs_tol=1e-6)
            for name in PHYSICAL_JOINTS
        )
    )


def evaluate_hand_frames(paths: list[Path]) -> dict[str, Any]:
    if len(paths) != 3:
        raise ValueError("hand evidence requires exactly three frames")
    images: list[Image.Image] = []
    frames = []
    try:
        for path in paths:
            image = Image.open(path).convert("RGB")
            images.append(image)
            stats = ImageStat.Stat(image)
            nonblank = max(stats.stddev) > 2.0
            frames.append(
                {
                    "path": str(path),
                    "width": image.width,
                    "height": image.height,
                    "channel_stddev": stats.stddev,
                    "nonblank": nonblank,
                    "sha256": sha256_file(path),
                }
            )
        differences = []
        for left, right in zip(images[:-1], images[1:], strict=True):
            if left.size != right.size:
                right = right.resize(left.size)
            differences.append(sum(ImageStat.Stat(ImageChops.difference(left, right)).mean) / 3.0)
        return {
            "frames": frames,
            "adjacent_mean_abs_diff": differences,
            "passed": all(frame["nonblank"] for frame in frames)
            and all(value > 0.5 for value in differences),
        }
    finally:
        for image in images:
            image.close()


def write_hand_gif(paths: list[Path], output: Path) -> None:
    frames = [Image.open(path).convert("RGB") for path in paths]
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            output,
            save_all=True,
            append_images=frames[1:],
            duration=700,
            loop=0,
            optimize=False,
        )
    finally:
        for frame in frames:
            frame.close()


def distribution_visual_provenance(manifest: dict[str, Any]) -> dict[str, Any]:
    files = manifest.get("files")
    source = manifest.get("source")
    if not isinstance(files, list) or not isinstance(source, dict):
        raise RuntimeError("distribution manifest lacks visual provenance")
    report = next(
        (
            item
            for item in files
            if isinstance(item, dict) and item.get("path") == "validation/isaac-report.json"
        ),
        None,
    )
    report_sha256 = report.get("sha256") if isinstance(report, dict) else None
    validation_run_id = source.get("validation_run_id")
    visuals = manifest.get("visual_evidence")
    if (
        not isinstance(report_sha256, str)
        or len(report_sha256) != 64
        or not isinstance(validation_run_id, str)
        or not validation_run_id
        or not isinstance(visuals, dict)
        or set(visuals) != set(VISUAL_NAMES)
    ):
        raise RuntimeError("distribution manifest lacks visual provenance")
    for name in VISUAL_NAMES:
        item = visuals[name]
        if (
            not isinstance(item, dict)
            or item.get("path") != f"validation/visuals/{name}.png"
            or not isinstance(item.get("bytes"), int)
            or isinstance(item.get("bytes"), bool)
            or item["bytes"] <= 0
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in item["sha256"])
        ):
            raise RuntimeError("distribution manifest lacks visual provenance")
    return {
        "report_sha256": report_sha256,
        "validation_run_id": validation_run_id,
        "visuals": visuals,
    }


def collect_static_visual_evidence(
    source_dir: Path,
    run_dir: Path,
    *,
    expected_report_sha256: str,
    expected_validation_run_id: str,
    expected_visuals: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Copy the separately validated Isaac USD frames without calling them live captures."""

    source_dir = source_dir.resolve(strict=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    for key in VISUAL_NAMES:
        metadata = expected_visuals.get(key)
        if not isinstance(metadata, dict):
            raise RuntimeError(f"static Isaac visual evidence is missing metadata: {key}")
        relative = metadata.get("path")
        source = (source_dir / str(relative)).resolve(strict=True)
        if not source.is_relative_to(source_dir) or not source.is_file():
            raise RuntimeError(f"static Isaac visual evidence is unsafe or missing: {relative}")
        if source.stat().st_size != metadata.get("bytes") or sha256_file(source) != metadata.get(
            "sha256"
        ):
            raise RuntimeError(f"static Isaac visual frame does not match the distribution: {key}")
        destination = run_dir / f"superarm-isaac60-passive-linkage-{key}.png"
        shutil.copyfile(source, destination)
        copied[key] = destination
    source_report = (source_dir / "validation" / "isaac-report.json").resolve(strict=True)
    if not source_report.is_relative_to(source_dir):
        raise RuntimeError("static Isaac visual report is unsafe")
    copied_report = run_dir / "superarm-isaac60-passive-linkage-report.json"
    shutil.copyfile(source_report, copied_report)
    source_report_sha256 = sha256_file(copied_report)
    if source_report_sha256 != expected_report_sha256:
        raise RuntimeError(
            "static Isaac visual report does not match the controlled distribution"
        )
    validation = json.loads(copied_report.read_text(encoding="utf-8"))
    passive_linkages = validation.get("passive_linkage_visuals")
    input_urdf = validation.get("input_urdf")
    if (
        validation.get("status") != "PASS"
        or not isinstance(passive_linkages, dict)
        or not passive_linkages
        or not isinstance(input_urdf, str)
        or expected_validation_run_id not in input_urdf
    ):
        raise RuntimeError("static Isaac visual evidence report is not PASS")
    hand_frames = [copied[key] for key in ("open", "half-close", "close")]
    return {
        "proof_category": "prevalidated_static_isaac_visuals",
        "is_live_session_capture": False,
        "whole_frame": str(copied["whole"]),
        "hand_frames": [str(path) for path in hand_frames],
        "source_report": str(copied_report),
        "source_report_sha256": source_report_sha256,
        "distribution_validation_report_sha256": expected_report_sha256,
        "validation_run_id": expected_validation_run_id,
    }


class ApiClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> bytes:
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc

    def json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.loads(self._request(method, path, payload))
        if not isinstance(data, dict):
            raise RuntimeError(f"{method} {path} returned a non-object JSON response")
        return data


def wait_for(
    operation,
    predicate,
    *,
    timeout_s: float,
    interval_s: float = 0.1,
    description: str,
):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = operation()
        if predicate(last):
            return last
        time.sleep(interval_s)
    raise TimeoutError(f"timed out waiting for {description}; last={last!r}")


def wait_settled(
    client: ApiClient,
    expected_targets: dict[str, float],
    *,
    previous_sequence: int,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    def accepted(value: dict[str, Any]) -> bool:
        state = value.get("state")
        return isinstance(state, dict) and snapshot_matches_command(
            build_joint_snapshot(state),
            expected_targets,
            previous_sequence=previous_sequence,
        )

    telemetry = wait_for(
        lambda: client.json("GET", "/api/superarm/telemetry"),
        accepted,
        timeout_s=timeout_s,
        description="new 13-joint command sequence with expected settled targets",
    )
    return build_joint_snapshot(telemetry["state"])


def wait_hold_stability(
    client: ApiClient,
    initial: dict[str, Any],
    *,
    minimum_steps: int = 120,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    initial_step = int(initial["physics_step"])
    targets = initial["reported_targets"]

    def stable(value: dict[str, Any]) -> bool:
        snapshot = build_joint_snapshot(value["state"])
        if snapshot["reported_targets"] != targets:
            raise RuntimeError("hold target vector changed during the stability window")
        return int(snapshot["physics_step"]) - initial_step >= minimum_steps

    final = wait_for(
        lambda: client.json("GET", "/api/superarm/telemetry"),
        stable,
        timeout_s=timeout_s,
        description=f"{minimum_steps} stable physics steps",
    )
    snapshot = build_joint_snapshot(final["state"])
    return {
        "passed": True,
        "start_physics_step": initial_step,
        "end_physics_step": snapshot["physics_step"],
        "stable_steps": int(snapshot["physics_step"]) - initial_step,
        "target_vector": targets,
    }


def container_absent(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode != 0


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    client = ApiClient(args.base_url, timeout_s=args.http_timeout_s)
    distribution = args.distribution_zip.resolve(strict=True)
    resolved_distribution = validate_and_extract_distribution(distribution)
    visual_provenance = distribution_visual_provenance(resolved_distribution.manifest)
    report: dict[str, Any] = {
        "schema": "lelab.superarm.isaac_e2e/v1",
        "status": "FAIL",
        "started_at": utc_now(),
        "base_url": args.base_url.rstrip("/"),
        "distribution_zip": str(distribution),
        "distribution_sha256": resolved_distribution.archive_sha256,
        "distribution_validation": visual_provenance,
        "run_dir": str(run_dir),
        "cases": [],
    }
    report_path = run_dir / "lelab-isaac-e2e-report.json"
    connected = False
    try:
        session_payload = {
            "runtime": "isaac_sim",
            "isaac_distribution_zip": str(distribution),
            "isaac_expected_sha256": report["distribution_sha256"],
            "isaac_bridge_mode": "managed",
            "isaac_host": "127.0.0.1",
            "isaac_port": args.bridge_port,
        }
        session = client.json("POST", "/api/superarm/session", session_payload)
        connected = bool(session.get("connected"))
        if not connected:
            raise RuntimeError(f"Isaac session did not connect: {session}")
        metadata = session.get("runtime_metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("Isaac session did not expose runtime metadata")
        report["runtime"] = session.get("runtime")
        report["bridge_metadata"] = metadata

        wait_for(
            lambda: client.json("GET", "/api/superarm/telemetry"),
            lambda value: bool(value.get("state", {}).get("physics_step")),
            timeout_s=10.0,
            description="connected Isaac telemetry",
        )

        for case_name, logical_action in CASES:
            expected = expand_logical_action(logical_action)
            started = time.monotonic()
            before = build_joint_snapshot(
                client.json("GET", "/api/superarm/telemetry")["state"]
            )
            client.json("PUT", "/api/superarm/logical-action", {"values": logical_action})
            snapshot = wait_settled(
                client,
                expected,
                previous_sequence=int(before["command_sequence"]),
            )
            report["cases"].append(
                {
                    "name": case_name,
                    "logical_action": logical_action,
                    "expanded_targets": expected,
                    **snapshot,
                    "settle_elapsed_s": time.monotonic() - started,
                }
            )

        visual_evidence = collect_static_visual_evidence(
            resolved_distribution.root,
            run_dir,
            expected_report_sha256=visual_provenance["report_sha256"],
            expected_validation_run_id=visual_provenance["validation_run_id"],
            expected_visuals=visual_provenance["visuals"],
        )
        hand_paths = [Path(path) for path in visual_evidence["hand_frames"]]
        report["static_visual_evidence"] = visual_evidence
        report["hand_frame_metrics"] = evaluate_hand_frames(hand_paths)
        gif_path = run_dir / "lelab-isaac-open-half-close.gif"
        write_hand_gif(hand_paths, gif_path)
        report["gif"] = {
            "path": str(gif_path),
            "sha256": sha256_file(gif_path),
            "bytes": gif_path.stat().st_size,
        }

        before_emergency = build_joint_snapshot(
            client.json("GET", "/api/superarm/telemetry")["state"]
        )
        client.json("POST", "/api/superarm/emergency-stop", {"active": True})
        emergency_state = wait_for(
            lambda: client.json("GET", "/api/superarm/telemetry"),
            lambda value: int(value["state"]["command_sequence"])
            > int(before_emergency["command_sequence"]),
            timeout_s=5.0,
            description="emergency hold command",
        )
        emergency_snapshot = build_joint_snapshot(emergency_state["state"])
        report["emergency_hold"] = wait_hold_stability(client, emergency_snapshot)
        report["emergency_hold"]["command_sequence"] = emergency_snapshot["command_sequence"]
        client.json("POST", "/api/superarm/emergency-stop", {"active": False})

        close_hand = {finger: [110.0, 110.0] for finger in ("pointer", "middle", "ring", "thumb")}
        client.json(
            "PUT",
            "/api/superarm/action",
            {
                "arm_rad": dict(zip(ARM_JOINTS, CASES[-1][1][:5], strict=True)),
                "hand_deg": close_hand,
                "source": "live",
            },
        )
        live_state = client.json("GET", "/api/superarm/telemetry")
        live_sequence = int(live_state["state"]["command_sequence"])
        timeout_state = wait_for(
            lambda: client.json("GET", "/api/superarm/telemetry"),
            lambda value: value.get("live_enabled") is False
            and int(value["state"]["command_sequence"]) > live_sequence,
            timeout_s=12.0,
            interval_s=0.2,
            description="ten-second live-command hold",
        )
        timeout_snapshot = build_joint_snapshot(timeout_state["state"])
        report["live_timeout_hold"] = wait_hold_stability(client, timeout_snapshot)
        report["live_timeout_hold"]["command_sequence"] = timeout_snapshot["command_sequence"]

        container_name = metadata.get("container", {}).get("container_name")
        if not isinstance(container_name, str) or not container_name:
            raise RuntimeError("managed session metadata is missing container_name")
        disconnect_started = time.monotonic()
        client.json("DELETE", "/api/superarm/session")
        connected = False
        wait_for(
            lambda: container_absent(container_name),
            bool,
            timeout_s=10.0,
            description="managed container removal",
        )
        report["managed_disconnect"] = {
            "passed": True,
            "container_name": container_name,
            "elapsed_s": time.monotonic() - disconnect_started,
        }

        reconnect = client.json("POST", "/api/superarm/session", session_payload)
        if not reconnect.get("connected"):
            raise RuntimeError(f"managed reconnect failed: {reconnect}")
        connected = True
        client.json("DELETE", "/api/superarm/session")
        connected = False
        report["reconnect"] = {"passed": True, "runtime": reconnect.get("runtime")}

        metadata_ok = (
            report["runtime"] == "isaac_sim"
            and str(metadata.get("isaac_sim_version", "")).startswith("6.0")
            and metadata.get("articulation_root_count") == 1
            and metadata.get("physical_dof_count") == 13
            and metadata.get("logical_action_width") == 6
            and set(metadata.get("joint_names", [])) == set(PHYSICAL_JOINTS)
            and len(metadata.get("joint_names", [])) == 13
        )
        report["gates"] = {
            "metadata": metadata_ok,
            "cases_settled": all(case["settled"] for case in report["cases"]),
            "static_hand_frames": report["hand_frame_metrics"]["passed"],
            "emergency_hold": report["emergency_hold"]["passed"],
            "live_timeout_hold": report["live_timeout_hold"]["passed"],
            "managed_disconnect": report["managed_disconnect"]["passed"],
            "reconnect": report["reconnect"]["passed"],
        }
        report["status"] = "PASS" if all(report["gates"].values()) else "FAIL"
        if report["status"] != "PASS":
            raise RuntimeError(f"acceptance gates failed: {report['gates']}")
        return report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if connected:
            try:
                client.json("DELETE", "/api/superarm/session")
            except Exception as exc:
                report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        report["finished_at"] = utc_now()
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--distribution-zip", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--bridge-port", type=int, default=8765)
    parser.add_argument("--http-timeout-s", type=float, default=240.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = run_acceptance(args)
    except Exception as exc:
        print(f"LeLab Isaac E2E failed: {exc}")
        return 1
    print(json.dumps({"status": report["status"], "run_dir": report["run_dir"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
