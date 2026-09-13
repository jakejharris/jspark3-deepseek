#!/usr/bin/env python3
"""Persistent per-rank EXL3 memory guard (Tempo).

Watches ONE exact container ID on ONE host. Never resolves containers by name
or prefix. The only mutating action it can ever take is `docker stop -t <t>
<exact-cid>` (and writing its own telemetry/trip/marker files).

Inherited L5-P policy (see docs/OPERATIONS.md):

  emergency stop : host MemAvailable < 4 GiB, or cgroup service swap > 0,
                   or new oom/oom_kill/oom_group_kill in memory.events,
                   or rank death/restart/OOMKilled, or container identity
                   mismatch, or stale/unreachable telemetry past the blind
                   window, or a peer guard's trip file (coupled stop).
  promotion      : only when the run-window minimum MemAvailable >= 5 GiB
                   and service swap stayed exactly 0 and no trip fired.
                   In [4,5) GiB the band is HOLD: no promotion, owner stops
                   new tests and drains; the 4 GiB floor is never weakened.
  sampling       : default 1 s (qualification); pass --interval 5 while idle.
                   No time-boxed expiry (spans entrypoint/load/capture/API/
                   bench/idle).
  telemetry      : append-only JSONL rows, each stamped with a monotonic
                   clock and UTC ISO-8601.

Telemetry row kinds: start | sample | warn | trip | stop_receipt | promotion |
end. Every row carries v, kind, mono, utc, cid.

Fixture mode (--fixture DIR) replaces all reads of /proc/*, the cgroup tree
and `docker inspect` with files under DIR, so the guard can be exercised
without any host, Docker daemon or GPU:

  DIR/proc_meminfo            raw /proc/meminfo text
  DIR/proc_vmstat             raw /proc/vmstat text
  DIR/proc_pressure_memory    raw /proc/pressure/memory text
  DIR/docker_inspect.json     `docker inspect <cid>` JSON (array)
  (the --cgroup argument itself points at a fixture directory that contains
   memory.current, memory.swap.current, memory.events, memory.peak,
   memory.max, memory.swap.max)

Exit codes:
  0  clean end (--once, --max-runtime, SIGTERM/SIGINT) without a trip
  4  emergency trip handled (stop receipt recorded)
  64 usage refusal (bad args, name-pattern/partial container id, blind
     window outside [1,60] s)
  65 safety refusal (cgroup does not belong to the exact container id,
     identity mismatch observed before the loop starts)
  66 required path missing (cgroup dir or its memory.current)
  70 internal error (e.g. telemetry unwritable)

Threshold interpretation notes (also in REPORT.md): the dispatch brief says
"60 s blind-sample cap" while EXL3-SPEC section 4 and the manifest say 15 s;
this guard defaults to 15 s and refuses any configured blind window above
60 s (the brief's number is treated as a hard cap, the spec's as default).
The brief's "cgroup swap cap 112 GiB" is the Docker --memory-swap knob; with
--memory 112g + --memory-swap 112g, cgroup v2 memory.swap.max must be 0, so
0 is the expected swap.max and any other value (for example `max`
anomaly) is a WARN, not a silent pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

GIB = 1024 ** 3

# EXL3-SPEC section 4 + rendered manifest guard block.
DEFAULT_EMERGENCY_BYTES = 4 * GIB
DEFAULT_PROMOTION_BYTES = 5 * GIB
DEFAULT_BLIND_SECONDS = 15          # spec: stale/unreachable beyond 15 s
MAX_BLIND_SECONDS = 60              # brief: blind-sample cap 60 s (hard max)
DEFAULT_INTERVAL = 1.0              # qualification cadence; use 5 for idle
EXPECTED_MEMORY_MAX_BYTES = 112 * GIB   # Docker --memory 112g
EXPECTED_TOTAL_SWAP_BYTES = 112 * GIB   # Docker --memory-swap 112g
# cgroup v2: memory.swap.max = --memory-swap - --memory = 0 expected.
EXPECTED_SWAP_MAX_BYTES = 0

CID_RE = re.compile(r"^[0-9a-f]{64}$")
OOM_EVENT_KEYS = ("oom", "oom_kill", "oom_group_kill")

STATE_WATCHING = "WATCHING"
STATE_BLIND = "BLIND"
STATE_TRIPPED = "TRIPPED"
STATE_ENDED = "ENDED"

BAND_EMERGENCY = "EMERGENCY"        # < 4 GiB  -> stop
BAND_HOLD = "HOLD"                  # [4,5) GiB -> no promotion, owner drains
BAND_PROMOTE_OK = "PROMOTE_OK"      # >= 5 GiB

TRIP_SERVICE_SWAP = "SERVICE_SWAP_NONZERO"
TRIP_HEADROOM = "HEADROOM_EMERGENCY"
TRIP_OOM_EVENTS = "CGROUP_OOM_EVENTS"
TRIP_RANK_DEATH = "RANK_DEATH_OR_RESTART"
TRIP_IDENTITY = "CONTAINER_IDENTITY_MISMATCH"
TRIP_PEER = "PEER_GUARD_TRIP"
TRIP_BLIND = "BLIND_WINDOW_EXCEEDED"
TRIP_INCARNATION = "CONTAINER_INCARNATION_CHANGED"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- parsing --


def parse_meminfo(text):
    out = {}
    for line in text.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key, rest = parts[0].strip(), parts[1].split()
        if rest:
            out[key] = int(rest[0]) * 1024  # /proc/meminfo is kB
    return out


def parse_psi(text):
    """Parse /proc/pressure/memory into {some: {...}, full: {...}}."""
    out = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        kind, rest = fields[0], fields[1:]
        entry = {}
        for f in rest:
            if "=" in f:
                k, v = f.split("=", 1)
                entry[k] = float(v)
        out[kind] = entry
    return out


def parse_keyval_ints(text):
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def parse_memory_events(text):
    return parse_keyval_ints(text)


# ------------------------------------------------------------ decision core --


def decide(sample, baseline, thresholds, blind_seconds_elapsed):
    """Pure decision core (unit-testable).

    sample: dict as produced by gather(); baseline: dict or None
    (set from first good sample); thresholds: dict with keys
    emergency_bytes, promotion_bytes. Returns (state, reasons, band,
    promotion_eligible).
    """
    reasons = []

    if sample.get("peer_trip"):
        reasons.append(TRIP_PEER)

    ident = sample.get("docker", {})
    if ident.get("id_match") is False:
        reasons.append(TRIP_IDENTITY)
    expected_started_at = thresholds.get("expected_started_at")
    if (expected_started_at is not None
            and ident.get("started_at") is not None
            and ident["started_at"] != expected_started_at):
        reasons.append(TRIP_INCARNATION)
    # restart policy is `no` and every attempt uses fresh exact IDs, so any
    # nonzero RestartCount observed at ANY sample is a restart (the guard
    # cannot know pre-registration history; fail closed).
    if ident.get("running") is False or ident.get("oom_killed") is True:
        reasons.append(TRIP_RANK_DEATH)
    if ident.get("restart_count"):
        reasons.append(TRIP_RANK_DEATH)

    cg = sample.get("cgroup", {})
    if cg.get("swap_current_bytes", 0) is not None and cg.get("swap_current_bytes", 0) > 0:
        reasons.append(TRIP_SERVICE_SWAP)
    # OOM events: fail-closed on ANY nonzero oom/oom_kill/oom_group_kill counter.
    # The guard cannot know the container's pre-registration history, so a
    # nonzero counter at first sight counts as a new event (EXL3-SPEC 4:
    # "new oom/oom_kill/oom_group_kill" -> stop coupled candidate ranks).
    if cg.get("events"):
        for key in OOM_EVENT_KEYS:
            if cg["events"].get(key, 0) > 0:
                reasons.append(TRIP_OOM_EVENTS)
                break

    headroom = sample.get("memavailable_bytes")
    if headroom is not None and headroom < thresholds["emergency_bytes"]:
        reasons.append(TRIP_HEADROOM)

    # Blind: missing control telemetry is never treated as "zero usage".
    if blind_seconds_elapsed is not None and blind_seconds_elapsed >= thresholds["blind_seconds"]:
        reasons.append(TRIP_BLIND)

    reasons = list(dict.fromkeys(reasons))
    state = STATE_TRIPPED if reasons else (
        STATE_BLIND if blind_seconds_elapsed is not None else STATE_WATCHING)

    if headroom is None:
        band = None
    elif headroom < thresholds["emergency_bytes"]:
        band = BAND_EMERGENCY
    elif headroom < thresholds["promotion_bytes"]:
        band = BAND_HOLD
    else:
        band = BAND_PROMOTE_OK

    eligible = (
        baseline is not None
        and sample.get("window_min_memavailable_bytes") is not None
        and sample["window_min_memavailable_bytes"] >= thresholds["promotion_bytes"]
        and sample.get("window_max_swap_bytes", 0) == 0
        and sample.get("tripped_ever") is False
    )
    return state, reasons, band, eligible


# ------------------------------------------------------------------ sources --


class Sources:
    """Reads /proc, cgroup and docker-inspect data from live host or fixture."""

    def __init__(self, cgroup_dir, fixture=None):
        self.cgroup_dir = Path(cgroup_dir)
        self.fixture = Path(fixture) if fixture else None

    def _read(self, live_path, fixture_name, required):
        path = (self.fixture / fixture_name) if self.fixture else Path(live_path)
        try:
            return path.read_text()
        except OSError as e:
            return ("__error__", repr(e), required)

    def _cg_read(self, name, required):
        path = self.cgroup_dir / name
        try:
            return path.read_text()
        except OSError as e:
            return ("__error__", repr(e), required)

    def gather(self, container_id):
        """Return a sample dict. Read failures land in sample['errors'];
        entries with required=True make the sample blind-critical."""
        s = {"errors": {}}
        # /proc/meminfo
        r = self._read("/proc/meminfo", "proc_meminfo", True)
        if isinstance(r, tuple):
            s["errors"]["proc_meminfo"] = {"error": r[1], "required": True}
            s["memavailable_bytes"] = None
            s["memfree_bytes"] = None
        else:
            mi = parse_meminfo(r)
            s["memavailable_bytes"] = mi.get("MemAvailable")
            s["memfree_bytes"] = mi.get("MemFree")
        # /proc/vmstat (record-only)
        r = self._read("/proc/vmstat", "proc_vmstat", False)
        if isinstance(r, tuple):
            s["errors"]["proc_vmstat"] = {"error": r[1], "required": False}
            s["vmstat"] = None
        else:
            s["vmstat"] = parse_keyval_ints(r)
        # PSI (record-only)
        r = self._read("/proc/pressure/memory", "proc_pressure_memory", False)
        if isinstance(r, tuple):
            s["errors"]["proc_pressure_memory"] = {"error": r[1], "required": False}
            s["psi"] = None
        else:
            s["psi"] = parse_psi(r)
        # cgroup files
        cg = {}
        for name, key, conv in (
            ("memory.current", "current_bytes", int),
            ("memory.swap.current", "swap_current_bytes", int),
            ("memory.peak", "peak_bytes", int),
            ("memory.events", "events", parse_memory_events),
            ("memory.max", "max_bytes", lambda t: t.strip()),
            ("memory.swap.max", "swap_max_bytes", lambda t: t.strip()),
        ):
            required = name in ("memory.current", "memory.swap.current", "memory.events")
            r = self._cg_read(name, required)
            if isinstance(r, tuple):
                s["errors"]["cgroup/" + name] = {"error": r[1], "required": required}
                cg[key] = None
            else:
                try:
                    cg[key] = conv(r)
                except (ValueError, TypeError) as e:
                    s["errors"]["cgroup/" + name] = {"error": repr(e), "required": required}
                    cg[key] = None
        s["cgroup"] = cg
        # docker inspect
        s["docker"] = self._docker_inspect(container_id, s["errors"])
        return s

    def _docker_inspect(self, container_id, errors):
        if self.fixture:
            f = self.fixture / "docker_inspect.json"
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError) as e:
                errors["docker_inspect"] = {"error": repr(e), "required": True}
                return {"id_match": None, "running": None,
                        "oom_killed": None, "restart_count": None,
                        "started_at": None}
        else:
            try:
                out = subprocess.run(
                    ["docker", "inspect", container_id],
                    capture_output=True, text=True, timeout=15, check=True)
                data = json.loads(out.stdout)
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                errors["docker_inspect"] = {"error": repr(e), "required": True}
                return {"id_match": None, "running": None,
                        "oom_killed": None, "restart_count": None,
                        "started_at": None}
        if isinstance(data, list):
            data = data[0] if data else {}
        state = data.get("State", {}) or {}
        ident = data.get("Id")
        return {
            "id_match": (ident == container_id) if ident is not None else None,
            "running": state.get("Running"),
            "oom_killed": state.get("OOMKilled"),
            "restart_count": data.get("RestartCount"),
            "started_at": state.get("StartedAt"),
        }


# ---------------------------------------------------------------- telemetry --


class Telemetry:
    """Append-only JSONL writer. One open/append/close per row so rows
    survive SIGKILL mid-run."""

    def __init__(self, path, cid):
        self.path = Path(path)
        self.cid = cid

    def row(self, kind, **fields):
        row = {"v": 1, "kind": kind, "mono": round(time.monotonic(), 6),
               "utc": utc_now(), "cid": self.cid}
        row.update(fields)
        line = json.dumps(row, sort_keys=True) + "\n"
        with self.path.open("a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return row


# -------------------------------------------------------------------- guard --


class Guard:
    def __init__(self, args):
        self.args = args
        self.cid = args.container_id
        self.tel = Telemetry(args.telemetry, self.cid)
        self.sources = Sources(args.cgroup, fixture=args.fixture)
        self.thresholds = {
            "emergency_bytes": args.emergency_headroom_bytes,
            "promotion_bytes": args.promotion_headroom_bytes,
            "blind_seconds": args.blind_limit,
            "expected_started_at": args.expected_started_at,
        }
        self.baseline = None
        self.prev = None
        self.blind_since = None
        self.window_min_headroom = None
        self.window_max_swap = 0
        self.tripped_ever = False
        self.stopping = False
        self.start_mono = time.monotonic()

    # -- helpers --

    @staticmethod
    def _atomic_write(path, text):
        tmp = str(path) + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _trip_files_present(self):
        if self.args.trip_file and Path(self.args.trip_file).exists():
            return os.fsencode(self.args.trip_file).decode()
        for p in self.args.peer_trip_file or []:
            if Path(p).exists():
                return p
        return None

    def _warn_swap_limit(self, cg):
        """Record cgroup limit expectations; mismatch is WARN, never silent."""
        max_b = cg.get("max_bytes")
        swap_max = cg.get("swap_max_bytes")
        warns = []
        if isinstance(max_b, str) and max_b != "max":
            try:
                if int(max_b) != EXPECTED_MEMORY_MAX_BYTES:
                    warns.append("memory.max=%s expected %d" % (
                        max_b, EXPECTED_MEMORY_MAX_BYTES))
            except ValueError:
                warns.append("memory.max unparseable: %r" % max_b)
        elif max_b == "max":
            warns.append("memory.max=max (expected %d cap)" % EXPECTED_MEMORY_MAX_BYTES)
        if swap_max is not None:
            if swap_max != str(EXPECTED_SWAP_MAX_BYTES):
                warns.append(
                    "memory.swap.max=%s expected %d (docker --memory-swap %d minus --memory; "
                    "see docs/OPERATIONS.md)" % (
                        swap_max, EXPECTED_SWAP_MAX_BYTES, EXPECTED_TOTAL_SWAP_BYTES))
        return warns

    def _do_stop(self, reason):
        """The ONLY mutating docker action: exact CID, stop only, never rm."""
        argv = ["docker", "stop", "-t", str(self.args.docker_stop_timeout), self.cid]
        receipt = {"reasons": [reason] if isinstance(reason, str) else list(reason),
                   "argv": argv, "dry_run": bool(self.args.dry_run)}
        if self.args.dry_run:
            receipt.update(rc=None, note="dry-run: stop not executed")
        else:
            try:
                p = subprocess.run(argv, capture_output=True, text=True, timeout=180)
                receipt.update(rc=p.returncode, stdout=p.stdout.strip(),
                               stderr=p.stderr.strip(),
                               stopped_cid=p.stdout.strip())
            except (OSError, subprocess.SubprocessError) as e:
                receipt.update(rc=None, error=repr(e))
        self.tel.row("stop_receipt", **receipt)
        return receipt

    def _handle_trip(self, reasons, sample):
        self.tripped_ever = True
        if self.args.trip_file:
            try:
                self._atomic_write(self.args.trip_file, json.dumps({
                    "cid": self.cid, "reasons": reasons, "utc": utc_now()}, sort_keys=True))
            except OSError as e:
                self.tel.row("warn", error="trip_file_unwritable", detail=repr(e))
        fields = {k: sample.get(k) for k in
                  ("memavailable_bytes", "memfree_bytes", "psi", "cgroup", "docker")}
        self.tel.row("trip", state=STATE_TRIPPED, reasons=reasons,
                     window_min_memavailable_bytes=self.window_min_headroom, **fields)
        self._do_stop(reasons)
        self.tel.row("end", state=STATE_ENDED, outcome="trip", reasons=reasons)

    def _promotion(self, sample, eligible):
        row = {
            "eligible": eligible,
            "window_min_memavailable_bytes": self.window_min_headroom,
            "promotion_floor_bytes": self.thresholds["promotion_bytes"],
            "emergency_floor_bytes": self.thresholds["emergency_bytes"],
            "window_max_swap_bytes": self.window_max_swap,
        }
        self.tel.row("promotion", **row)
        if eligible and self.args.promotion_marker:
            self._atomic_write(self.args.promotion_marker, json.dumps(
                {"cid": self.cid, "utc": utc_now(),
                 "window_min_memavailable_bytes": self.window_min_headroom,
                 "window_max_swap_bytes": self.window_max_swap},
                sort_keys=True))

    # -- main loop --

    def run(self):
        a = self.args
        self.tel.row(
            "start", state=STATE_WATCHING,
            cgroup=str(a.cgroup), interval_s=a.interval, blind_seconds=a.blind_limit,
            emergency_headroom_bytes=a.emergency_headroom_bytes,
            promotion_headroom_bytes=a.promotion_headroom_bytes,
            dry_run=bool(a.dry_run), fixture=str(a.fixture) if a.fixture else None,
            expected_memory_max_bytes=EXPECTED_MEMORY_MAX_BYTES,
            expected_swap_max_bytes=EXPECTED_SWAP_MAX_BYTES,
            once=bool(a.once), max_runtime_s=a.max_runtime,
            trip_file=a.trip_file, peer_trip_files=a.peer_trip_file,
            docker_stop_timeout_s=a.docker_stop_timeout,
            expected_started_at=a.expected_started_at)

        # Pre-loop identity check: never watch (and never stop) a container
        # that is already known to be the wrong one.
        pre = self.sources.gather(self.cid)
        if pre["docker"].get("id_match") is False:
            self.tel.row("trip", state=STATE_TRIPPED, reasons=[TRIP_IDENTITY],
                         phase="pre-start-refusal", docker=pre["docker"])
            self.tel.row("end", state=STATE_ENDED, outcome="refused_identity_mismatch")
            print("exl3_guard: docker inspect returned a different container id; "
                  "refusing to watch or stop anything", file=sys.stderr)
            return 65

        next_tick = time.monotonic()
        while not self.stopping:
            now = time.monotonic()
            if a.max_runtime and (now - self.start_mono) >= a.max_runtime:
                break
            sample = self.sources.gather(self.cid)
            sample["peer_trip"] = self._trip_files_present()

            blind_elapsed = None
            blind_critical = any(e.get("required") for e in sample["errors"].values())
            if blind_critical:
                self.blind_since = self.blind_since or now
                blind_elapsed = now - self.blind_since
            else:
                self.blind_since = None

            if sample["memavailable_bytes"] is not None:
                self.window_min_headroom = (
                    sample["memavailable_bytes"] if self.window_min_headroom is None
                    else min(self.window_min_headroom, sample["memavailable_bytes"]))
            swap_now = sample["cgroup"].get("swap_current_bytes") or 0
            self.window_max_swap = max(self.window_max_swap, swap_now)

            if self.baseline is None and not blind_critical:
                self.baseline = {
                    "cgroup": {"events": dict(sample["cgroup"].get("events") or {})},
                    "docker": {"restart_count": sample["docker"].get("restart_count")},
                }

            sample["window_min_memavailable_bytes"] = self.window_min_headroom
            sample["window_max_swap_bytes"] = self.window_max_swap
            sample["tripped_ever"] = self.tripped_ever

            state, reasons, band, eligible = decide(
                sample, self.baseline, self.thresholds, blind_elapsed)

            vm = sample.get("vmstat") or {}
            prev_vm = (self.prev or {}).get("vmstat") or {}
            row = {
                "state": state, "reasons": reasons, "band": band,
                "memavailable_bytes": sample["memavailable_bytes"],
                "memfree_bytes": sample["memfree_bytes"],
                "psi": sample["psi"],
                "pswpin_cum": vm.get("pswpin"), "pswpout_cum": vm.get("pswpout"),
                "pswpin_delta": (vm.get("pswpin") - prev_vm["pswpin"])
                                if "pswpin" in vm and "pswpin" in prev_vm else None,
                "pswpout_delta": (vm.get("pswpout") - prev_vm["pswpout"])
                                 if "pswpout" in vm and "pswpout" in prev_vm else None,
                "cgroup": sample["cgroup"], "docker": sample["docker"],
                "peer_trip": sample["peer_trip"],
                "blind_s": round(blind_elapsed, 3) if blind_elapsed is not None else None,
                "errors": sample["errors"] or None,
                "window_min_memavailable_bytes": self.window_min_headroom,
                "window_max_swap_bytes": self.window_max_swap,
                "promotion_eligible": eligible,
            }
            self.tel.row("sample", **row)
            for w in self._warn_swap_limit(sample["cgroup"]):
                self.tel.row("warn", check="cgroup_limits", detail=w)
            self.prev = sample

            if reasons:
                self._handle_trip(reasons, sample)
                return 4

            if a.once:
                break
            next_tick += a.interval
            sleep_for = max(0.0, next_tick - time.monotonic())
            time.sleep(min(sleep_for, a.interval))

        eligible = (
            self.baseline is not None and not self.tripped_ever
            and self.window_min_headroom is not None
            and self.window_min_headroom >= self.thresholds["promotion_bytes"]
            and self.window_max_swap == 0)
        self._promotion(None, eligible)
        self.tel.row("end", state=STATE_ENDED, outcome="clean",
                     window_min_memavailable_bytes=self.window_min_headroom)
        return 0


# ---------------------------------------------------------------------- CLI --


def positive_int(s):
    v = int(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return v


def bytes_arg(s):
    v = int(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be > 0 bytes")
    return v


def build_parser():
    p = argparse.ArgumentParser(
        prog="exl3_guard.py",
        description="Persistent per-rank EXL3 guard: one exact container id, "
                    "cgroup v2 + PSI + swap telemetry, exact-CID emergency stop.",
        epilog="Fixture mode replaces /proc, cgroup and docker-inspect reads "
               "with files; see module docstring for the layout.")
    p.add_argument("--container-id", required=True,
                   help="EXACT full 64-hex container id. Names, prefixes and "
                        "patterns are refused (exit 64).")
    p.add_argument("--cgroup", required=True,
                   help="cgroup v2 directory of that container, e.g. "
                        "/sys/fs/cgroup/system.slice/docker-<cid>.scope. "
                        "Required; no default. Its name must contain the cid.")
    p.add_argument("--telemetry", required=True,
                   help="append-only JSONL output path (GUARD.jsonl)")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                   help="sample cadence seconds (default %(default)s = "
                        "qualification; use 5 while idle)")
    p.add_argument("--blind-limit", type=int, default=DEFAULT_BLIND_SECONDS,
                   help="seconds of stale/unreachable control telemetry before "
                        "a fail-closed stop (default %(default)s per EXL3-SPEC; "
                        "hard-capped at 60)")
    p.add_argument("--emergency-headroom-bytes", type=bytes_arg,
                   default=DEFAULT_EMERGENCY_BYTES,
                   help="emergency physical floor (default %(default)s = 4 GiB)")
    p.add_argument("--promotion-headroom-bytes", type=bytes_arg,
                   default=DEFAULT_PROMOTION_BYTES,
                   help="promotion floor (default %(default)s = 5 GiB)")
    p.add_argument("--docker-stop-timeout", type=positive_int, default=15,
                   help="docker stop -t seconds (default %(default)s)")
    p.add_argument("--expected-started-at", default=None,
                   help="owner-registered Docker StartedAt for this exact "
                        "CID; a live mismatch trips "
                        "CONTAINER_INCARNATION_CHANGED")
    p.add_argument("--trip-file",
                   help="file this guard writes on trip so sibling guards on "
                        "the other hosts stop their ranks too (coupled stop)")
    p.add_argument("--peer-trip-file", action="append", default=[],
                   help="peer guard trip file to poll (repeatable)")
    p.add_argument("--promotion-marker",
                   help="file written at clean end when the promotion gate "
                        "(>=5 GiB window minimum, swap exactly 0, no trip) holds")
    p.add_argument("--max-runtime", type=float, default=0,
                   help="end cleanly after N seconds (0 = run until trip/signal; "
                        "the spec has no 50-minute expiry)")
    p.add_argument("--once", action="store_true",
                   help="take exactly one sample then end cleanly")
    p.add_argument("--dry-run", action="store_true",
                   help="record the stop action without executing docker")
    p.add_argument("--fixture",
                   help="fixture directory replacing /proc, cgroup and "
                        "docker-inspect reads (tests, dry rehearsal)")
    return p


def refuse(msg):
    print("exl3_guard: %s" % msg, file=sys.stderr)
    sys.exit(64)


def validate_args(args, parser):
    if not CID_RE.match(args.container_id):
        refuse(
            "refusing target %r: container id must be the exact full 64-hex "
            "id; names/patterns/partial ids are not accepted" % args.container_id)
    if not (1 <= args.blind_limit <= MAX_BLIND_SECONDS):
        refuse("blind limit must be within [1,%d] seconds "
               "(spec 15; brief caps at 60)" % MAX_BLIND_SECONDS)
    if args.promotion_headroom_bytes <= args.emergency_headroom_bytes:
        refuse("promotion floor must exceed the emergency floor")
    if args.interval <= 0:
        refuse("interval must be > 0")
    cg = Path(args.cgroup)
    if not cg.is_dir():
        print("exl3_guard: cgroup dir %s does not exist" % cg, file=sys.stderr)
        sys.exit(66)
    if args.container_id not in cg.name:
        print("exl3_guard: cgroup dir name %r does not contain the exact "
              "container id; refusing (guards exactly one declared container)"
              % cg.name, file=sys.stderr)
        sys.exit(65)
    if not (cg / "memory.current").exists() and not args.fixture:
        print("exl3_guard: %s/memory.current missing; not a cgroup v2 dir"
              % cg, file=sys.stderr)
        sys.exit(66)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)

    guard = Guard(args)

    def _stop(signum, _frame):
        guard.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        rc = guard.run()
    except Exception as e:  # internal failure: record and fail loudly
        try:
            guard.tel.row("end", state=STATE_ENDED, outcome="internal_error",
                          error=repr(e))
        except Exception:
            pass
        print("exl3_guard: internal error: %r" % e, file=sys.stderr)
        return 70
    return rc


if __name__ == "__main__":
    sys.exit(main())
