#!/usr/bin/env python3
"""Validate a frozen code response in a restricted disposable container."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from common import (
    acquire_runner_lock,
    HarnessError,
    existing_receipt,
    fixture_hash,
    load_fixtures,
    load_manifest,
    read_json,
    receipt_identity,
    receipt_path,
    sha256_file,
    utc_text,
    write_json_atomic,
)


def remote(
    prefix: list[str],
    argv: list[str],
    *,
    input_text: str | None = None,
    merge_stderr: bool = False,
) -> str:
    completed = subprocess.run(
        [*prefix, *argv],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        error_output = completed.stdout if merge_stderr else completed.stderr
        raise HarnessError(
            f"remote command failed ({completed.returncode}): argv={argv!r} stderr={error_output[-1000:]!r}"
        )
    return completed.stdout.strip()


def _find_cell(
    catalog: dict[str, Any], catalog_path: Path, task: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    cells = catalog["suites"]["quality"]["cells"]
    matches = [cell for cell in cells if cell["id"] == task]
    if len(matches) == 1:
        fixture = catalog["fixtures"][matches[0]["fixture_id"]]
        return "quality", matches[0], fixture
    held_spec = catalog["suites"]["quality"]["held_out_tasks"]
    held_path = (catalog_path.parent / held_spec["path"]).resolve()
    if sha256_file(held_path) != held_spec["sha256"]:
        raise HarnessError("held-out task catalog hash mismatch")
    held_matches = [item for item in read_json(held_path)["tasks"] if item["id"] == task]
    if len(held_matches) != 1:
        raise HarnessError(f"unknown or duplicate quality/held-out task: {task!r}")
    held = held_matches[0]
    if held["validator"].get("type") != "external_code_validator":
        raise HarnessError(f"held-out task {task!r} is not a code-validator task")
    fixture = {
        "id": task,
        "application_request": held["request"],
        "expected": {
            "validator": "external_code_validator",
            "validator_spec": held["validator"],
        },
        "fixture_sha256": held["task_sha256"],
    }
    return "held-out", {"id": task, "fixture_id": task}, fixture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    acquire_runner_lock(manifest)
    catalog, catalog_path, catalog_hash = load_fixtures(manifest)
    receipt_suite, cell, fixture = _find_cell(catalog, catalog_path, args.task)
    expected = fixture.get("expected", {})
    if expected.get("validator") != "external_code_validator":
        raise HarnessError(f"task {args.task!r} does not declare an external code validator")
    validator = expected["validator_spec"]
    validator_path = Path(validator["path"])
    if not validator_path.is_absolute():
        validator_path = (catalog_path.parent / validator_path).resolve()
    actual_validator_hash = sha256_file(validator_path)
    if actual_validator_hash != validator["sha256"]:
        raise HarnessError(
            f"validator hash mismatch: expected {validator['sha256']}, got {actual_validator_hash}"
        )

    response_path = args.response.resolve()
    response = json.loads(response_path.read_text(encoding="utf-8"))
    if response.get("state") not in {"COMPLETED", "PASSED"}:
        raise HarnessError(f"response is not a successful complete transport receipt: {response.get('state')!r}")
    if response.get("cell") != args.task:
        raise HarnessError(f"response cell {response.get('cell')!r} does not match task {args.task!r}")
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", response.get("text", ""), re.S | re.I)
    if len(blocks) != 1:
        raise HarnessError("expected exactly one complete Python code block")
    source = blocks[0]

    validation = manifest.get("validation")
    if not isinstance(validation, dict):
        raise HarnessError("manifest.validation is required")
    for key in ("host", "image", "transport_argv_prefix", "remote_work_root_template"):
        if key not in validation:
            raise HarnessError(f"manifest.validation missing {key!r}")
    host = validation["host"]
    image = validation["image"]
    if not isinstance(image, str) or not image:
        raise HarnessError("validation image must be a nonempty immutable image reference")
    values = {
        "campaign_id": manifest["campaign_id"],
        "run_id": manifest["run_id"],
        "task": args.task,
        "rep": args.repetition,
        "host": host,
    }
    try:
        prefix = [str(value).format_map(values) for value in validation["transport_argv_prefix"]]
    except (KeyError, ValueError) as error:
        raise HarnessError(f"invalid validation transport argv prefix: {error}") from error
    if not prefix:
        raise HarnessError("validation transport argv prefix must be nonempty")
    work = validation["remote_work_root_template"].format_map(values)
    if not work.startswith("/") or any(character in work for character in "\n\r\x00"):
        raise HarnessError(f"unsafe remote work path: {work!r}")
    container_name = (
        f"campaign-code-{manifest['run_id']}-{args.task}-{args.repetition}".lower().replace("_", "-")
    )
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", container_name):
        raise HarnessError(f"unsafe validation container name: {container_name!r}")

    material = {
        "catalog_sha256": catalog_hash,
        "cell": cell,
        "fixture": fixture,
        "response_sha256": sha256_file(response_path),
        "validator_sha256": actual_validator_hash,
        "validation_host": host,
        "validation_image": image,
    }
    combined_hash = fixture_hash(material)
    identity = receipt_identity(
        manifest, combined_hash, receipt_suite, "validator", args.task, args.repetition
    )
    path = receipt_path(manifest, receipt_suite, "validator", args.task, args.repetition)
    previous = existing_receipt(path, identity)
    if previous is not None:
        print(previous["state"], previous.get("validator_log", ""), flush=True)
        return 0 if previous["state"] == "PASSED" else 1

    validator_remote = work + "/" + validator_path.name
    generated_remote = work + "/generated.py"
    launch_argv = [
        "docker",
        "run",
        "-d",
        "--pull=never",
        "--name",
        container_name,
        "--user",
        "1000:1000",
        "--memory=256m",
        "--memory-swap=256m",
        "--cpus=1",
        "--pids-limit=32",
        "--ulimit",
        "cpu=8:8",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--mount",
        f"type=bind,source={work},target=/candidate,readonly",
        "--entrypoint",
        "timeout",
        image,
        "15s",
        "python3",
        "-I",
        "-S",
        "/candidate/" + validator_path.name,
        *[
            str(value).format(task=args.task, source="/candidate/generated.py")
            for value in validator["argv"]
        ],
    ]
    record: dict[str, Any] = {
        **identity,
        "state": "RUNNING",
        "at": utc_text(),
        "validation_host": host,
        "validation_image": image,
        "transport_argv_prefix": prefix,
        "remote_work_path": work,
        "response": str(response_path),
        "response_sha256": material["response_sha256"],
        "generated_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "validator": str(validator_path),
        "validator_sha256": actual_validator_hash,
        "launch_argv": launch_argv,
        "sandbox_contract": {
            "network": "none",
            "root_filesystem": "read_only",
            "memory_bytes": 256 * 1024 * 1024,
            "memory_plus_swap_bytes": 256 * 1024 * 1024,
            "cpus": 1,
            "pids_limit": 32,
            "container_timeout_seconds": 15,
        },
    }
    write_json_atomic(path, record)
    cid = None
    try:
        remote(prefix, ["mkdir", "-p", work])
        remote(prefix, ["tee", generated_remote], input_text=source)
        remote(prefix, ["tee", validator_remote], input_text=validator_path.read_text(encoding="utf-8"))
        cid = remote(prefix, launch_argv)
        if not re.fullmatch(r"[a-f0-9]{12,64}", cid):
            raise HarnessError(f"docker run returned an invalid CID: {cid!r}")
        record["container_id"] = cid
        write_json_atomic(path, record)
        exit_text = remote(prefix, ["docker", "wait", cid])
        exit_code = int(exit_text)
        validator_log = remote(prefix, ["docker", "logs", cid], merge_stderr=True)
        inspect = remote(prefix, ["docker", "inspect", "--format", "{{json .State}}", cid])
        record.update(
            state="PASSED" if exit_code == 0 else "FAILED",
            reason=None if exit_code == 0 else "FUNCTIONAL_VALIDATOR_FAILED",
            exit_code=exit_code,
            validator_log=validator_log,
            docker_state=json.loads(inspect),
            ended_at=utc_text(),
        )
    except Exception as error:
        record.update(state="FAILED", reason="VALIDATOR_INFRASTRUCTURE_FAILURE", error=repr(error), ended_at=utc_text())
    finally:
        if cid:
            try:
                remote(prefix, ["docker", "rm", cid])
                record["container_removed"] = True
            except Exception as cleanup_error:
                record["container_removed"] = False
                record["cleanup_error"] = repr(cleanup_error)
                record.update(state="FAILED", reason="VALIDATOR_CLEANUP_FAILURE")
        write_json_atomic(path, record)
    print(record["state"], record.get("validator_log", record.get("error", "")), flush=True)
    return 0 if record["state"] == "PASSED" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"HARNESS_ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
