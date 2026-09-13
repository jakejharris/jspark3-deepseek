#!/usr/bin/env python3
"""Derive per-rank Engram table row bounds for DeepSeek-V4.1-Flash EXL3 TP3.

Pure stdlib. Mirrors, line for line, the layout + sharding math of the pinned
vLLM sources (commit e47aa780bccf59f59dfa2cbb18e17a10b4fe69ba,
``vllm/models/deepseek_v4_1/common/engram.py``, byte-identical in Tony's
mounted patch ``patch/exl3-tp3/engram.py`` at recipe pin c2c1bb7):

* ``EngramLayout.__init__``  - prime bucket sizes per (layer, n-gram, head)
* ``find_next_prime``        - deterministic prime draw, shared `seen` set
* ``ParallelEngramEmbedding.__init__`` - contiguous hash-column ownership:
  part = ceil(n_hash_cols / tp); rank r owns columns [r*part, (r+1)*part);
  row range = [cumulative_size[head_start], cumulative_size[head_end])
* ``DiskEngramTable``        - disk reads are based at those same row offsets

Usage:
  derive_bounds.py --config CONFIG.json [--tp 3] [--emit bounds.json]
                   [--index model.safetensors.index.json]
                   [--tensor-manifest tensor-manifest.json] [--explain]

With --index (and optionally --tensor-manifest for byte geometry without
local shards) the output also carries shard placement, file byte offsets and
per-rank owned byte ranges. Without them, output carries the logical row
bounds plus config-derived row byte sizes (weight = head_dim bytes/row,
scale = head_dim/quant_block bytes/row, both 1-byte dtypes).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "engram-bounds/1"

# Byte width of the safetensors dtypes used by the Engram tables.
DTYPE_BYTES = {"F8_E4M3": 1, "F8_E8M0": 1, "BF16": 2, "F16": 2, "F32": 4}
# Loader-side quant block for the Engram ue8m0 scales (Engram.__init__ passes
# block_size=32 to ParallelEngramEmbedding; scale row = head_dim // 32).
QUANT_BLOCK = 32


# --------------------------------------------------------------------------
# Prime machinery - exact port of vllm/models/deepseek_v4_1/common/engram.py
# --------------------------------------------------------------------------
def _is_prime(n: int) -> bool:
    """Deterministic Miller-Rabin for n < 2**32 (same bases as the loader)."""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in (2, 7, 61):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def find_next_prime(start: int, seen: set) -> int:
    """The smallest prime above `start` that has not been handed out yet."""
    candidate = start + 1
    while not _is_prime(candidate) or candidate in seen:
        candidate += 1
    return candidate


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_engram_config(path: str) -> Dict[str, Any]:
    cfg = json.load(open(path))
    if "text_config" in cfg and "engram_layer_ids" in cfg.get("text_config", {}):
        cfg = cfg["text_config"]
    required = (
        "engram_layer_ids",
        "engram_num_embeddings",
        "engram_max_ngram_size",
        "engram_vocab_size",
        "engram_n_heads",
        "engram_head_dim",
    )
    missing = [k for k in required if k not in cfg]
    if missing:
        raise SystemExit(f"config {path} lacks Engram keys: {missing}")
    if len(cfg["engram_layer_ids"]) != len(cfg["engram_num_embeddings"]):
        raise SystemExit("engram_layer_ids / engram_num_embeddings length mismatch")
    return cfg


# --------------------------------------------------------------------------
# Layout derivation (EngramLayout.__init__)
# --------------------------------------------------------------------------
def engram_primes(cfg: Dict[str, Any]) -> List[List[int]]:
    """Flatten per-layer prime bucket sizes in column order.

    Exact loop structure of EngramLayout.__init__: `current` resets to
    engram_vocab_size - 1 for every n-gram size, but `seen` is never cleared,
    so the whole model draws one consecutive prime sequence.
    """
    seen: set = set()
    layers: List[List[int]] = []
    for _ in cfg["engram_layer_ids"]:
        flat: List[int] = []
        for _ in range(cfg["engram_max_ngram_size"] - 1):
            current = cfg["engram_vocab_size"] - 1
            for _ in range(cfg["engram_n_heads"]):
                current = find_next_prime(current, seen)
                seen.add(current)
                flat.append(current)
        layers.append(flat)
    return layers


def derive_bounds(cfg: Dict[str, Any], tp: int) -> Dict[str, Any]:
    """Row bounds per (layer, rank), mirroring ParallelEngramEmbedding."""
    if tp < 1:
        raise SystemExit("--tp must be >= 1")
    n_hash_cols = (cfg["engram_max_ngram_size"] - 1) * cfg["engram_n_heads"]
    part = math.ceil(n_hash_cols / tp)  # triton.cdiv in the loader
    primes = engram_primes(cfg)
    head_dim = cfg["engram_head_dim"]
    layers = []
    for layer_id, layer_primes, declared in zip(
        cfg["engram_layer_ids"], primes, cfg["engram_num_embeddings"]
    ):
        if sum(layer_primes) > declared:
            raise SystemExit(
                f"layer {layer_id}: prime layout sums to {sum(layer_primes)} rows, "
                f"config declares only {declared} (engram_num_embeddings) - layout/config mismatch"
            )
        cum = [0]
        for p in layer_primes:
            cum.append(cum[-1] + p)
        ranks = []
        for rank in range(tp):
            head_start = min(rank * part, n_hash_cols)  # python slice clamp
            head_end = min(head_start + part, n_hash_cols)
            row_start, row_end = cum[head_start], cum[head_end]
            ranks.append(
                {
                    "rank": rank,
                    "head_columns": [head_start, head_end],
                    "row_start": row_start,
                    "row_end": row_end,
                    "rows": row_end - row_start,
                    "weight_bytes": (row_end - row_start) * head_dim,
                    "scale_bytes": (row_end - row_start) * (head_dim // QUANT_BLOCK),
                }
            )
        layers.append(
            {
                "layer": layer_id,
                "rows": cum[n_hash_cols],
                "num_embeddings_declared": declared,
                "num_embeddings_matches_layout": cum[n_hash_cols] == declared,
                "primes": layer_primes,
                "cumulative_rows": cum,
                "ranks": ranks,
            }
        )
    return {
        "schema": SCHEMA,
        "tp": tp,
        "n_hash_cols": n_hash_cols,
        "columns_per_rank": part,
        "even_column_split": n_hash_cols % tp == 0,
        "head_dim": head_dim,
        "quant_block": QUANT_BLOCK,
        "weight_row_bytes": head_dim,
        "scale_row_bytes": head_dim // QUANT_BLOCK,
        "layers": layers,
    }


# --------------------------------------------------------------------------
# Byte geometry from the checkpoint index / tensor manifest
# --------------------------------------------------------------------------
def tensor_geometry(layer_id: int, index_path: str, manifest_path: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Map layers.{L}.engram.embed.{weight,scale} to shard + byte layout.

    Prefers shard-file headers if the files sit next to the index (exact);
    otherwise falls back to the campaign's frozen tensor-manifest.json
    (header audit of revision 2bc89ac; EXL3 shards 47/48 are metadata-
    identical, sha256 824db488../976330f4.. at both revisions).
    """
    idx = json.load(open(index_path))
    wmap = idx["weight_map"] if "weight_map" in idx else idx
    out = {}
    for kind in ("weight", "scale"):
        name = f"layers.{layer_id}.engram.embed.{kind}"
        if name not in wmap:
            raise SystemExit(f"index has no entry for {name}")
        shard = wmap[name]
        entry: Dict[str, Any] = {"name": name, "shard": shard}
        header_path = os.path.join(os.path.dirname(index_path), shard)
        if os.path.exists(header_path):
            with open(header_path, "rb") as f:
                n = struct.unpack("<Q", f.read(8))[0]
                hdr = json.loads(f.read(n))
            meta = hdr[name]
            entry.update(
                source="shard-header",
                dtype=meta.get("dtype"),
                shape=meta["shape"],
                data_offsets=meta["data_offsets"],
                file_prefix=8 + n,
                file_size=os.path.getsize(header_path),
                row_bytes=meta["shape"][1] * DTYPE_BYTES.get(meta.get("dtype", "F8_E4M3"), 1),
            )
        elif manifest_path:
            man = json.load(open(manifest_path))
            meta = man[name]
            entry.update(
                source="tensor-manifest",
                dtype=meta["dtype"],
                shape=meta["shape"],
                data_offsets=meta["data_offsets"],
                file_prefix=None,  # header length only in the shard/audit receipt
                row_bytes=meta["shape"][1] * DTYPE_BYTES.get(meta["dtype"], 1),
            )
        else:
            entry.update(source="none", row_bytes=None)
        out[kind] = entry
    return out


