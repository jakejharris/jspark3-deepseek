#!/usr/bin/env python3
"""HTTP range Engram sparse-store materializer (owner follow-up, artifact-only).

Bridges the gap left by the campaign's make_store.py (full local source only):
materializes a per-rank sparse Engram store by fetching ONLY the rank's four
owned byte ranges (2 layers x weight/scale) plus the small safetensors headers
from the selected-revision shard URLs, writing them at ORIGINAL offsets into
sparse files of FULL declared length. Reuses the campaign's proven semantics:
derive_bounds (prime layout, contiguous rank rows), header/trailer validation,
engram-local.json + store-manifest.json shapes (validate_store-compatible).

Hard rules this tool enforces:
  * strict 206 + Content-Range (exact start/end/total) + exact body length;
    a 200 whole-file response is ALWAYS refused, never read
  * no full-file fallback: at most header + owned ranges are ever fetched
  * the eight small model-required projection tensors in shards 47/48
    (q_weight/k_weight/wkv) are NOT fetched; the index is copied verbatim
    with all entries intact (a separate sidecar worker owns projections)
  * <=64 MiB working buffers (chunk <= 32 MiB + one read-back buffer)
  * every fetched chunk is read back from disk and sha256-verified before it
    is journaled; per-chunk receipts enable explicit validated resume
  * stale completed output is refused; a partial store is never silently
    trusted (resume re-verifies every journaled chunk against disk)
  * bounded retries/timeouts; nothing token-bearing or signed is ever
    printed (URLs are sanitized to scheme://host/path)
  * the expected LFS sha256 proves source SELECTION only; a sparse download
    cannot verify whole-file sha256 - recorded as an explicit limitation

Usage (see REPORT.md for schemas):
  range_store.py --config CONFIG.json --index model.safetensors.index.json \
      --shards shards-meta.json --rank N --dst STORE_DIR \
      [--tools-dir DIR] [--tp 3] [--bounds bounds.json] [--chunk-mib 8]
      [--rate-mbps MBPS] [--retries 4] [--timeout 60] [--auth-header-env VAR]
      [--resume] [--probes 32]

Exit codes: 0 ok, 1 validated failure (nothing silently trusted), 2 usage.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_STATE = "range-store-state/1"
SCHEMA_CHUNKS = "range-store-chunks/1"
MAX_CHUNK_BYTES = 32 << 20
SEEK_DATA = getattr(os, "SEEK_DATA", 3)
SEEK_H = getattr(os, "SEEK_H", 4)
DEFAULT_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
HEADER_SANITY_CAP = 1 << 20  # a safetensors header larger than this is absurd

INTEGRITY_LIMITATION = (
    "Sparse HTTP range download: only the fetched byte ranges are verified "
    "(strict 206/Content-Range/size agreement, per-chunk read-back sha256, "
    "per-range receipts, deterministic row probes). The whole-file LFS "
    "sha256 of each source shard is NOT verified because the remaining "
    "bytes are intentionally never fetched; the input expected LFS hash "
    "proves source selection (metadata pin) only."
)


class RangeStoreError(Exception):
    """Validated failure; message is guaranteed sanitized."""


# --------------------------------------------------------------------------
# sanitization: never print token-bearing or signed URLs
# --------------------------------------------------------------------------
def sanitize_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", url)


def sanitize_text(text: str) -> str:
    return re.sub(r"https?://[^\s'\"<>]+", lambda m: sanitize_url(m.group(0)), str(text))


def say(msg: str) -> None:
    print(sanitize_text(msg), flush=True)


def peak_rss_bytes() -> Optional[int]:
    """VmHWM from /proc (Linux); evidence for the bounded-working-set claim."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------
# campaign derive_bounds import (read-only reuse)
# --------------------------------------------------------------------------
def load_derive_bounds(tools_dir: str):
    path = os.path.join(tools_dir, "derive_bounds.py")
    if not os.path.isfile(path):
        raise RangeStoreError(f"derive_bounds.py not found at {path} (--tools-dir)")
    spec = importlib.util.spec_from_file_location("campaign_derive_bounds", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def sha256_bytes(buf: bytes) -> str:
    return hashlib.sha256(buf).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_digest(obj: Any) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode())


