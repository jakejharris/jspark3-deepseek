"""Mock tests for the persistent EXL3 guard (exl3_guard.py).

All tests run the guard against synthetic fixture /proc + cgroup +
docker-inspect data in --once --dry-run mode: no host state, no docker, no
GPU, no ssh (the campaign's PREP boundary).
"""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from guardhelpers import CID, OTHER_CID, make_cgroup, make_fixture, run_guard

GIB = 1024 ** 3


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "exl3_guard", Path(__file__).resolve().parent.parent / "tools/exl3_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard_mod():
    return _load_module()


# ------------------------------------------------------------- refusals ----


def test_help_exits_zero():
    import subprocess, sys
    from guardhelpers import GUARD
    p = subprocess.run([sys.executable, str(GUARD), "--help"],
                       capture_output=True, text=True)
    assert p.returncode == 0
    assert "container-id" in p.stdout.lower()


def test_refuses_name_pattern_target(tmp_path):
    fx = make_fixture(tmp_path / "fx")
    cg = make_cgroup(tmp_path / "cg", "c" * 64)
    for bad in ("vllm_dsv41", "exl3-E0-r0", "a" * 12, "A" * 64, "zz-*"):
        rc, rows, err = run_guard(fx, cg, cid=bad, tmp_path=tmp_path)
        assert rc == 64, bad
        assert "refusing" in err.lower()


def test_refuses_cgroup_cid_mismatch(tmp_path):
    fx = make_fixture(tmp_path / "fx")
    cg = make_cgroup(tmp_path / "cg", "c" * 64)  # dir belongs to another cid
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    assert rc == 65
    assert "does not contain the exact container id" in err


def test_refuses_missing_cgroup(tmp_path):
    fx = make_fixture(tmp_path / "fx")
    rc, rows, err = run_guard(fx, tmp_path / "nope", tmp_path=tmp_path)
    assert rc == 66


def test_refuses_out_of_spec_blind_window(tmp_path):
    fx = make_fixture(tmp_path / "fx")
    cg = make_cgroup(tmp_path / "cg", CID)
    rc, rows, err = run_guard(fx, cg, extra=["--blind-limit", "61"],
                              tmp_path=tmp_path)
    assert rc == 64
    rc, rows, err = run_guard(fx, cg, extra=["--blind-limit", "0"],
                              tmp_path=tmp_path)
    assert rc == 64


# ----------------------------------------------------------- trip cases ----


def _trip_case(tmp_path, reason, **fixture_kw):
    fx = make_fixture(tmp_path / "fx", **fixture_kw.get("fx", {}))
    cg = make_cgroup(tmp_path / "cg", CID, **fixture_kw.get("cg", {}))
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path,
                              extra=fixture_kw.get("extra", []))
    trips = [r for r in rows if r["kind"] == "trip"]
    assert rc == 4, err
    assert trips and trips[0]["reasons"] == [reason], rows
    receipts = [r for r in rows if r["kind"] == "stop_receipt"]
    assert receipts, rows
    return receipts[0]


@pytest.mark.parametrize("swap_bytes", [1, 3608576, 2 * GIB])
def test_service_swap_is_telemetry_only(tmp_path, swap_bytes):
    fx = make_fixture(tmp_path / "fx")
    cg = make_cgroup(tmp_path / "cg", CID, swap_current=swap_bytes, swap_max="max")
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    assert rc == 0, err
    assert not any(r["kind"] in ("trip", "stop_receipt") for r in rows)
    sample = next(r for r in rows if r["kind"] == "sample")
    assert sample["state"] == "WATCHING"
    assert sample["cgroup"]["swap_current_bytes"] == swap_bytes
    assert sample["window_max_swap_bytes"] == swap_bytes
    assert sample["promotion_eligible"] is False  # benchmark annotation only
    assert any(r["kind"] == "warn" and "memory.swap.max=max" in r["detail"] for r in rows)


def test_swap_does_not_mask_actual_oom(tmp_path):
    _trip_case(tmp_path, "CGROUP_OOM_EVENTS",
               cg={"swap_current": 3608576, "events": {"oom_kill": 1}})


def test_trips_on_headroom_emergency(tmp_path):
    _trip_case(tmp_path, "HEADROOM_EMERGENCY", fx={"memavailable_gib": 3.5})


def test_trips_on_new_oom_events(tmp_path):
    _trip_case(tmp_path, "CGROUP_OOM_EVENTS",
               cg={"events": {"oom_kill": 1}})


def test_trips_on_rank_death(tmp_path):
    _trip_case(tmp_path, "RANK_DEATH_OR_RESTART",
               fx={"running": False})


def test_trips_on_restart_count_change(tmp_path):
    _trip_case(tmp_path, "RANK_DEATH_OR_RESTART",
               fx={"restart_count": 1})


