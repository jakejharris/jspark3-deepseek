#!/usr/bin/env python3
"""Materialize a per-rank sparse Engram store from a full Engram source.

Successor of Tony's tools/engram_local.py (recipe pin c2c1bb7), keeping its
sound core - sparse safetensors copy at ORIGINAL byte offsets, verbatim
shard header, verbatim index copy, engram-local.json - and adding:

  * bounds come from derive_bounds.py (derived, not hand-copied); explicit
    Tony-style ``LAYER:START:END`` specs still accepted for compatibility
  * geometry validation against the source shard headers BEFORE copying
    (tensor shapes == derived rows, row bytes, data_offsets, file trailer)
  * write-back verification of every copied chunk (default; the spec's
    zero-mismatch full-range check), not just 2000 random rows
  * per-range sha256 in a store-manifest.json (plus optional whole-source
    sha256 with --hash-source full)
  * refuses to reuse an existing destination shard (Tony re-opened a stale
    file with O_CREAT, which could keep aborted-run bytes in unowned holes)

Behavioral differences vs engram_local.py are flagged [DIFF] below and in
DERIVATION.md.

Usage:
  make_store.py SRC_DIR DST_DIR --bounds bounds.json --rank N
                [--mbps MBPS] [--verify full|random|none] [--random-rows N]
                [--hash-source none|full]
  make_store.py SRC_DIR DST_DIR LAYER:START:END [LAYER:START:END ...]   # Tony compat
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

CHUNK = 32 << 20  # Tony's read chunk

# safetensors header of one shard: (prefix_len, {tensor: meta})
def read_header(path: str) -> Tuple[int, Dict[str, Any]]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return 8 + n, json.loads(f.read(n))


def pwrite_all(fd: int, buf: bytes, off: int) -> None:
    view = memoryview(buf)
    while view:
        n = os.pwrite(fd, view, off)
        if n <= 0:
            raise OSError("short write")
        view = view[n:]
        off += n


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
        buf = pread_exact(fd, min(CHUNK, length - pos), off + pos)
        h.update(buf)
        pos += len(buf)
    return h.hexdigest()


def load_specs(args) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Normalize --bounds/--rank or positional LAYER:START:END into job specs."""
    if args.bounds:
        if args.rank is None:
            raise SystemExit("--rank is required with --bounds")
        bounds = json.load(open(args.bounds))
        specs, layer_bounds = [], {}
        for layer in bounds["layers"]:
            r = layer["ranks"][args.rank]
            specs.append(
                {
                    "layer": layer["layer"],
                    "row_start": r["row_start"],
                    "row_end": r["row_end"],
                    "derived_rows": layer["rows"],
                    "weight_row_bytes": bounds["weight_row_bytes"],
                    "scale_row_bytes": bounds["scale_row_bytes"],
                }
            )
            layer_bounds[str(layer["layer"])] = [r["row_start"], r["row_end"]]
        return specs, {"bounds": bounds, "rank": args.rank, "layer_ranges": layer_bounds}
    if not args.specs:
        raise SystemExit("need --bounds/--rank or LAYER:START:END specs")
    specs = []
    for spec in args.specs:
        layer, start, end = (int(x) for x in spec.split(":"))
        if not (0 <= start < end):
            raise SystemExit(f"bad spec {spec}")
        specs.append(
            {
                "layer": layer,
                "row_start": start,
                "row_end": end,
                "derived_rows": None,
                "weight_row_bytes": None,
                "scale_row_bytes": None,
            }
        )
    return specs, None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("specs", nargs="*", help="LAYER:START:END (Tony engram_local.py compat)")
    ap.add_argument("--bounds", help="bounds JSON from derive_bounds.py")
    ap.add_argument("--rank", type=int, help="rank to materialize (with --bounds)")
    ap.add_argument("--mbps", type=float, default=None, help="rate limit MB/s (Tony default 600)")
    ap.add_argument("--verify", choices=("full", "random", "none"), default="full",
                    help="full: re-read every copied chunk and compare (spec-grade)")
    ap.add_argument("--random-rows", type=int, default=2000, help="random rows for --verify random")
    ap.add_argument("--hash-source", choices=("none", "full"), default="none",
                    help="full: sha256 the whole source shards (101.5 GB each - hours)")
    ap.add_argument("--keep-dst", action="store_true",
                    help="keep pre-existing dst shards instead of refusing [DIFF vs Tony]")
    args = ap.parse_args(argv)

    if args.specs and args.bounds:
        raise SystemExit("pass either --bounds/--rank or LAYER:START:END, not both")

    specs, provenance = load_specs(args)
    os.makedirs(args.dst, exist_ok=True)
    wmap = json.load(open(f"{args.src}/model.safetensors.index.json"))["weight_map"]

    # ---- validate source geometry before writing anything ------------------
    shards: Dict[str, dict] = {}
    for spec in specs:
        layer = spec["layer"]
        for kind in ("weight", "scale"):
            name = f"layers.{layer}.engram.embed.{kind}"
            if name not in wmap:
                raise SystemExit(f"source index lacks {name}")
            shard = wmap[name]
            if shard not in shards:
                path = os.path.join(args.src, shard)
                prefix, hdr = read_header(path)
                data_end = max(m["data_offsets"][1] for m in hdr.values() if "data_offsets" in m)
                size = os.path.getsize(path)
                if size != prefix + data_end:  # safetensors has no trailer: file ends at data end
                    raise SystemExit(f"{shard}: size {size} != {prefix}+{data_end} (truncated/trailing junk)")
                hfd = os.open(path, os.O_RDONLY)
                try:
                    header_sha = hashlib.sha256(pread_exact(hfd, prefix, 0)).hexdigest()
                finally:
                    os.close(hfd)
                shards[shard] = {
                    "path": path, "prefix": prefix, "header": hdr, "size": size,
                    "header_sha256": header_sha,
                }
            info = shards[shard]
            meta = info["header"].get(name)
            if meta is None:
                raise SystemExit(f"{shard}: header lacks {name}")
            expected_dtype = "F8_E4M3" if kind == "weight" else "F8_E8M0"
            if meta.get("dtype") != expected_dtype:
                raise SystemExit(f"{name}: dtype {meta.get('dtype')} != {expected_dtype}; "
                                 "row-byte math assumes 1-byte elements")  # [DIFF] explicit dtype check
            rows, cols = meta["shape"]
            row_bytes = cols  # F8_E4M3 / F8_E8M0: one byte per element [SAME as Tony]
            spec_key = f"{kind}_row_bytes"
            if spec[spec_key] is not None and spec[spec_key] != row_bytes:
                raise SystemExit(f"{name}: header row bytes {row_bytes} != derived {spec[spec_key]}")
            spec[spec_key] = row_bytes
            if spec["derived_rows"] is not None and rows != spec["derived_rows"]:
                raise SystemExit(f"{name}: header rows {rows} != derived {spec['derived_rows']}")
            spec["derived_rows"] = rows
            if spec["row_end"] > rows:
                raise SystemExit(f"{name}: spec rows [{spec['row_start']}, {spec['row_end']}) exceed table rows {rows}")
            spec.setdefault("shards", {})[kind] = shard
            spec.setdefault("offsets", {})[kind] = info["prefix"] + meta["data_offsets"][0]

    # ---- sparse copy --------------------------------------------------------
    fds = {}
    manifest_ranges: List[Dict[str, Any]] = []
    total_bytes = sum(
        (s["row_end"] - s["row_start"]) * (s["weight_row_bytes"] + s["scale_row_bytes"]) for s in specs
    )
    print(f"copying {total_bytes / 2**30:.1f} GiB of owned rows in {len(specs) * 2} ranges"
          + (f", limit {args.mbps:.0f} MB/s" if args.mbps else ""), flush=True)

    for shard, info in shards.items():
        dst_path = os.path.join(args.dst, shard)
        if os.path.exists(dst_path) and not args.keep_dst:
            os.unlink(dst_path)  # [DIFF] Tony kept a stale file open via O_CREAT
        sfd = os.open(info["path"], os.O_RDONLY)
        dfd = os.open(dst_path, os.O_RDWR | os.O_CREAT, 0o644)
        os.ftruncate(dfd, info["size"])  # sparse: holes cost no disk [SAME as Tony]
        pwrite_all(dfd, pread_exact(sfd, info["prefix"], 0), 0)  # verbatim header [SAME]
        try:
            os.posix_fadvise(sfd, 0, 0, os.POSIX_FADV_SEQUENTIAL)
        except OSError:
            pass
        fds[shard] = (sfd, dfd)

    t0, done, next_report = time.time(), 0, 2 << 30
    for spec in specs:
        layer = spec["layer"]
        for kind in ("weight", "scale"):
            shard = spec["shards"][kind]
            sfd, dfd = fds[shard]
            rb = spec[f"{kind}_row_bytes"]
            base = spec["offsets"][kind]
            off = base + spec["row_start"] * rb
            length = (spec["row_end"] - spec["row_start"]) * rb
            h = hashlib.sha256()
            pos = 0
            while pos < length:
                n = min(CHUNK, length - pos)
                buf = pread_exact(sfd, n, off + pos)
                pwrite_all(dfd, buf, off + pos)
                if args.verify == "full":
                    if pread_exact(dfd, n, off + pos) != buf:  # [DIFF] spec-grade full check
                        raise SystemExit(f"write-back mismatch {shard} {kind} at byte {off + pos}")
                h.update(buf)
                try:
                    os.posix_fadvise(sfd, off + pos, n, os.POSIX_FADV_DONTNEED)  # [SAME] live-service hygiene
                except OSError:
                    pass
                pos += n
                done += n
                if done >= next_report:
                    next_report += 2 << 30
                    print(f"  {done / 2**30:6.1f} / {total_bytes / 2**30:.1f} GiB  "
                          f"{done / max(time.time() - t0, 1e-9) / 1e6:5.0f} MB/s", flush=True)
                if args.mbps:
                    lag = done / (args.mbps * 1e6) - (time.time() - t0)
                    if lag > 0:
                        time.sleep(lag)
            try:
                os.posix_fadvise(dfd, 0, 0, os.POSIX_FADV_DONTNEED)  # [SAME] page-cache hygiene
            except OSError:
                pass
            manifest_ranges.append(
                {
                    "layer": layer,
                    "tensor": f"layers.{layer}.engram.embed.{kind}",
                    "kind": kind,
                    "shard": shard,
                    "row_start": spec["row_start"],
                    "row_end": spec["row_end"],
                    "rows": spec["row_end"] - spec["row_start"],
                    "row_bytes": rb,
                    "file_offset": off,
                    "bytes": length,
                    "sha256": h.hexdigest(),
                }
            )
    # ---- random-row spot check (Tony's check; full check already ran) ------
    bad = 0
    if args.verify == "random":
        rng = random.Random(0)
        for spec in specs:
            for kind in ("weight", "scale"):
                shard = spec["shards"][kind]
                sfd, dfd = fds[shard]
                rb = spec[f"{kind}_row_bytes"]
                base = spec["offsets"][kind]
                rows = [spec["row_start"], spec["row_end"] - 1] + [
                    rng.randrange(spec["row_start"], spec["row_end"]) for _ in range(args.random_rows)
                ]
                for r in rows:
                    o = base + r * rb
                    if pread_exact(sfd, rb, o) != pread_exact(dfd, rb, o):
                        bad += 1
                        print(f"ROW MISMATCH {shard} {kind} row {r}")
        if bad:
            print(f"verify: {bad} row mismatches", flush=True)
            return 1

    for sfd, dfd in fds.values():
        os.fsync(dfd)
        os.close(sfd)
        os.close(dfd)

    # ---- index copy + manifests [SAME as Tony for engram-local.json] -------
    with open(f"{args.src}/model.safetensors.index.json", "rb") as f, \
         open(f"{args.dst}/model.safetensors.index.json", "wb") as g:
        g.write(f.read())
    layers_local = {str(s["layer"]): [s["row_start"], s["row_end"]] for s in specs}
    json.dump(
        {
            "source": args.src,
            "layers": layers_local,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "sparse copy: only these rows exist; runtime/exl3-mia-campaign-20260911/engram/make_store.py "
                    "(successor of tools/engram_local.py)",
        },
        open(f"{args.dst}/engram-local.json", "w"), indent=1,
    )

    source_hashes = {}
    if args.hash_source == "full":
        for name, info in shards.items():
            fd = os.open(info["path"], os.O_RDONLY)
            try:
                source_hashes[name] = sha256_range(fd, 0, info["size"])
            finally:
                os.close(fd)
    manifest: Dict[str, Any] = {
        "schema": "engram-store-manifest/1",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_dir": os.path.abspath(args.src),
        "rank": provenance["rank"] if provenance else None,
        "layer_ranges": layers_local,
        "shards": {
            name: {
                "size": info["size"],
                "header_len": info["prefix"],
                "header_sha256": info["header_sha256"],
                "source_dev": os.stat(info["path"]).st_dev,
                "source_ino": os.stat(info["path"]).st_ino,
                "source_mtime_ns": os.stat(info["path"]).st_mtime_ns,
                "source_sha256": source_hashes.get(name),
            }
            for name, info in shards.items()
        },
        "ranges": manifest_ranges,
        "verify": args.verify,
    }
    if provenance and provenance.get("bounds"):
        b = provenance["bounds"]
        manifest["bounds"] = {
            "schema": b.get("schema"),
            "tp": b.get("tp"),
            "config_sha256": b.get("config", {}).get("sha256"),
            "generated_utc": b.get("generated_utc"),
        }
    json.dump(manifest, open(f"{args.dst}/store-manifest.json", "w"), indent=1)

    allocated = 0
    for shard in shards:
        st = os.stat(os.path.join(args.dst, shard))
        allocated += st.st_blocks * 512
    el = time.time() - t0
    print(f"store ready: {total_bytes / 2**30:.1f} GiB owned bytes, apparent {sum(i['size'] for i in shards.values()) / 2**30:.1f} GiB, "
          f"allocated {allocated / 2**30:.1f} GiB, {el:.0f}s, verify={args.verify}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
