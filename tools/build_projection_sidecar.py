#!/usr/bin/env python3
"""Build engram-projections.safetensors: the 8 MODEL-REQUIRED Engram projection
tensors extracted from EXL3 shards 47/48 at the selected HF revision.

Why (weights/INDEX-COMPAT-REPORT.md follow-up + owner verdict 2026-09-12):
shards 47/48 hold, per Engram layer (1 -> shard 47, 14 -> shard 48), exactly six
index keys: the two giant hash tables (``embed.weight``/``embed.scale``,
disk-resolved by ``DiskEngramTable`` and deliberately excluded from model-tp3)
plus FOUR projection tensors the model loads through the normal path
(``k_weight``, ``q_weight``, ``wkv.weight``, ``wkv.scale``;
patch/engram.py:1182-1194).  The owner chose the sidecar plan change: extract
those eight small tensors (~157.5 MB/layer) into one new safetensors file and
point the filtered index at it (``filter_index.py --projections-sidecar``).

This tool is stdlib-only and bounded-memory.  Source bytes come either from
VERIFIED HTTP byte ranges against the pinned revision (never a whole-file
response) or, explicitly opt-in, from local shard files with identical
structural validation (for tests / a release-stub comparison -- never an
automatic substitute; provenance is recorded).

Hard rules:
  * every HTTP range response must be 206 with the exact
    ``Content-Range: bytes S-E/<pinned total>`` and matching Content-Length;
    HTTP 200 (whole file), other codes, malformed ranges, compressed bodies,
    short reads and overlong reads are all refused;
  * pinned revision/URLs/sizes/sha256 are built in (and cross-checked against
    WEIGHTS-METADATA.json when --metadata is given);
  * shard headers must contain EXACTLY the six expected keys for their layer
    (no missing, no unexpected); entries must be non-overlapping, in bounds,
    internally consistent (bytes == prod(shape) * dtype size) and must match
    the pinned dtype/shape spec (unless --allow-shape-drift, which is
    receipted loudly; --spec-override replaces the table for tests/revisions);
  * output is a brand-new inode: staged to a temp file, fsynced, atomically
    published; pre-existing output/receipt paths are refused;
  * every fetched range and every tensor sha256 is receipted, plus the whole
    output sha256 (streamed re-read); credentials never appear in any output
    (explicit scrub guard).

The original index and the source shards are never modified.

Usage (HTTP, on the staging host, token via environment):
  HF_TOKEN=... python3 build_projection_sidecar.py --http \
      --metadata WEIGHTS-METADATA.json \
      --output engram-projections.safetensors \
      --receipt receipts/engram-projections.json --commit

Local mode (identical validation, explicit provenance):
  python3 build_projection_sidecar.py \
      --local-shard-47 /path/model-00047-of-00048.safetensors \
      --local-shard-48 /path/model-00048-of-00048.safetensors \
      --output ... --receipt ... [--spec-override spec.json] --commit

Exit codes: 0 ok, 1 environmental error, 2 validation refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

TOOL = "build_projection_sidecar.py"
TOOL_VERSION = 1

# --- pinned provenance (frozen; cross-checked against WEIGHTS-METADATA) ----
REPO = "bot-lab-21/DeepSeek-V4.1-Flash-EXL3-3.5bpw-Pollard"
REVISION = "b60193e0609147553145d1538d935925f2763c1d"
DEFAULT_BASE_URL = "https://huggingface.co"
DEFAULT_TOKEN_ENV = "HF_TOKEN"

SHARD_NAME = {47: "model-00047-of-00048.safetensors",
              48: "model-00048-of-00048.safetensors"}
LAYER_OF_SHARD = {47: 1, 48: 14}
PINNED = {
    47: {"bytes": 101535150936,
         "sha256": "824db4881320407ac340736d14dcee5ecd748c27d0f5836b8127ecc2e3781b0f"},
    48: {"bytes": 101537926640,
         "sha256": "976330f4954338e1ad8b508c32aa912032c7ad908959fd53c8307650fe4520ed"},
}
# Observed live by the owner range probe (evidence/OWNER-HF-RANGE-PROBE.json,
# 2026-09-12T04:41Z, anonymous, both shards PASSED).  The FINAL CDN strong
# ETags are Xet hashes -- they are NOT the LFS sha256 above and must never be
# compared against it (the resolve-stage X-Linked-Etag is the LFS-side one).
# Policy: pin these observed source-CDN ETags, require every fetched range of
# a shard to carry the same ETag, and truthfully record that no whole-file
# LFS sha256 verification happened.
PINNED_CDN_ETAG = {
    47: "ea90b8dc0768541091d10697efbe84ca12b41888b40171d588844a1c5e6254fb",
    48: "8cdbb8a278f02c91c8f0ad861b0d0ca92e58367be441140bc3497eb7d8833e35",
}
# Header geometry pins from the same probe (shard bytes are accounted to the
# byte: header + the six tensors == the pinned file size exactly).
PINNED_HEADER = {
    47: {"bytes": 664, "sha256": "9055d06658af74f431f6675f6880f187b24a3f648b3f70ae949edc328b7702fc"},
    48: {"bytes": 672, "sha256": "ffc9da89096525ea191d967531f8063db66327b0880a9d2f4d7d73a42d641f28"},
}

# --- expected tensor spec, derived from the selected revision's config ------
# text_config: hidden_size=5120, hc_mult=4, engram_max_ngram_size=4,
# engram_n_heads=8, engram_head_dim=256 -> n_hash_cols=24, wkv 24*256 -> 5120*5
# with FP8 dynamic weights + ue8m0 32x32 block scales; k/q are bf16 params
# (patch/engram.py:1182-1194).  Shard assignment per the owner-verified index.
BUILTIN_SPEC = {}
for _shard, _layer in LAYER_OF_SHARD.items():
    for _kind, _dt, _shape in (
            ("k_weight", "BF16", [4, 5120]),
            ("q_weight", "BF16", [4, 5120]),
            ("wkv.weight", "F8_E4M3", [25600, 6144]),
            ("wkv.scale", "F8_E8M0", [800, 192])):   # dtype per the owner probe
        BUILTIN_SPEC["layers.%d.engram.%s" % (_layer, _kind)] = {
            "dtype": _dt, "shape": list(_shape), "shard": _shard}

EMBED_KEYS = ("embed.weight", "embed.scale")

DTYPE_SIZES = {"BF16": 2, "F16": 2, "F32": 4, "F64": 8, "I8": 1, "U8": 1,
               "I16": 2, "U16": 2, "I32": 4, "U32": 4, "I64": 8, "U64": 8,
               "F8_E4M3": 1, "F8_E5M2": 1, "F8_E8M0": 1, "BOOL": 1}

MAX_HEADER_BYTES = 64 * 1024 * 1024
MAX_SINGLE_FETCH = 512 * 1024 * 1024
DEFAULT_CHUNK = 8 * 1024 * 1024
OUTPUT_METADATA = {"format": "pt",
                   "producer": TOOL + " v%d" % TOOL_VERSION,
                   "source_repo": REPO,
                   "source_revision": REVISION}


class Refusal(Exception):
    """Validation refusal: refuse to produce output from this input."""


# ---------------------------------------------------------------------------
# Range fetch primitives
# ---------------------------------------------------------------------------
def urllib_open(url, headers, timeout):
    """Transport seam: opens the request (monkeypatched in tests)."""
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=timeout)


def http_open_range(url, start, end, pinned_total, token, timeout):
    """Open a verified 206 range response for [start, end] (inclusive).

    Never returns a whole-file/other response: every deviation from the exact
    expected Content-Range (including the pinned total, which validates the
    source selection) is a Refusal.
    """
    if end - start + 1 > MAX_SINGLE_FETCH:
        raise Refusal("range %d-%d exceeds the single-fetch guardrail (%d bytes)"
                      % (start, end, MAX_SINGLE_FETCH))
    headers = {"Range": "bytes=%d-%d" % (start, end),
               "Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        response = urllib_open(url, headers, timeout)
    except urllib.error.HTTPError as exc:
        raise Refusal("HTTP %d for range %d-%d (body withheld; credentials "
                      "never included in errors)" % (exc.code, start, end))
    except OSError as exc:
        raise Refusal("network error for range %d-%d: %s" % (start, end, exc))
    try:
        code = response.getcode()
        if code != 206:
            raise Refusal("HTTP %d for range %d-%d: only exact 206 range "
                          "responses are accepted (whole-file 200 or any "
                          "other status is refused)" % (code, start, end))
        expected = "bytes %d-%d/%d" % (start, end, pinned_total)
        actual = response.headers.get("Content-Range")
        if actual != expected:
            raise Refusal("Content-Range %r != expected %r" % (actual, expected))
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != end - start + 1:
            raise Refusal("Content-Length %r != requested %d bytes"
                          % (length, end - start + 1))
        encoding = response.headers.get("Content-Encoding")
        if encoding and encoding.lower() != "identity":
            raise Refusal("compressed response (%r) would corrupt range "
                          "semantics" % encoding)
        return response
    except Exception:
        response.close()
        raise


class HttpShardSource:
    """Verified-range HTTP source for one pinned shard."""

    def __init__(self, shard, base_url, token, timeout, chunk):
        self.shard = shard
        self.url = "%s/%s/resolve/%s/%s" % (base_url.rstrip("/"), REPO,
                                            REVISION, SHARD_NAME[shard])
        self.pinned = PINNED[shard]
        self.token = token
        self.timeout = timeout
        self.chunk = chunk
        self.etag = None
        self.etag_kind = None
        self.etag_matches_pinned = None
        self.etag_responses = 0

    def _check_etag(self, headers):
        """Owner policy: pin the observed source-CDN (Xet) ETag; require the
        SAME ETag on every fetched range of this shard; never compare the
        final ETag to the LFS sha256 (different hash families; the
        resolve-stage X-Linked-Etag is the LFS-side one)."""
        etag = headers.get("ETag")
        self.etag_responses += 1
        if etag is None:
            if self.etag is not None:
                raise Refusal("shard %d: ETag disappeared after being present "
                              "on earlier ranges" % self.shard)
            self.etag_kind = "absent"
            return
        if self.etag is None:
            self.etag = etag
            clean = etag.strip().removeprefix("W/").strip('"')
            if len(clean) == 64 and all(c in "0123456789abcdef" for c in clean):
                self.etag_kind = "strong_hex"
                if clean != PINNED_CDN_ETAG[self.shard]:
                    raise Refusal(
                        "shard %d: strong CDN ETag %s does not match the pinned "
                        "source-CDN (Xet) ETag %s.  The final CDN ETag is a "
                        "Xet hash and is NEVER compared to the LFS sha256; a "
                        "mismatch means the served content changed since the "
                        "owner range probe"
                        % (self.shard, clean, PINNED_CDN_ETAG[self.shard]))
                self.etag_matches_pinned = True
            else:
                self.etag_kind = "other"
                self.etag_matches_pinned = None
        elif etag != self.etag:
            raise Refusal("shard %d: ETag changed across fetched ranges "
                          "(%r -> %r); refusing mid-run content switch"
                          % (self.shard, self.etag, etag))

    def fetch(self, start, end):
        """Yield exactly end-start+1 bytes; refuse short or overlong reads."""
        response = http_open_range(self.url, start, end,
                                   self.pinned["bytes"], self.token,
                                   self.timeout)
        self._check_etag(response.headers)
        remaining = end - start + 1
        try:
            while remaining:
                data = response.read(min(self.chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
            if remaining:
                raise Refusal("short read for range %d-%d: %d of %d bytes"
                              % (start, end, end - start + 1 - remaining,
                                 end - start + 1))
            if response.read(1):
                raise Refusal("server sent more bytes than the requested "
                              "range %d-%d" % (start, end))
        finally:
            response.close()

    def read_header(self):
        prefix = b"".join(self.fetch(0, 7))
        if len(prefix) != 8:
            raise Refusal("shard %d: 8-byte header length prefix not delivered"
                          % self.shard)
        (n,) = struct.unpack("<Q", prefix)
        if not 0 < n <= MAX_HEADER_BYTES:
            raise Refusal("shard %d: implausible header length %d"
                          % (self.shard, n))
        if 8 + n > self.pinned["bytes"]:
            raise Refusal("shard %d: header exceeds the pinned file size"
                          % self.shard)
        body = b"".join(self.fetch(8, 8 + n - 1))
        if len(body) != n:
            raise Refusal("shard %d: header body short read" % self.shard)
        header_all = prefix + body
        pin = PINNED_HEADER[self.shard]
        if 8 + n != pin["bytes"] or \
                hashlib.sha256(header_all).hexdigest() != pin["sha256"]:
            raise Refusal(
                "shard %d: header geometry does not match the owner probe pin "
                "(got %d bytes / sha256 %s, pinned %d bytes / %s)"
                % (self.shard, 8 + n, hashlib.sha256(header_all).hexdigest(),
                   pin["bytes"], pin["sha256"]))
        return header_all, 8 + n, body

    def provenance(self):
        return {"mode": "http", "shard": self.shard, "url": self.url,
                "pinned_bytes": self.pinned["bytes"],
                "pinned_lfs_sha256": self.pinned["sha256"],
                "whole_file_lfs_sha256_verified": False,
                "etag": self.etag, "etag_kind": self.etag_kind,
                "pinned_cdn_etag": PINNED_CDN_ETAG[self.shard],
                "etag_matches_pinned_cdn_etag": self.etag_matches_pinned,
                "etag_stable_across_ranges": True,  # any instability refused
                "etag_responses": self.etag_responses,
                "etag_note": "final CDN ETag is a Xet hash; never compared "
                             "to the LFS sha256 (X-Linked-Etag is LFS-side)",
                "header_pin": PINNED_HEADER[self.shard]}


class LocalShardSource:
    """Local-file source with identical structural validation.

    The pinned-size cross-check is recorded, not enforced: a release stub
    (~157.5 MB) or a full local copy are both admissible EXPLICIT inputs, and
    the receipt says which one it was (pinned_size_match false/true).  This is
    never an automatic substitute for the HTTP path.
    """

    def __init__(self, shard, path, chunk):
        self.shard = shard
        self.path = os.path.realpath(path)
        if not os.path.isfile(self.path):
            raise Refusal("local shard %d: %r is not a file" % (self.shard, path))
        self.fd = os.open(self.path, os.O_RDONLY)
        self.size = os.fstat(self.fd).st_size
        self.chunk = chunk
        self.pinned = PINNED[shard]

    def fetch(self, start, end):
        if not 0 <= start <= end < self.size:
            raise Refusal("local shard %d: range %d-%d out of file bounds "
                          "[0, %d)" % (self.shard, start, end, self.size))
        remaining = end - start + 1
        offset = start
        while remaining:
            data = os.pread(self.fd, min(self.chunk, remaining), offset)
            if not data:
                raise Refusal("local shard %d: short read at %d"
                              % (self.shard, offset))
            remaining -= len(data)
            offset += len(data)
            yield data

    def read_header(self):
        prefix = os.pread(self.fd, 8, 0)
        if len(prefix) != 8:
            raise Refusal("local shard %d: cannot read 8-byte header prefix"
                          % self.shard)
        (n,) = struct.unpack("<Q", prefix)
        if not 0 < n <= MAX_HEADER_BYTES:
            raise Refusal("local shard %d: implausible header length %d"
                          % (self.shard, n))
        if 8 + n > self.size:
            raise Refusal("local shard %d: header exceeds file size" % self.shard)
        body = os.pread(self.fd, n, 8)
        if len(body) != n:
            raise Refusal("local shard %d: header short read" % self.shard)
        return prefix + body, 8 + n, body

    def provenance(self, header_all, header_len):
        pin = PINNED_HEADER[self.shard]
        return {"mode": "local", "shard": self.shard, "path": self.path,
                "actual_bytes": self.size,
                "pinned_bytes": self.pinned["bytes"],
                "pinned_size_match": self.size == self.pinned["bytes"],
                "pinned_lfs_sha256": self.pinned["sha256"],
                "full_file_sha256_verified": False,
                "header_bytes": header_len,
                "header_sha256": hashlib.sha256(header_all).hexdigest(),
                "header_pin_match": (header_len == pin["bytes"] and
                                     hashlib.sha256(header_all).hexdigest()
                                     == pin["sha256"]),
                "header_pin_enforced": False}

    def close(self):
        os.close(self.fd)


# ---------------------------------------------------------------------------
# Header validation
# ---------------------------------------------------------------------------
def expected_keys(shard):
    layer = LAYER_OF_SHARD[shard]
    names = ["layers.%d.engram.%s" % (layer, kind) for kind in EMBED_KEYS]
    names += [name for name in BUILTIN_SPEC if name.startswith("layers.%d." % layer)]
    return names


def validate_shard_header(shard, header_bytes, header_len, file_size, spec,
                          allow_drift):
    """Validate one shard header; return per-tensor records (all six)."""
    layer = LAYER_OF_SHARD[shard]
    try:
        entries = json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise Refusal("shard %d: header is not valid JSON: %s" % (shard, exc))
    if not isinstance(entries, dict) or not entries:
        raise Refusal("shard %d: header is not an object" % shard)

    expected = set(expected_keys(shard))
    actual = set(entries.keys())
    missing, unexpected = sorted(expected - actual), sorted(actual - expected)
    if missing or unexpected:
        raise Refusal(
            "shard %d header key set mismatch (must be EXACTLY the six "
            "layer-%d Engram keys): missing=%s unexpected=%s"
            % (shard, layer, missing, unexpected))

    spans, records, drifts = [], [], []
    for name in sorted(entries):
        meta = entries[name]
        if not isinstance(meta, dict):
            raise Refusal("shard %d: entry %r is not an object" % (shard, name))
        dtype, shape = meta.get("dtype"), meta.get("shape")
        offsets = meta.get("data_offsets")
        if dtype not in DTYPE_SIZES:
            raise Refusal("shard %d: %r has unknown dtype %r"
                          % (shard, name, dtype))
        if not isinstance(shape, list) or not all(
                isinstance(d, int) and d >= 0 for d in shape):
            raise Refusal("shard %d: %r has bad shape %r" % (shard, name, shape))
        if not (isinstance(offsets, list) and len(offsets) == 2
                and all(isinstance(o, int) for o in offsets)
                and 0 <= offsets[0] < offsets[1]):
            raise Refusal("shard %d: %r has bad data_offsets %r"
                          % (shard, name, offsets))
        nbytes = offsets[1] - offsets[0]
        if nbytes != product(shape) * DTYPE_SIZES[dtype]:
            raise Refusal("shard %d: %r span %d != prod(shape)*dtype_size %d"
                          % (shard, name, nbytes,
                             product(shape) * DTYPE_SIZES[dtype]))
        if offsets[1] > file_size - header_len:
            raise Refusal("shard %d: %r data_offsets [%d, %d) exceed the data "
                          "region (file %d, header %d)"
                          % (shard, name, offsets[0], offsets[1], file_size,
                             header_len))
        spans.append((offsets[0], offsets[1], name))
        if name in spec:
            want = spec[name]
            if dtype != want["dtype"] or list(shape) != list(want["shape"]):
                message = ("shard %d: %r is %s %s, pinned spec expects %s %s"
                           % (shard, name, dtype, shape, want["dtype"],
                              want["shape"]))
                if not allow_drift:
                    raise Refusal(message + " (pass --allow-shape-drift to "
                                          "record and proceed)")
                drifts.append(message)
        records.append({"name": name, "shard": shard, "dtype": dtype,
                        "shape": list(shape),
                        "source_offset_start": offsets[0],
                        "source_offset_end": offsets[1],
                        "bytes": nbytes})
    spans.sort()
    for (s1, e1, n1), (s2, e2, n2) in zip(spans, spans[1:]):
        if s2 < e1:
            raise Refusal("shard %d: tensors %r and %r overlap in the data "
                          "region" % (shard, n1, n2))
    for record in records:
        if record["name"] in spec:
            record["in_output"] = record["name"] in spec  # projection -> copy
        else:
            record["in_output"] = False                   # embed table -> skip
    return records, drifts


def product(values):
    result = 1
    for value in values:
        result *= int(value)
    return result


# ---------------------------------------------------------------------------
# Output serialization
# ---------------------------------------------------------------------------
def serialize_header(projection_records):
    """Deterministic safetensors header; returns (padded_bytes, data_total)."""
    entries = {"__metadata__": dict(OUTPUT_METADATA)}
    offset = 0
    for record in sorted(projection_records, key=lambda r: r["name"]):
        entries[record["name"]] = {
            "dtype": record["dtype"], "shape": record["shape"],
            "data_offsets": [offset, offset + record["bytes"]]}
        offset += record["bytes"]
    text = json.dumps(entries, separators=(",", ":"))
    padding = (-(8 + len(text))) % 8
    padded = text + " " * padding
    return padded.encode("ascii"), offset


def sha256_file(path, chunk):
    digest = hashlib.sha256()
    with open(path, "rb", buffering=0) as handle:
        while True:
            data = handle.read(chunk)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def load_spec_override(path):
    with open(path, "r", encoding="utf-8") as handle:
        table = json.load(handle)
    if not isinstance(table, dict):
        raise Refusal("spec override must be an object")
    spec = {}
    for name, meta in table.items():
        if meta["dtype"] not in DTYPE_SIZES:
            raise Refusal("spec override: %r has unknown dtype" % name)
        if name.endswith((".engram.embed.weight", ".engram.embed.scale")):
            raise Refusal("spec override must not include the Engram tables "
                          "(%r): they are never copied into the sidecar" % name)
        spec[name] = {"dtype": meta["dtype"], "shape": list(meta["shape"])}
    return spec


def crosscheck_metadata(path):
    with open(path, "r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    rows = {row.get("path"): row for row in metadata.get("weights", [])
            if isinstance(row, dict)}
    for shard, pinned in PINNED.items():
        row = rows.get(SHARD_NAME[shard])
        if row is None:
            raise Refusal("metadata does not cover %s" % SHARD_NAME[shard])
        if row.get("bytes") != pinned["bytes"] or \
                row.get("sha256") != pinned["sha256"]:
            raise Refusal("metadata disagrees with the pinned %s identity"
                          % SHARD_NAME[shard])
    return True


def scrub(text, token):
    if token and token in text:
        raise Refusal("internal guard: credential would appear in output")
    for marker in ("Bearer ", "Authorization"):
        if marker in text:
            raise Refusal("internal guard: auth marker %r in output" % marker)
    return text


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build(sources, spec, allow_drift, chunk, output_path, receipt_path, commit,
          metadata_path):
    if metadata_path:
        crosscheck_metadata(metadata_path)
    if os.path.exists(output_path):
        raise Refusal("output path %r already exists (never overwrite)"
                      % output_path)
    if os.path.exists(receipt_path):
        raise Refusal("receipt path %r already exists (preserve prior receipts)"
                      % receipt_path)
    for source in sources.values():
        if isinstance(source, LocalShardSource) and \
                os.path.realpath(output_path) == source.path:
            raise Refusal("output path must not be a source shard")

    shard_info, projection_records, drifts = {}, [], []
    for shard, source in sorted(sources.items()):
        header_all, header_len, header_body = source.read_header()
        records, shard_drifts = validate_shard_header(
            shard, header_body, header_len,
            source.pinned["bytes"] if isinstance(source, HttpShardSource)
            else source.size, spec, allow_drift)
        drifts.extend(shard_drifts)
        if isinstance(source, LocalShardSource):
            provenance = source.provenance(header_all, header_len)
        else:
            provenance = source.provenance()
        shard_info[str(shard)] = {
            "provenance": provenance,
            "header_bytes": header_len,
            "header_sha256": hashlib.sha256(header_all).hexdigest(),
            "tensors": {r["name"]: r for r in records}}
        projection_records += [r for r in records if r["in_output"]]

    if len(projection_records) != len(spec):
        raise Refusal("expected %d projection tensors, validated %d"
                      % (len(spec), len(projection_records)))
    header_bytes, data_total = serialize_header(projection_records)
    output_bytes = 8 + len(header_bytes) + data_total

    plan = {
        "tool": TOOL, "tool_version": TOOL_VERSION,
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": "http" if isinstance(next(iter(sources.values())),
                                     HttpShardSource) else "local",
        "revision": REVISION,
        "projection_count": len(projection_records),
        "output_bytes_planned": output_bytes,
        "header_bytes_planned": 8 + len(header_bytes),
        "data_bytes_planned": data_total,
        "tensors": sorted(projection_records, key=lambda r: r["name"]),
        "shards": shard_info,
        "drifts": drifts,
        "metadata_crosscheck": bool(metadata_path),
    }
    print(json.dumps({k: v for k, v in plan.items()
                      if k not in ("shards", "tensors")}, indent=2))
    if not commit:
        print("dry-run: nothing written (pass --commit to extract)", file=sys.stderr)
        return 0

    directory = os.path.dirname(os.path.abspath(output_path)) or "."
    fd, temp_path = tempfile.mkstemp(dir=directory,
                                     prefix=".engram-projections-", suffix=".tmp")
    tensor_hashes = []
    try:
        with os.fdopen(fd, "wb", buffering=0) as handle:
            handle.write(struct.pack("<Q", len(header_bytes)))
            handle.write(header_bytes)
            cursor = 0
            for record in sorted(projection_records,
                                 key=lambda r: r["name"]):
                source = sources[record["shard"]]
                start = shard_info[str(record["shard"])]["header_bytes"] \
                    + record["source_offset_start"]
                end = shard_info[str(record["shard"])]["header_bytes"] \
                    + record["source_offset_end"] - 1
                digest = hashlib.sha256()
                got = 0
                for chunk_bytes in source.fetch(start, end):
                    handle.write(chunk_bytes)
                    digest.update(chunk_bytes)
                    got += len(chunk_bytes)
                if got != record["bytes"]:
                    raise Refusal("short stream for %r: %d of %d bytes"
                                  % (record["name"], got, record["bytes"]))
                record["output_offset"] = cursor
                record["source_file_range"] = [start, end + 1]
                record["sha256"] = digest.hexdigest()
                tensor_hashes.append(record["name"])
                cursor += got
            if cursor != data_total:
                raise Refusal("data written %d != planned %d" % (cursor,
                                                                 data_total))
            os.fsync(handle.fileno())
        if os.path.getsize(temp_path) != output_bytes:
            raise Refusal("temp output size %d != planned %d"
                          % (os.path.getsize(temp_path), output_bytes))
        output_sha = sha256_file(temp_path, chunk)   # streamed re-read hash
        os.replace(temp_path, output_path)
    except BaseException:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    stat = os.stat(output_path)
    receipt = {
        "tool": TOOL, "tool_version": TOOL_VERSION,
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": plan["mode"], "revision": REVISION,
        "output": {"path": os.path.abspath(output_path),
                   "bytes": output_bytes, "sha256": output_sha,
                   "header_bytes": 8 + len(header_bytes),
                   "data_bytes": data_total,
                   "inode": stat.st_ino, "device": stat.st_dev},
        "tensors": sorted(projection_records, key=lambda r: r["name"]),
        "shards": shard_info,
        "drifts": drifts,
        "metadata_crosscheck": bool(metadata_path),
        "note": "original index and source shards untouched; per-tensor "
                "sha256 values are the owner's cross-check against a second "
                "fetch or the release-stub comparison",
    }
    receipt_text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    scrub(receipt_text, _active_token)
    rfd, rtemp = tempfile.mkstemp(dir=os.path.dirname(
        os.path.abspath(receipt_path)) or ".", prefix=".receipt-", suffix=".tmp")
    with os.fdopen(rfd, "w", encoding="utf-8") as handle:
        handle.write(receipt_text)
        os.fsync(handle.fileno())
    os.replace(rtemp, receipt_path)
    print("wrote %s (%d bytes, sha256 %s) and %s"
          % (output_path, output_bytes, output_sha, receipt_path),
          file=sys.stderr)
    return 0


_active_token = None


def main(argv=None):
    global _active_token
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--http", action="store_true",
                        help="fetch verified byte ranges from the pinned "
                             "HF revision (token from --token-env required "
                             "unless --allow-anonymous)")
    source.add_argument("--local-shard-47", metavar="PATH",
                        help=argparse.SUPPRESS)
    parser.add_argument("--local-shard-48", metavar="PATH",
                        help="local mode: path to shard 48 (both shards "
                             "required for local mode)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--allow-anonymous", action="store_true")
    parser.add_argument("--metadata", help="WEIGHTS-METADATA.json to "
                                           "cross-check the pinned identity")
    parser.add_argument("--spec-override", metavar="PATH",
                        help="JSON {name: {dtype, shape}} replacing the "
                             "built-in pinned spec (tests / future revisions)")
    parser.add_argument("--allow-shape-drift", action="store_true")
    parser.add_argument("--chunk-mib", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)

    def refuse(message):
        print("REFUSED: %s" % message, file=sys.stderr)
        return 2

    try:
        if not 1 <= args.chunk_mib <= 256:
            return refuse("--chunk-mib out of range 1..256")
        chunk = args.chunk_mib * 1024 * 1024
        spec = BUILTIN_SPEC if not args.spec_override \
            else load_spec_override(args.spec_override)
        sources = {}
        if args.http:
            token = os.environ.get(args.token_env) or None
            if not token and not args.allow_anonymous:
                return refuse("http mode needs a token in $%s (or explicit "
                              "--allow-anonymous)" % args.token_env)
            _active_token = token
            for shard in (47, 48):
                sources[shard] = HttpShardSource(shard, args.base_url, token,
                                                 args.timeout, chunk)
        else:
            if not (args.local_shard_47 and args.local_shard_48):
                return refuse("local mode requires both --local-shard-47 and "
                              "--local-shard-48")
            for shard, path in ((47, args.local_shard_47),
                                (48, args.local_shard_48)):
                sources[shard] = LocalShardSource(shard, path, chunk)
        try:
            return build(sources, spec, args.allow_shape_drift, chunk,
                         args.output, args.receipt, args.commit, args.metadata)
        finally:
            for source in sources.values():
                if isinstance(source, LocalShardSource):
                    source.close()
    except Refusal as exc:
        return refuse(str(exc))
    except OSError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