def test_trips_on_peer_guard_trip_file(tmp_path):
    peer = tmp_path / "peer-trip"
    peer.write_text("{}\n")
    _trip_case(tmp_path, "PEER_GUARD_TRIP", extra=["--peer-trip-file", str(peer)])


def test_trips_and_writes_trip_file_for_peers(tmp_path):
    trip = tmp_path / "my-trip"
    fx = make_fixture(tmp_path / "fx", memavailable_gib=3.0)
    cg = make_cgroup(tmp_path / "cg", CID)
    rc, rows, err = run_guard(fx, cg, extra=["--trip-file", str(trip)],
                              tmp_path=tmp_path)
    assert rc == 4
    assert json.loads(trip.read_text())["cid"] == CID


def test_identity_mismatch_stops_only_requested_cid(tmp_path, guard_mod):
    """inspect returns a different container: the in-loop decision must trip
    on CONTAINER_IDENTITY_MISMATCH, and the only stop target ever referenced
    is the exact requested CID (other CIDs are ignored)."""
    sample = {"memavailable_bytes": 8 * GIB,
              "cgroup": {"swap_current_bytes": 0, "events": {"oom": 0}},
              "docker": {"id_match": False, "running": True,
                         "oom_killed": False, "restart_count": 0}}
    thr = {"emergency_bytes": 4 * GIB, "promotion_bytes": 5 * GIB,
           "blind_seconds": 15}
    state, reasons, _, _ = guard_mod.decide(sample, None, thr, None)
    assert guard_mod.TRIP_IDENTITY in reasons

    # CLI level: the pre-start check refuses before the loop ever starts,
    # so no docker stop is issued against ANY container.
    fx = make_fixture(tmp_path / "fx", inspect_id=OTHER_CID)
    cg = make_cgroup(tmp_path / "cg", CID)
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    assert rc == 65
    assert not [r for r in rows if r["kind"] == "stop_receipt"]
    assert OTHER_CID not in json.dumps(rows)


def test_pre_start_identity_mismatch_refuses_to_run(tmp_path):
    fx = make_fixture(tmp_path / "fx", inspect_id=OTHER_CID)
    cg = make_cgroup(tmp_path / "cg", CID)
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    # pre-loop refusal: no stop of anything, exit 65
    assert rc == 65
    assert not [r for r in rows if r["kind"] == "stop_receipt"]


# ------------------------------------------------------- healthy / bands ----


def test_healthy_once_no_trip(tmp_path):
    fx = make_fixture(tmp_path / "fx", memavailable_gib=8.0)
    cg = make_cgroup(tmp_path / "cg", CID)
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    assert rc == 0, err
    assert not [r for r in rows if r["kind"] in ("trip", "stop_receipt")]
    sample = [r for r in rows if r["kind"] == "sample"][0]
    assert sample["state"] == "WATCHING"
    assert sample["band"] == "PROMOTE_OK"
    assert sample["promotion_eligible"] is True


def test_hold_band_between_4_and_5_gib(tmp_path):
    fx = make_fixture(tmp_path / "fx", memavailable_gib=4.5)
    cg = make_cgroup(tmp_path / "cg", CID)
    marker = tmp_path / "PROMOTION-OK"
    rc, rows, err = run_guard(fx, cg, extra=["--promotion-marker", str(marker)],
                              tmp_path=tmp_path)
    assert rc == 0, err  # 4.5 GiB is above the 4 GiB emergency floor: no stop
    sample = [r for r in rows if r["kind"] == "sample"][0]
    assert sample["band"] == "HOLD"
    assert sample["promotion_eligible"] is False
    assert not marker.exists()          # no promotion signal in [4,5)


def test_promotion_marker_only_at_or_above_5_gib(tmp_path):
    fx = make_fixture(tmp_path / "fx", memavailable_gib=5.0)
    cg = make_cgroup(tmp_path / "cg", CID)
    marker = tmp_path / "PROMOTION-OK"
    rc, rows, err = run_guard(fx, cg, extra=["--promotion-marker", str(marker)],
                              tmp_path=tmp_path)
    assert rc == 0, err
    assert marker.exists()
    promo = json.loads(marker.read_text())
    assert promo["cid"] == CID
    assert promo["window_min_memavailable_bytes"] >= 5 * GIB
    assert [r for r in rows if r["kind"] == "promotion"][0]["eligible"] is True


def test_emergency_boundary_is_exclusive_at_4_gib(tmp_path):
    fx = make_fixture(tmp_path / "fx", memavailable_gib=4.0)
    cg = make_cgroup(tmp_path / "cg", CID)
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    assert rc == 0, err  # exactly 4 GiB is not "< 4 GiB"
    sample = [r for r in rows if r["kind"] == "sample"][0]
    assert sample["band"] == "HOLD"


