"""Shared fixtures for the EXL3 guard + barrier mock tests (stdlib + pytest)."""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD_DIR = HERE.parent / "tools"
GUARD = GUARD_DIR / "exl3_guard.py"
BARRIER = GUARD_DIR / "barrier.sh"

# Synthetic full container ids (64 hex). Deliberately NOT any real host id.
CID = "a" * 64
OTHER_CID = "b" * 64


def make_cgroup(root, cid, *, current=10 * 2**30, swap_current=0, peak=20 * 2**30,
                events=None, max_bytes=112 * 2**30, swap_max="0", name=None):
    """Build a fixture cgroup v2 dir for exactly this cid."""
    d = Path(root) / (name or ("docker-%s.scope" % cid))
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.current").write_text("%d\n" % current)
    (d / "memory.swap.current").write_text("%d\n" % swap_current)
    (d / "memory.peak").write_text("%d\n" % peak)
    ev = {"oom": 0, "oom_kill": 0, "oom_group_kill": 0}
    ev.update(events or {})
    (d / "memory.events").write_text(
        "".join("%s %d\n" % kv for kv in sorted(ev.items())))
    (d / "memory.max").write_text("%d\n" % max_bytes)
    (d / "memory.swap.max").write_text("%s\n" % swap_max)
    return d


def make_fixture(root, *, memavailable_gib=8.0, memfree_gib=6.0,
                 pswpin=0, pswpout=0, inspect_id=CID, running=True,
                 oom_killed=False, restart_count=0, psi=None):
    """Build a --fixture DIR of synthetic /proc + docker inspect data."""
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "proc_meminfo").write_text(
        "MemTotal:       262144000 kB\n"
        "MemFree:        %d kB\n"
        "MemAvailable:   %d kB\n"
        "SwapTotal:      %d kB\nSwapFree: %d kB\n"
        % (memfree_gib * 2**20, memavailable_gib * 2**20, 0, 0))
    (d / "proc_vmstat").write_text("pswpin %d\npswpout %d\n" % (pswpin, pswpout))
    (d / "proc_pressure_memory").write_text(
        psi or "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
               "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    inspect = [{
        "Id": inspect_id,
        "RestartCount": restart_count,
        "State": {"Running": running, "OOMKilled": oom_killed, "Pid": 4242},
    }]
    (d / "docker_inspect.json").write_text(json.dumps(inspect))
    return d


def run_guard(fixture_dir, cgroup_dir, cid=CID, extra=(), telemetry=None,
              tmp_path=None):
    """Run exl3_guard.py --once --dry-run against fixtures."""
    tel = telemetry or str(Path(tmp_path) / "GUARD.jsonl")
    argv = [sys.executable, str(GUARD),
            "--container-id", cid,
            "--cgroup", str(cgroup_dir),
            "--telemetry", tel,
            "--fixture", str(fixture_dir),
            "--once", "--dry-run"] + list(extra)
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    rows = []
    tel_path = Path(tel)
    if tel_path.exists():
        rows = [json.loads(l) for l in tel_path.read_text().splitlines() if l.strip()]
    return p.returncode, rows, p.stderr


def run_barrier(args, timeout=30):
    """Run barrier.sh with a bounded timeout."""
    p = subprocess.run(["bash", str(BARRIER)] + list(args),
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr
