"""Exercise publication checks on complete, independently frozen candidates."""
import hashlib
import json
import shutil
import subprocess
import sys

import pytest

from common import ROOT
from release_check import artifacts


@pytest.fixture
def candidate(tmp_path):
    for name in artifacts():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return tmp_path


def freeze(candidate):
    subprocess.run(
        [sys.executable, "tools/export_release.py"],
        cwd=candidate, check=True, capture_output=True, text=True,
    )
    return subprocess.run(
        [sys.executable, "tools/release_check.py", "--freeze"],
        cwd=candidate, capture_output=True, text=True,
    )


@pytest.mark.parametrize("status", ["PASS", "PENDING", "FAIL"])
def test_freeze_accepts_valid_operational_status(candidate, status):
    identity_path = candidate / "release/identity.json"
    identity = json.loads(identity_path.read_text())
    gate = identity["validation"]["operational_patch"]
    gate["status"] = status
    if status != "PASS":
        gate["evidence"] = ""
        gate["evidence_sha256"] = ""
    identity_path.write_text(json.dumps(identity, indent=2) + "\n")
    result = freeze(candidate)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((candidate / "release/manifest.json").read_text())
    assert (manifest["status"] == "validated") == (status == "PASS")
    subprocess.run(
        [sys.executable, "tools/release_check.py"],
        cwd=candidate, check=True, capture_output=True, text=True,
    )


@pytest.mark.parametrize("fault", [
    "missing_evidence", "wrong_hash", "invalid_status", "failed_check",
    "missing_commit", "missing_release", "missing_tool_hash", "tool_drift",
    "unknown_gate",
])
def test_freeze_rejects_unbound_operational_pass(candidate, fault):
    identity_path = candidate / "release/identity.json"
    identity = json.loads(identity_path.read_text())
    gate = identity["validation"]["operational_patch"]
    receipt_path = candidate / gate["evidence"]
    receipt = json.loads(receipt_path.read_text())

    if fault == "missing_evidence":
        gate["evidence"] = ""
    elif fault == "wrong_hash":
        gate["evidence_sha256"] = "0" * 64
    elif fault == "invalid_status":
        gate["status"] = "UNVERIFIED"
    elif fault == "failed_check":
        receipt["checks"]["operational_patch"] = "FAIL"
    elif fault == "missing_commit":
        receipt["tested_code_commit"] = ""
    elif fault == "missing_release":
        receipt["release"] = ""
    elif fault == "missing_tool_hash":
        del receipt["operational_tool_sha256"]["tools/tempo.py"]
    elif fault == "tool_drift":
        tool = candidate / "tools/tempo.py"
        tool.write_text(tool.read_text() + "\n# Changed after validation.\n")
    elif fault == "unknown_gate":
        identity["validation"]["unreviewed_gate"] = {
            "status": "PASS", "evidence": "", "evidence_sha256": "",
        }

    if fault in {"failed_check", "missing_commit", "missing_release", "missing_tool_hash"}:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        gate["evidence_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    identity_path.write_text(json.dumps(identity, indent=2) + "\n")

    result = freeze(candidate)
    assert result.returncode != 0, "Invalid candidate was frozen as validated"
    expected = "Unsupported validation gate" if fault == "unknown_gate" else "operational_patch"
    assert expected in result.stderr, result.stderr