def test_swap_limit_anomaly_is_warned_not_silent(tmp_path):
    """An unbounded memory.swap.max limit must be visible."""
    fx = make_fixture(tmp_path / "fx")
    cg = make_cgroup(tmp_path / "cg", CID, swap_max="max")
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    assert rc == 0, err
    warns = [r for r in rows if r["kind"] == "warn"]
    assert any("memory.swap.max=max" in r["detail"] for r in warns), rows


# ------------------------------------------------------------ telemetry ----


REQUIRED_SAMPLE_KEYS = {
    "v", "kind", "mono", "utc", "cid", "state", "reasons", "band",
    "memavailable_bytes", "memfree_bytes", "psi",
    "pswpin_cum", "pswpout_cum", "pswpin_delta", "pswpout_delta",
    "cgroup", "docker", "peer_trip", "blind_s", "errors",
    "window_min_memavailable_bytes", "window_max_swap_bytes",
    "promotion_eligible",
}


def test_telemetry_row_schema(tmp_path):
    fx = make_fixture(tmp_path / "fx", pswpin=7, pswpout=9)
    cg = make_cgroup(tmp_path / "cg", CID, events={"oom": 0, "oom_kill": 0})
    rc, rows, err = run_guard(fx, cg, tmp_path=tmp_path)
    assert rc == 0, err
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "start" and kinds[-1] == "end"
    assert "sample" in kinds and "promotion" in kinds
    for r in rows:
        assert set(REQUIRED_SAMPLE_KEYS) <= set(r) if r["kind"] == "sample" else True
        assert isinstance(r["mono"], float)
        datetime.fromisoformat(r["utc"])       # parseable UTC ISO stamp
        assert r["cid"] == CID
    sample = [r for r in rows if r["kind"] == "sample"][0]
    assert sample["pswpin_cum"] == 7 and sample["pswpout_cum"] == 9
    assert sample["cgroup"]["events"]["oom"] == 0
    assert sample["psi"]["some"]["avg10"] == 0.0
    assert sample["memavailable_bytes"] == 8 * GIB
    # monotonic stamps never decrease
    monos = [r["mono"] for r in rows]
    assert monos == sorted(monos)


def test_telemetry_is_append_only_across_runs(tmp_path):
    fx = make_fixture(tmp_path / "fx")
    cg = make_cgroup(tmp_path / "cg", CID)
    tel = tmp_path / "GUARD.jsonl"
    run_guard(fx, cg, telemetry=str(tel), tmp_path=tmp_path)
    first = tel.read_text().splitlines()
    fx2 = make_fixture(tmp_path / "fx2", memavailable_gib=3.0)
    run_guard(fx2, cg, telemetry=str(tel), tmp_path=tmp_path)
    lines = tel.read_text().splitlines()
    assert len(lines) > len(first)
    assert lines[:len(first)] == first       # earlier rows untouched


# ------------------------------------------------- blind window (unit) ----


def test_blind_decision_logic(guard_mod):
    """Stale/unreachable control telemetry past the window must trip."""
    thr = {"emergency_bytes": 4 * GIB, "promotion_bytes": 5 * GIB,
           "blind_seconds": 15}
    base = {"cgroup": {"events": {"oom": 0}}, "docker": {"restart_count": 0}}
    good = {"memavailable_bytes": 8 * GIB, "cgroup": {"swap_current_bytes": 0,
            "events": {"oom": 0}},
            "docker": {"id_match": True, "running": True, "oom_killed": False,
                       "restart_count": 0}}
    state, reasons, _, _ = guard_mod.decide(good, base, thr, None)
    assert state == guard_mod.STATE_WATCHING and not reasons
    # 14.9 s blind: still watching (spec default 15 s)
    state, reasons, _, _ = guard_mod.decide(good, base, thr, 14.9)
    assert state == guard_mod.STATE_BLIND and not reasons
    # 15 s blind: fail-closed trip
    state, reasons, _, _ = guard_mod.decide(good, base, thr, 15.0)
    assert guard_mod.TRIP_BLIND in reasons
    assert state == guard_mod.STATE_TRIPPED


def test_psi_and_vmstat_recorded_per_sample(guard_mod):
    psi = guard_mod.parse_psi(
        "some avg10=1.23 avg60=4.56 avg300=7.89 total=12345\n"
        "full avg10=0.10 avg60=0.20 avg300=0.30 total=999\n")
    assert psi == {"some": {"avg10": 1.23, "avg60": 4.56, "avg300": 7.89,
                            "total": 12345.0},
                   "full": {"avg10": 0.1, "avg60": 0.2, "avg300": 0.3,
                            "total": 999.0}}
    vm = guard_mod.parse_keyval_ints("pswpin 42\npswpout 7\nnr_free_pages 1\n")
    assert vm["pswpin"] == 42 and vm["pswpout"] == 7