# --------------------------------------------------------------------------
# transport: strict ranged GET with clipped continuation + bounded retries
# --------------------------------------------------------------------------
class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Explicit redirect policy (owner review: urllib's default redirect
    handling must not be trusted to strip Authorization). Authorization is
    stripped whenever the redirect changes netloc (host or port); signed CDN
    URLs carry their own credentials in the query. Hops are bounded."""

    max_repeats = 1
    max_redirects = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        old_loc = urllib.parse.urlsplit(req.full_url).netloc.lower()
        new_loc = urllib.parse.urlsplit(newurl).netloc.lower()
        if old_loc != new_loc:
            for k in [k for k in new.headers if k.lower() == "authorization"]:
                del new.headers[k]
        return new


class RangeFetcher:
    def __init__(self, shard: str, url: str, expected_size: int,
                 auth_header: Optional[str], timeout: float, retries: int):
        self.shard, self.url = shard, url
        self.size = expected_size
        self.auth_header = auth_header
        self.timeout, self.retries = timeout, retries
        self.stats = {"requests": 0, "clipped_continuations": 0,
                      "retried_failures": 0, "fetched_bytes": 0}
        self.opener = urllib.request.build_opener(_SafeRedirectHandler)

    def _open(self, start: int, length: int):
        end = start + length - 1
        if end >= self.size:
            raise RangeStoreError(
                f"{self.shard}: range [{start}, {end}] exceeds declared size {self.size}")
        headers = {"Range": f"bytes={start}-{end}",
                   "User-Agent": "range-store/1",
                   "Accept-Encoding": "identity"}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        req = urllib.request.Request(self.url, headers=headers)
        try:
            resp = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            # e.url may be a signed redirect target; never surface it raw
            raise RangeStoreError(
                f"{self.shard}: HTTP {e.code} for {sanitize_url(self.url)} "
                f"(range bytes={start}-{end})") from None
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise RangeStoreError(
                f"{self.shard}: transport error for {sanitize_url(self.url)} "
                f"({type(e).__name__})") from None
        self.stats["requests"] += 1
        code = getattr(resp, "status", None) or resp.getcode()
        if code == 200:
            resp.close()
            raise RangeStoreError(
                f"{self.shard}: server answered 200 (whole file) to a Range "
                f"request; refusing - never accept200")
        if code != 206:
            resp.close()
            raise RangeStoreError(
                f"{self.shard}: expected 206 for range bytes={start}-{end}, got {code}")
        cr = resp.headers.get("Content-Range")
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", cr.strip()) if cr else None
        if not m:
            resp.close()
            raise RangeStoreError(
                f"{self.shard}: malformed Content-Range {cr!r} for range {start}-{end}")
        r_start, r_end, total = (int(x) for x in m.groups())
        if (r_start, r_end, total) != (start, end, self.size):
            resp.close()
            raise RangeStoreError(
                f"{self.shard}: Content-Range bytes {r_start}-{r_end}/{total} does not "
                f"match requested {start}-{end}/{self.size}")
        return resp

    def fetch_bytes(self, start: int, length: int) -> bytes:
        """Small exact fetch (headers, probes). length must be <= 1 MiB."""
        buf = bytearray(length)
        self.fetch_into(start, length, memoryview(buf))
        return bytes(buf)

    def fetch_into(self, start: int, length: int, mv: memoryview) -> None:
        """Fill mv (exactly `length` bytes) from [start, start+length).

        Short/partial bodies continue with strictly validated clipped
        sub-range requests; hard failures retry with bounded backoff. Every
        sub-response is validated exactly like the first.
        """
        got = 0
        failures = 0
        while got < length:
            want = length - got
            try:
                resp = self._open(start + got, want)
            except RangeStoreError:
                failures += 1
                self.stats["retried_failures"] += 1
                if failures > self.retries:
                    raise
                time.sleep(min(8.0, 0.5 * 2 ** failures))
                continue
            try:
                n = 0
                while n < want:
                    r = resp.readinto(mv[got + n: got + want])
                    if not r:
                        break  # clean EOF short of the promised body
                    n += r
                if n == 0:
                    raise RangeStoreError(
                        f"{self.shard}: zero-byte response body at offset {start + got}")
                if n < want:
                    self.stats["clipped_continuations"] += 1
                got += n
                self.stats["fetched_bytes"] += n
                failures = 0
            except (socket.timeout, OSError) as e:
                failures += 1
                self.stats["retried_failures"] += 1
                if failures > self.retries:
                    raise RangeStoreError(
                        f"{self.shard}: transport error mid-body at offset "
                        f"{start + got} ({type(e).__name__}); retries exhausted") from None
                time.sleep(min(8.0, 0.5 * 2 ** failures))
            finally:
                resp.close()


# --------------------------------------------------------------------------
# safetensors header (fetched, strict)
# --------------------------------------------------------------------------
def fetch_header(fetcher: RangeFetcher) -> Tuple[int, Dict[str, Any], bytes]:
    nbuf = fetcher.fetch_bytes(0, 8)
    n = struct.unpack("<Q", nbuf)[0]
    if not (8 <= n <= HEADER_SANITY_CAP):
        raise RangeStoreError(f"{fetcher.shard}: implausible header length {n}")
    hb = fetcher.fetch_bytes(8, n)
    try:
        hdr = json.loads(hb)
    except ValueError as e:
        raise RangeStoreError(f"{fetcher.shard}: header JSON: {e}") from None
    return 8 + n, hdr, nbuf + hb


# --------------------------------------------------------------------------
# sparse extent accounting (same walk as campaign validate_store)
# --------------------------------------------------------------------------
def allocated_extents(fd: int, size: int) -> List[Tuple[int, int]]:
    extents: List[Tuple[int, int]] = []
    pos = 0
    while pos < size:
        try:
            d = os.lseek(fd, pos, SEEK_DATA)
        except OSError:
            break
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


def pwrite_all(fd: int, buf, off: int) -> None:
    """buf: bytes or (writable-agnostic) memoryview; slices never copy."""
    view = memoryview(buf)
    while view:
        n = os.pwrite(fd, view, off)
        if n <= 0:
            raise OSError("short write")
        view = view[n:]
        off += n


def preadv_fill(fd: int, mv: memoryview, off: int) -> None:
    """Read exactly len(mv) bytes at off into the caller's REUSED buffer."""
    got = 0
    while got < len(mv):
        n = os.preadv(fd, [mv[got:]], off + got)
        if n <= 0:
            raise OSError(f"short read at {off + got}")
        got += n


