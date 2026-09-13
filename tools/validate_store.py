#!/usr/bin/env python3
"""Validate a per-rank sparse Engram store against the derived bounds.

Covers the spec's engram-store-full-validation bar:
  * full owned logical ranges present and byte-identical (with --source:
    streaming full-range compare, boundary rows, deterministic random rows;
    zero mismatches required)
  * safetensors header intact (byte-equal to source / manifest sha256),
    tensor geometry equals the derivation, file trailer consistent
  * sparse layout: every owned byte allocated (SEEK_DATA/SEEK_H walk),
    apparent vs allocated bytes recorded
  * sizes match derivation; engram-local.json satisfies the loader's
    coverage contract (lo <= row_start && row_start+num_rows <= hi)

Usage:
  validate_store.py STORE --bounds bounds.json --rank N [--source SRC_DIR]
                       [--emit report.json] [--random-rows N]
Exit code 0 = all checks pass; 1 = failures (listed); 2 = usage/structural.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

SEEK_DATA = getattr(os, "SEEK_DATA", 3)
SEEK_H = getattr(os, "SEEK_H", 4)
CHUNK = 32 << 20


def read_header_path(path: str) -> Tuple[int, Dict[str, Any]]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return 8 + n, json.loads(f.read(n))


def pread_exact(fd: int, size: int, off: int) -> bytes:
    parts, got = [], 0
    while got < size:
        b = os.pread(fd, size - got, off + got)
        if not b:
            raise OSError(f"short read at {off + got}")
        parts.append(b)
        got += len(b)
    return b"".join(parts)


def sha256_range(fd: int, off: int, length: int) -> str:
    h = hashlib.sha256()
    pos = 0
    while pos < length:
        h.update(pread_exact(fd, min(CHUNK, length - pos), off + pos))
        pos += min(CHUNK, length - pos)
    return h.hexdigest()


def allocated_extents(fd: int, size: int) -> List[Tuple[int, int]]:
    """Data (non-hole) extents via lseek(SEEK_DATA/SEEK_H); [] if unsupported."""
    extents: List[Tuple[int, int]] = []
    pos = 0
    while pos < size:
        try:
            d = os.lseek(fd, pos, SEEK_DATA)
        except OSError:
            break  # ENXIO: nothing allocated at/after pos
        if d < 0 or d >= size:
            break
        try:
            h = os.lseek(fd, d, SEEK_H)
        except OSError:
            h = size
        if h < 0 or h > size:
            h = size
        extents.append((d, h))
        pos = h if h > d else size
    return extents


def covered_by(rng: Tuple[int, int], extents: List[Tuple[int, int]]) -> bool:
    s, e = rng
    pos = s
    for d, h in extents:
        if h <= pos:
            continue
        if d > pos:
            return False  # hole before this extent
        pos = h
        if pos >= e:
            return True
    return pos >= e


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("store")
    ap.add_argument("--bounds", required=True)
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--source", help="full Engram source dir for byte-exact comparison")
    ap.add_argument("--emit", help="write the validation report JSON here")
    ap.add_argument("--random-rows", type=int, default=512)
    args = ap.parse_args(argv)

    bounds = json.load(open(args.bounds))
    failures: List[str] = []
    warnings: List[str] = []
    report: Dict[str, Any] = {
        "schema": "engram-store-validation/1",
        "store": os.path.abspath(args.store),
        "rank": args.rank,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": [],
    }

    def check(name: str, ok: bool, detail: str = "") -> bool:
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}" if detail else name)
        return ok

    # ---- structure ---------------------------------------------------------
    local_path = os.path.join(args.store, "engram-local.json")
    ok = os.path.isfile(local_path)
    check("engram-local.json present", ok)
    local = json.load(open(local_path)) if ok else {"layers": {}}
    idx_path = os.path.join(args.store, "model.safetensors.index.json")
    check("model.safetensors.index.json present", os.path.isfile(idx_path))
    wmap = json.load(open(idx_path))["weight_map"] if os.path.isfile(idx_path) else {}
    manifest_path = os.path.join(args.store, "store-manifest.json")
    manifest = json.load(open(manifest_path)) if os.path.isfile(manifest_path) else None
    if manifest is None:
        warnings.append("store-manifest.json missing (Tony-era store?); sha256 checks limited")

    # ---- per-layer validation ----------------------------------------------
    rng = random.Random(0)  # deterministic across reruns
    shard_report: Dict[str, Any] = {}
    regions: Dict[str, Dict[str, Any]] = {}
    for layer in bounds["layers"]:
        L = layer["layer"]
        rank_info = layer["ranks"][args.rank]
        s, e = rank_info["row_start"], rank_info["row_end"]

        # loader coverage contract (_dsv41_engram_local_dir): [lo, hi) must cover
        # [row_start, row_start + num_rows) for BOTH weight and scale reads.
        lohi = local.get("layers", {}).get(str(L))
        check(
            f"layer {L}: engram-local.json covers derived rank range",
            isinstance(lohi, list) and lohi[0] <= s and e <= lohi[1],
            f"local={lohi} derived=[{s}, {e})",
        )
        check(f"layer {L}: engram-local.json equals derived range", lohi == [s, e], f"local={lohi}")

        for kind in ("weight", "scale"):
            name = f"layers.{L}.engram.embed.{kind}"
            rb_key = f"{kind}_row_bytes"
            rb = bounds.get(rb_key) or (bounds["head_dim"] if kind == "weight" else bounds["head_dim"] // bounds["quant_block"])
            shard = wmap.get(name)
            if not check(f"{name}: index entry", bool(shard), str(shard)):
                continue
            spath = os.path.join(args.store, shard)
            if not check(f"{name}: store shard file present", os.path.isfile(spath), shard):
                continue
            prefix, hdr = read_header_path(spath)
            meta = hdr.get(name)
            if not check(f"{name}: in store shard header", meta is not None):
                continue
            check(f"{name}: header rows == derived total", meta["shape"][0] == layer["rows"],
                  f"header={meta['shape'][0]} derived={layer['rows']}")
            check(f"{name}: header row bytes == derived", meta["shape"][1] == rb,
                  f"header={meta['shape'][1]} derived={rb}")
            geo = (layer.get("tensors", {}).get(kind) or {})
            if geo.get("data_offsets") and geo.get("row_bytes"):
                check(f"{name}: data_offsets match geometry", meta["data_offsets"] == geo["data_offsets"],
                      f"header={meta['data_offsets']} geometry={geo['data_offsets']}")

            data_end = max(m["data_offsets"][1] for m in hdr.values() if "data_offsets" in m)
            size = os.path.getsize(spath)
            check(f"{name}: shard apparent size == 8+header+data_end (trailer)",
                  size == prefix + data_end, f"size={size} expected={prefix + data_end}")

            base = prefix + meta["data_offsets"][0]
            owned = (base + s * rb, base + e * rb)
            regions.setdefault(shard, {"prefix": prefix, "owned": []})
            regions[shard]["owned"].append(owned)

            fd = os.open(spath, os.O_RDONLY)
            try:
                extents = allocated_extents(fd, size)
                check(f"{name}: owned byte range fully allocated (sparse coverage)",
                      covered_by(owned, extents),
                      f"owned=[{owned[0]}, {owned[1]}) extents={len(extents)}")
                st = os.fstat(fd)
                shard_report.setdefault(shard, {}).update(
                    apparent_bytes=st.st_size,
                    allocated_bytes_st_blocks=st.st_blocks * 512,
                )

                # ---- content ----
                mrange = None
                if manifest:
                    mrange = next((r for r in manifest.get("ranges", [])
                                   if r["tensor"] == name), None)
                store_sha = sha256_range(fd, owned[0], owned[1] - owned[0])
                if mrange:
                    check(f"{name}: owned-range sha256 == manifest", store_sha == mrange["sha256"],
                          f"store={store_sha[:16]}.. manifest={mrange['sha256'][:16]}..")
                if args.source:
                    src_shard = os.path.join(args.source, shard)
                    sfd = os.open(src_shard, os.O_RDONLY)
                    try:
                        mism = 0
                        pos = owned[0]
                        while pos < owned[1]:
                            n = min(CHUNK, owned[1] - pos)
                            if pread_exact(sfd, n, pos) != pread_exact(fd, n, pos):
                                mism += 1
                            pos += n
                        check(f"{name}: full owned-range byte compare vs source", mism == 0,
                              f"{mism} mismatching chunks of {((owned[1]-owned[0])+CHUNK-1)//CHUNK}")
                        src_sha = sha256_range(sfd, owned[0], owned[1] - owned[0])
                        check(f"{name}: full owned-range sha256 vs source", store_sha == src_sha,
                              f"store={store_sha[:16]}.. source={src_sha[:16]}..")
                        rows = [s, s + 1, e - 2, e - 1] + [rng.randrange(s, e) for _ in range(args.random_rows)]
                        bad_rows = []
                        for r in rows:
                            o = base + r * rb
                            if pread_exact(sfd, rb, o) != pread_exact(fd, rb, o):
                                bad_rows.append(r)
                        check(f"{name}: boundary+deterministic random rows vs source", not bad_rows,
                              f"{len(bad_rows)} bad of {len(rows)} (first: {bad_rows[:3]})")
                        check(f"{name}: store header bytes == source header bytes",
                              pread_exact(sfd, prefix, 0) == pread_exact(fd, prefix, 0))
                    finally:
                        os.close(sfd)
            finally:
                os.close(fd)

    # ---- per-shard sparse accounting (header + all owned ranges) ----------
    for shard, info in regions.items():
        spath = os.path.join(args.store, shard)
        prefix = info["prefix"]
        fd = os.open(spath, os.O_RDONLY)
        try:
            size = os.path.getsize(spath)
            extents = allocated_extents(fd, size)
            alloc_total = sum(h - d for d, h in extents)
            expected = [(0, prefix)] + info["owned"]
            covered = 0
            for d, h in extents:
                inside = sum(min(h, b) - max(d, a) for a, b in expected if min(h, b) > max(d, a))
                covered += inside
            shard_report[shard].update(
                allocated_bytes_extents=alloc_total,
                expected_bytes_header_plus_owned=sum(b - a for a, b in expected),
                extent_coverage_slack_bytes=alloc_total - covered,
            )
            if alloc_total - covered > 2 * 1024 * 1024:
                warnings.append(
                    f"{shard}: {alloc_total - covered} allocated bytes outside header+owned ranges"
                )
        finally:
            os.close(fd)

    report["shards"] = shard_report
    report["failures"] = failures
    report["warnings"] = warnings
    report["verdict"] = "PASS" if not failures else "FAIL"
    if args.emit:
        json.dump(report, open(args.emit, "w"), indent=1)
    print(json.dumps({k: report[k] for k in ("verdict", "failures", "warnings")}, indent=1))
    for shard, info in shard_report.items():
        print(f"{shard}: apparent {info['apparent_bytes']/2**30:.1f} GiB, "
              f"allocated(st_blocks) {info['allocated_bytes_st_blocks']/2**30:.1f} GiB, "
              f"extents {info['allocated_bytes_extents']/2**30:.1f} GiB")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