def attach_geometry(bounds: Dict[str, Any], cfg: Dict[str, Any],
                    index_path: Optional[str], manifest_path: Optional[str]) -> Dict[str, Any]:
    if not index_path:
        return bounds
    bounds["index"] = os.path.abspath(index_path)
    for layer in bounds["layers"]:
        geo = tensor_geometry(layer["layer"], index_path, manifest_path)
        weight, scale = geo["weight"], geo["scale"]
        for e in (weight, scale):
            if e.get("shape") and e["shape"][0] != layer["rows"]:
                raise SystemExit(
                    f"{e['name']}: shard/manifest rows {e['shape'][0]} != derived rows {layer['rows']}"
                )
        if weight.get("row_bytes") and weight["row_bytes"] != bounds["weight_row_bytes"]:
            raise SystemExit(f"weight row bytes {weight['row_bytes']} != config-derived {bounds['weight_row_bytes']}")
        if scale.get("row_bytes") and scale["row_bytes"] != bounds["scale_row_bytes"]:
            raise SystemExit(f"scale row bytes {scale['row_bytes']} != config-derived {bounds['scale_row_bytes']}")
        layer["tensors"] = geo
        for rank in layer["ranks"]:
            br = {}
            for kind in ("weight", "scale"):
                e = geo[kind]
                if e.get("data_offsets") is not None and e.get("row_bytes"):
                    base = e["data_offsets"][0]
                    rb = e["row_bytes"]
                    prefix = e.get("file_prefix", 0) or 0
                    br[kind] = {
                        "file_offset_start": prefix + base + rank["row_start"] * rb,
                        "file_offset_end": prefix + base + rank["row_end"] * rb,
                        "bytes": (rank["row_end"] - rank["row_start"]) * rb,
                    }
                else:
                    br[kind] = {"bytes": rank[f"{kind}_bytes"]}
            rank["byte_ranges"] = br
            rank["owned_bytes"] = sum(v.get("bytes", 0) for v in br.values())
    per_rank = [sum(l["ranks"][r].get("owned_bytes", 0) for l in bounds["layers"]) for r in range(bounds["tp"])]
    bounds["totals"] = {"owned_bytes_per_rank": per_rank}
    return bounds


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Explain / CLI
# --------------------------------------------------------------------------
def explain(bounds: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    L = []
    A = L.append
    A(f"Engram layout derivation (schema {SCHEMA})")
    A(f"  engram_layer_ids        = {cfg['engram_layer_ids']}")
    A(f"  engram_num_embeddings   = {cfg['engram_num_embeddings']}")
    A(f"  engram_max_ngram_size   = {cfg['engram_max_ngram_size']}")
    A(f"  engram_vocab_size       = {cfg['engram_vocab_size']}  (prime search starts above V-1)")
    A(f"  engram_n_heads          = {cfg['engram_n_heads']}")
    A(f"  engram_head_dim         = {cfg['engram_head_dim']}  -> weight row {cfg['engram_head_dim']} B, "
      f"scale row {cfg['engram_head_dim'] // QUANT_BLOCK} B (block {QUANT_BLOCK})")
    A(f"  n_hash_cols = (max_ngram-1)*n_heads = {bounds['n_hash_cols']}; "
      f"tp = {bounds['tp']}; columns/rank = ceil = {bounds['columns_per_rank']}"
      f"{' (even)' if bounds['even_column_split'] else ' (UNEVEN - last ranks own fewer columns, loader clamps)'}")
    A("")
    A("Prime draw order: for each layer, for each n-gram size 2..max, current resets to")
    A("V-1 but the seen-set is global, so the model draws ONE consecutive prime sequence;")
    A("column order within a layer is [2-gram heads 0..n-1, 3-gram heads, 4-gram heads].")
    A("Rank r owns columns [r*part, (r+1)*part) (ParallelEngramEmbedding.head_start),")
    A("i.e. global rows [cum[head_start], cum[head_end]) of that layer's table.")
    A("")
    for layer in bounds["layers"]:
        A(f"layer {layer['layer']}: {layer['rows']} rows "
          f"(config declares {layer['num_embeddings_declared']}, "
          f"{'match' if layer['num_embeddings_matches_layout'] else 'MISMATCH'})")
        A(f"  primes: {layer['primes']}")
        for r in layer["ranks"]:
            br = r.get("byte_ranges", {})
            w = br.get("weight", {})
            s = br.get("scale", {})
            extra = ""
            if "file_offset_start" in w:
                extra = (f"; weight file bytes [{w['file_offset_start']}, {w['file_offset_end']})"
                         f", scale [{s['file_offset_start']}, {s['file_offset_end']})")
            A(f"  rank {r['rank']}: columns {r['head_columns'][0]}..{r['head_columns'][1] - 1}, "
              f"rows [{r['row_start']}, {r['row_end']}) = {r['rows']} rows, "
              f"{r['weight_bytes'] + r['scale_bytes']} bytes{extra}")
    if "totals" in bounds:
        A("")
        A(f"owned Engram bytes per rank: {bounds['totals']['owned_bytes_per_rank']}")
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True, help="model config.json (text_config auto-detected)")
    ap.add_argument("--tp", type=int, default=3)
    ap.add_argument("--index", help="model.safetensors.index.json for shard mapping")
    ap.add_argument("--tensor-manifest", help="frozen tensor-manifest.json for byte geometry without local shards")
    ap.add_argument("--emit", help="write bounds JSON here (default: stdout)")
    ap.add_argument("--explain", action="store_true", help="print the human-readable derivation")
    args = ap.parse_args(argv)

    cfg = load_engram_config(args.config)
    bounds = derive_bounds(cfg, args.tp)
    bounds["generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    bounds["config"] = {
        "path": os.path.abspath(args.config),
        "sha256": sha256_file(args.config),
        "engram": {k: cfg[k] for k in cfg if k.startswith("engram_")},
    }
    bounds = attach_geometry(bounds, cfg, args.index, args.tensor_manifest)

    if args.explain:
        print(explain(bounds, cfg))
    out = json.dumps(bounds, indent=1)
    if args.emit:
        with open(args.emit, "w") as f:
            f.write(out + "\n")
        print(f"wrote {args.emit}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