def pread_exact(fd: int, size: int, off: int) -> bytes:
    parts, got = [], 0
    while got < size:
        b = os.pread(fd, size - got, off + got)
        if not b:
            raise OSError(f"short read at {off + got}")
        parts.append(b)
        got += len(b)
    return b"".join(parts)


# --------------------------------------------------------------------------
# main flow
# --------------------------------------------------------------------------
def parse_shards_meta(path: str) -> Dict[str, Dict[str, Any]]:
    meta = json.load(open(path))
    shards = meta.get("shards", meta)
    if not isinstance(shards, dict) or not shards:
        raise RangeStoreError(f"{path}: no shards in metadata")
    out: Dict[str, Dict[str, Any]] = {}
    for name, e in shards.items():
        for k in ("url", "size", "lfs_sha256"):
            if k not in e:
                raise RangeStoreError(f"{path}: shard {name} lacks {k!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(e["lfs_sha256"])):
            raise RangeStoreError(f"{path}: shard {name}: lfs_sha256 is not 64 hex chars")
        if not isinstance(e["size"], int) or e["size"] <= 0:
            raise RangeStoreError(f"{path}: shard {name}: bad size {e['size']!r}")
        if not str(e["url"]).startswith(("http://", "https://")):
            raise RangeStoreError(f"{path}: shard {name}: url must be http(s)")
        out[name] = {"url": str(e["url"]), "size": e["size"], "lfs_sha256": e["lfs_sha256"]}
    return out


def build_plan(bounds: Dict[str, Any], rank: int, wmap: Dict[str, str],
               headers: Dict[str, Tuple[int, Dict[str, Any], int]]) -> List[Dict[str, Any]]:
    """Four owned ranges for this rank, byte-exact from fetched headers."""
    plan: List[Dict[str, Any]] = []
    for layer in bounds["layers"]:
        r = layer["ranks"][rank]
        for kind in ("weight", "scale"):
            name = f"layers.{layer['layer']}.engram.embed.{kind}"
            if name not in wmap:
                raise RangeStoreError(f"index lacks {name}")
            shard = wmap[name]
            if shard not in headers:
                raise RangeStoreError(f"no fetched header for {shard} (needed by {name})")
            prefix, hdr, size = headers[shard]
            meta = hdr.get(name)
            if meta is None:
                raise RangeStoreError(f"{shard}: header lacks {name}")
            expected_dtype = "F8_E4M3" if kind == "weight" else "F8_E8M0"
            if meta.get("dtype") != expected_dtype:
                raise RangeStoreError(f"{name}: dtype {meta.get('dtype')!r} != {expected_dtype}")
            rows, cols = meta["shape"]
            if rows != layer["rows"]:
                raise RangeStoreError(f"{name}: header rows {rows} != derived {layer['rows']}")
            rb = bounds[f"{kind}_row_bytes"]
            if cols != rb:
                raise RangeStoreError(f"{name}: header row bytes {cols} != derived {rb}")
            data_end = max(m["data_offsets"][1] for m in hdr.values() if "data_offsets" in m)
            if size != prefix + data_end:
                raise RangeStoreError(
                    f"{shard}: declared size {size} != {prefix}+{data_end} (trailer mismatch)")
            if r["row_end"] > rows:
                raise RangeStoreError(f"{name}: rank rows [{r['row_start']}, {r['row_end']}) exceed {rows}")
            off = prefix + meta["data_offsets"][0] + r["row_start"] * rb
            length = (r["row_end"] - r["row_start"]) * rb
            plan.append({"shard": shard, "tensor": name, "kind": kind,
                         "layer": layer["layer"], "row_start": r["row_start"],
                         "row_end": r["row_end"], "rows": r["row_end"] - r["row_start"],
                         "row_bytes": rb,
                         "file_offset": off, "bytes": length})
    return plan


def journal_path(dst: str) -> str:
    return os.path.join(dst, "range-store-chunks.jsonl")


def state_path(dst: str) -> str:
    return os.path.join(dst, "range-store-state.json")


def invocation_fingerprint(args, plan: List[Dict[str, Any]], cfg_sha: str, idx_sha: str,
                           shards_meta: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "rank": args.rank,
        "tp": args.tp,
        "config_sha256": cfg_sha,
        "index_sha256": idx_sha,
        "shards": {k: {"url": v["url"], "size": v["size"], "lfs_sha256": v["lfs_sha256"]}
                   for k, v in sorted(shards_meta.items())},
        "plan_digest": canonical_digest(plan),
    }


def check_destination(dst: str, args) -> Optional[Dict[str, Any]]:
    """Refuse stale output. Returns prior state when an explicit validated
    resume is requested and possible; never silently trusts anything."""
    manifest = os.path.join(dst, "store-manifest.json")
    local = os.path.join(dst, "engram-local.json")
    state = os.path.join(dst, "range-store-state.json")
    if os.path.isfile(manifest):
        try:
            m = json.load(open(manifest))
        except ValueError:
            m = {}
        if m.get("status") == "complete":
            raise RangeStoreError(
                f"{dst} already holds a COMPLETED store (rank {m.get('rank')}); refusing "
                "to touch it - validate with the campaign validate_store.py instead")
        raise RangeStoreError(f"{dst} holds an unrecognized store-manifest.json; refusing")
    if os.path.isfile(local) and not os.path.isfile(state):
        raise RangeStoreError(
            f"{dst} holds engram-local.json without range-store state; refusing "
            "(foreign or stale store) - use a new destination")
    if not os.path.isfile(state):
        stray = [f for f in os.listdir(dst) if f.endswith(".safetensors")
                 or f == "range-store-chunks.jsonl"] if os.path.isdir(dst) else []
        if stray:
            raise RangeStoreError(
                f"{dst} holds stale files {sorted(set(stray))} without state; refusing - "
                "use a new destination")
        return None
    st = json.load(open(state))
    if st.get("status") == "complete":
        raise RangeStoreError(
            f"{dst} state says complete but store-manifest.json is missing; refusing")
    if not args.resume:
        raise RangeStoreError(
            f"{dst} holds an INCOMPLETE store (status {st.get('status')!r}); refusing "
            "without --resume - resume is explicit, or use a new destination")
    return st


def replay_journal(dst: str, plan: List[Dict[str, Any]]) -> Dict[Tuple[str, int, int], str]:
    """Verify every journaled chunk against disk. Returns verified chunk map.
    Chunks must tile a contiguous prefix of each planned range starting at the
    range's own file_offset; any mismatch/gap/overlap/corruption fails loudly
    (never silently trust a partial store)."""
    jp = journal_path(dst)
    verified: Dict[Tuple[str, int, int], str] = {}
    if not os.path.isfile(jp):
        return verified
    per_plan: Dict[int, List[Dict[str, Any]]] = {}
    fds: Dict[str, int] = {}
    jbuf = bytearray(0)  # reused, sized to the largest journaled chunk (<=32 MiB)
    try:
        with open(jp) as f:
            for line_no, line in enumerate(f, 1):
                try:
                    c = json.loads(line)
                except ValueError:
                    raise RangeStoreError(f"{jp}:{line_no}: corrupt journal line; refusing")
                key = (c["shard"], c["file_offset"], c["bytes"])
                if key in verified:
                    raise RangeStoreError(f"{jp}:{line_no}: duplicate chunk; refusing")
                if c.get("status") != "verified":
                    raise RangeStoreError(f"{jp}:{line_no}: chunk status {c.get('status')!r}; refusing")
                owner = None
                for i, p in enumerate(plan):
                    if (p["shard"] == c["shard"]
                            and p["file_offset"] <= c["file_offset"]
                            and c["file_offset"] + c["bytes"] <= p["file_offset"] + p["bytes"]):
                        owner = i
                        break
                if owner is None:
                    raise RangeStoreError(
                        f"{jp}:{line_no}: chunk {c['shard']}+{c['file_offset']} outside the "
                        "planned ranges; refusing")
                per_plan.setdefault(owner, []).append(c)
                # [owner-review fix] explicit membership: setdefault would evaluate
                # os.open(...) eagerly and leak one fd per journaled chunk
                if c["shard"] not in fds:
                    fds[c["shard"]] = os.open(os.path.join(dst, c["shard"]), os.O_RDONLY)
                if len(jbuf) < c["bytes"]:
                    jbuf = bytearray(c["bytes"])
                mv = memoryview(jbuf)[: c["bytes"]]
                preadv_fill(fds[c["shard"]], mv, c["file_offset"])
                if hashlib.sha256(mv).hexdigest() != c["sha256"]:
                    raise RangeStoreError(
                        f"{jp}:{line_no}: chunk at {c['shard']}+{c['file_offset']} does not "
                        "match its journaled sha256 (corrupted disk?); refusing - "
                        "a new destination is required")
                verified[key] = c["sha256"]
    finally:
        for fd in fds.values():
            os.close(fd)
    for i, chunks in per_plan.items():
        p = plan[i]
        spans = sorted((c["file_offset"], c["file_offset"] + c["bytes"]) for c in chunks)
        if spans[0][0] != p["file_offset"]:
            raise RangeStoreError(
                f"journaled chunks for {p['tensor']} do not start at the range start; refusing")
        for (a, b), (c, d) in zip(spans, spans[1:]):
            if b != c:
                raise RangeStoreError(
                    f"journaled chunks for {p['tensor']} have a gap/overlap at {b}; refusing")
    return verified


def chunks_cover(plan_entry: Dict[str, Any], verified: Dict[Tuple[str, int, int], str]) -> int:
    """Bytes of this plan range already verified on disk."""
    lo, hi = plan_entry["file_offset"], plan_entry["file_offset"] + plan_entry["bytes"]
    cov = 0
    for (shard, off, size) in verified:
        if shard != plan_entry["shard"]:
            continue
        a, b = off, off + size
        cov += max(0, min(b, hi) - max(a, lo))
    return cov


class Pacer:
    def __init__(self, mbps: Optional[float]):
        self.rate = mbps * 1e6 if mbps else None
        self.t0 = time.time()
        self.sent = 0

    def add(self, n: int) -> None:
        self.sent += n
        if self.rate:
            lag = self.sent / self.rate - (time.time() - self.t0)
            if lag > 0:
                time.sleep(lag)


def materialize(args) -> int:
    tools = load_derive_bounds(args.tools_dir)
    cfg_sha, idx_sha = sha256_file(args.config), sha256_file(args.index)

    # ---- derive via the campaign module (the required path) ---------------
    cfg = tools.load_engram_config(args.config)
    bounds = tools.derive_bounds(cfg, args.tp)
    bounds["generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    bounds["config"] = {"path": os.path.abspath(args.config), "sha256": cfg_sha,
                        "engram": {k: cfg[k] for k in cfg if k.startswith("engram_")}}
    if args.bounds:
        given = json.load(open(args.bounds))
        for layer in bounds["layers"]:
            g = next((l for l in given.get("layers", []) if l.get("layer") == layer["layer"]), None)
            if g is None:
                raise RangeStoreError(f"--bounds lacks layer {layer['layer']}")
            gr = g["ranks"][args.rank]
            dr = layer["ranks"][args.rank]
            if (gr["row_start"], gr["row_end"]) != (dr["row_start"], dr["row_end"]):
                raise RangeStoreError(
                    f"--bounds disagrees with derivation for layer {layer['layer']} rank "
                    f"{args.rank}: {gr['row_start']},{gr['row_end']} vs {dr['row_start']},{dr['row_end']}")

    wmap = json.load(open(args.index))["weight_map"]
    needed = set()
    for layer in bounds["layers"]:
        for kind in ("weight", "scale"):
            name = f"layers.{layer['layer']}.engram.embed.{kind}"
            if name not in wmap:
                raise RangeStoreError(f"index lacks {name}")
            needed.add(wmap[name])
    shards_meta = parse_shards_meta(args.shards)
    missing = needed - set(shards_meta)
    if missing:
        raise RangeStoreError(f"shards metadata lacks entries for {sorted(missing)}")

    os.makedirs(args.dst, exist_ok=True)
    prior = check_destination(args.dst, args)

    auth = os.environ.get(args.auth_header_env) if args.auth_header_env else None
    fetchers = {n: RangeFetcher(n, shards_meta[n]["url"], shards_meta[n]["size"],
                                auth, args.timeout, args.retries) for n in needed}

    # ---- fetch the two small headers (strict) ------------------------------
    headers: Dict[str, Tuple[int, Dict[str, Any], int]] = {}
    header_raw: Dict[str, bytes] = {}
    for name in sorted(needed):
        prefix, hdr, raw = fetch_header(fetchers[name])
        headers[name] = (prefix, hdr, shards_meta[name]["size"])
        header_raw[name] = raw
        say(f"{name}: header ok ({prefix} B prefix)")

    plan = build_plan(bounds, args.rank, wmap, headers)
    total = sum(p["bytes"] for p in plan)
    fingerprint = invocation_fingerprint(args, plan, cfg_sha, idx_sha, shards_meta)
    say(f"rank {args.rank}: {len(plan)} owned ranges, {total} B total")

    verified: Dict[Tuple[str, int, int], str] = {}
    if prior is not None:
        if prior.get("fingerprint") != fingerprint:
            raise RangeStoreError(
                "resume refused: invocation fingerprint differs from the recorded "
                "state (config/index/plan/shards changed) - use a new destination")
        verified = replay_journal(args.dst, plan)
        say(f"resume: {sum(k[2] for k in verified)} B of journaled chunks re-verified on disk")

    state = {"schema": SCHEMA_STATE, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "status": "incomplete", "fingerprint": fingerprint,
             "chunk_mib": args.chunk_mib, "rank": args.rank}
    with open(state_path(args.dst), "w") as f:
        json.dump(state, f, indent=1)
        f.flush()
        os.fsync(f.fileno())

    chunk = args.chunk_mib << 20
    # [owner-review fix] exactly two chunk-sized buffers live for the whole
    # copy (fetch + read-back), reused via memoryviews/preadv; no per-chunk
    # allocations. Peak working set = 2 * chunk <= 64 MiB.
    fetch_buf = bytearray(chunk)
    readback_buf = bytearray(chunk)
    pacer = Pacer(args.rate_mbps)
    jf = open(journal_path(args.dst), "a")
    fds: Dict[str, int] = {}
    t0 = time.time()
    done = 0
    try:
        for p in plan:
            shard = p["shard"]
            if shard not in fds:
                path = os.path.join(args.dst, shard)
                if not os.path.exists(path):
                    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
                    os.ftruncate(fd, headers[shard][2])  # full sparse length
                else:
                    fd = os.open(path, os.O_RDWR)
                    if os.fstat(fd).st_size != headers[shard][2]:
                        raise RangeStoreError(
                            f"{shard}: existing file size {os.fstat(fd).st_size} != declared "
                            f"{headers[shard][2]}; refusing")
                fds[shard] = fd
            fetcher = fetchers[shard]
            prefix, hdr, size = headers[shard]
            # verbatim header at offset 0 (tiny, from the cached fetch, idempotent)
            hbuf = header_raw[shard]
            pwrite_all(fds[shard], hbuf, 0)
            if pread_exact(fds[shard], len(hbuf), 0) != hbuf:
                raise RangeStoreError(f"{shard}: header write-back mismatch")
            off, remaining = p["file_offset"], p["bytes"]
            h = hashlib.sha256()
            skipped = chunks_cover(p, verified)
            if skipped:
                # already-fetched prefix of this range: re-read from disk (into the
                # REUSED read-back buffer) to seed the range hash
                fd = fds[shard]
                pos, left = p["file_offset"], skipped
                while left:
                    n = min(chunk, left)
                    rmv = memoryview(readback_buf)[:n]
                    preadv_fill(fd, rmv, pos)
                    h.update(rmv)
                    pos += n
                    left -= n
                off, remaining = p["file_offset"] + skipped, p["bytes"] - skipped
            while remaining:
                n = min(chunk, remaining)
                fmv = memoryview(fetch_buf)[:n]      # slices of the reused buffers
                fetcher.fetch_into(off, n, fmv)
                pwrite_all(fds[shard], fmv, off)
                rmv = memoryview(readback_buf)[:n]
                preadv_fill(fds[shard], rmv, off)    # read-back into the reused buffer
                if fmv != rmv:                        # memoryview content equality
                    raise RangeStoreError(
                        f"{shard}: chunk write-back mismatch at {off}; NOT journaled")
                h.update(fmv)
                rec = {"schema": SCHEMA_CHUNKS, "shard": shard, "tensor": p["tensor"],
                       "kind": p["kind"], "file_offset": off, "bytes": n,
                       "sha256": hashlib.sha256(fmv).hexdigest(), "status": "verified",
                       "attempts": fetcher.stats["retried_failures"]}
                jf.write(json.dumps(rec, sort_keys=True) + "\n")
                jf.flush()
                os.fsync(jf.fileno())
                verified[(shard, off, n)] = rec["sha256"]
                off += n
                remaining -= n
                done += n
                pacer.add(n)
                say(f"  {p['tensor']}: {p['bytes'] - remaining}/{p['bytes']} B "
                    f"({done / max(time.time() - t0, 1e-9) / 1e6:.0f} MB/s)")
            # finalize per-range receipt
            p["sha256"] = h.hexdigest()
        for fd in fds.values():
            os.fsync(fd)
    finally:
        jf.close()
        for fd in fds.values():
            os.close(fd)

    # ---- deterministic probes (disk evidence; no local source to compare) --
    rng = random.Random(0)
    probes = []
    for p in plan:
        rows = sorted({p["row_start"], p["row_start"] + 1, p["row_end"] - 2,
                       p["row_end"] - 1}
                      | {rng.randrange(p["row_start"], p["row_end"]) for _ in range(args.probes)})
        fd = os.open(os.path.join(args.dst, p["shard"]), os.O_RDONLY)
        try:
            base = p["file_offset"] - p["row_start"] * p["row_bytes"]
            for r in rows:
                o = base + r * p["row_bytes"]
                b = pread_exact(fd, p["row_bytes"], o)
                probes.append({"tensor": p["tensor"], "row": r, "file_offset": o,
                               "bytes": p["row_bytes"], "sha256": sha256_bytes(b)})
        finally:
            os.close(fd)

    # ---- index copy + loader-compatible engram-local.json -----------------
    with open(args.index, "rb") as f, open(os.path.join(args.dst, "model.safetensors.index.json"), "wb") as g:
        g.write(f.read())
    layers_local = {str(p["layer"]): [p["row_start"], p["row_end"]] for p in plan}
    json.dump({"source": "http-range", "layers": layers_local,
               "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "note": "sparse http-range copy; range_store.py; projections owned by sidecar"},
              open(os.path.join(args.dst, "engram-local.json"), "w"), indent=1)

    # ---- manifest (validate_store-compatible + transport evidence) --------
    peak = peak_rss_bytes()
    shard_manifest: Dict[str, Any] = {}
    for name in sorted(needed):
        prefix, hdr, size = headers[name]
        path = os.path.join(args.dst, name)
        st = os.stat(path)
        fd = os.open(path, os.O_RDONLY)
        try:
            extents = allocated_extents(fd, st.st_size)
        finally:
            os.close(fd)
        shard_manifest[name] = {
            "size": size, "header_len": prefix,
            "header_sha256": sha256_bytes(json.dumps(hdr, sort_keys=True).encode()),
            "url": sanitize_url(shards_meta[name]["url"]),
            "lfs_sha256_expected": shards_meta[name]["lfs_sha256"],
            "source_sha256": None,
            "sparse": {"apparent_bytes": st.st_size,
                       "allocated_bytes_st_blocks": st.st_blocks * 512,
                       "allocated_bytes_extents": sum(b - a for a, b in extents),
                       "extents": [list(e) for e in extents]},
            "transport": dict(fetchers[name].stats),
        }
    manifest = {
        "schema": "engram-store-manifest/1",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "complete",
        "source": "http-range",
        "rank": args.rank,
        "layer_ranges": layers_local,
        "shards": shard_manifest,
        "ranges": [{k: p[k] for k in ("layer", "tensor", "kind", "shard", "row_start",
                                      "row_end", "rows", "row_bytes", "file_offset",
                                      "bytes", "sha256")} for p in plan],
        "probes": {"seed": 0, "count": args.probes, "rows": probes},
        "verify": "full-readback+chunk-journal",
        "bounds": {"schema": bounds.get("schema"), "tp": bounds.get("tp"),
                   "config_sha256": cfg_sha, "generated_utc": bounds.get("generated_utc")},
        "peak_rss_bytes": peak,
        "working_buffers_bytes": 2 * chunk,
        "integrity_limitation": INTEGRITY_LIMITATION,
    }
    json.dump(manifest, open(os.path.join(args.dst, "store-manifest.json"), "w"), indent=1)
    state["status"] = "complete"
    json.dump(state, open(state_path(args.dst), "w"), indent=1)
    peak_txt = f", peak RSS {peak / 2**20:.0f} MiB (working buffers {2 * chunk / 2**20:.0f} MiB)" if peak else ""
    say(f"store complete: rank {args.rank}, {total} B owned bytes, "
        f"{time.time() - t0:.0f}s; probes {len(probes)}{peak_txt}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--shards", required=True, help="shards-meta JSON (see REPORT.md)")
    ap.add_argument("--rank", type=int, required=True, choices=(0, 1, 2))
    ap.add_argument("--dst", required=True)
    ap.add_argument("--tools-dir", default=DEFAULT_TOOLS_DIR,
                    help="campaign engram dir holding derive_bounds.py (read-only)")
    ap.add_argument("--tp", type=int, default=3)
    ap.add_argument("--bounds", help="optional bounds JSON cross-checked against derivation")
    ap.add_argument("--chunk-mib", type=int, default=8)
    ap.add_argument("--rate-mbps", type=float, default=None)
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--auth-header-env",
                    help="env var whose value is sent verbatim as Authorization (never printed)")
    ap.add_argument("--resume", action="store_true",
                    help="explicitly resume an interrupted store after chunk re-verification")
    ap.add_argument("--probes", type=int, default=32)
    args = ap.parse_args(argv)
    if not (1 <= args.chunk_mib <= MAX_CHUNK_BYTES >> 20):
        print("range_store: --chunk-mib must be within [1, 32] (<=64MiB working buffers)",
              file=sys.stderr)
        return 2
    try:
        return materialize(args)
    except RangeStoreError as e:
        print(f"range_store: error: {sanitize_text(str(e))}", file=sys.stderr)
        print("range_store: partial evidence retained (state + chunk journal); "
              "resume explicitly with --resume or use a new destination", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
