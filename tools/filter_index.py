#!/usr/bin/env python3
"""Derive the EXL3 46-shard inference index from the selected-revision index.

Why this exists (see INDEX-COMPAT-REPORT.md in this directory): the patched
vLLM weight_utils.py mounted into the pinned image
(``filter_duplicate_safetensors_files``) requires every safetensors file named
by ``model.safetensors.index.json`` to exist on disk, and raises
``FileNotFoundError`` otherwise -- before the Engram tensor-name skip in
``safetensors_weights_iterator`` ever runs.  A 46-shard model directory
(shards 47/48 deliberately absent, ~203 GB of skipped Engram tables) therefore
needs a derived index that no longer names shards 47/48.

Key-set taxonomy (from the pinned source, INDEX-COMPAT-REPORT follow-up
section; engram layers are 1 and 14 per the selected config):

  * ``layers.{1,14}.engram.embed.{weight,scale}`` -- the giant hash tables.
    The ONLY tensors that are disk-resolved: ``DiskEngramTable`` builds
    exactly these two lookups per layer (patch/engram.py:733-734) and the
    iterator skip matches exactly these suffixes (patch/weight_utils.py:971);
    under DSV41_ENGRAM_DISK=1 they are 1-row placeholder buffers
    (patch/engram.py:878-881).  REMOVED by the rewrite rule.
  * ``layers.{1,14}.engram.{k_weight,q_weight,wkv.weight,wkv.scale}`` -- the
    Engram module's own projections (patch/engram.py:1182,1190,1194): ordinary
    model parameters ALWAYS loaded through the normal path (~157.5 MB/layer;
    they are what is left of shard 47/48 once the tables are accounted for).
    MODEL-REQUIRED.  Cannot simply be removed; either keep a source for them
    or use --projections-sidecar (below).
  * ``layers.N.ffn.experts.E.w{1,2,3}.{trellis,suh,svh,mcg,mul1}`` -- EXL3
    expert payload consumed by the normal loader stream
    (patch/exl3-tp3/exl3_moe.py:303-381, exl3_config.py:144-150).  MODEL
    REQUIRED.  If any of these point at 47/48 the staging plan is broken;
    this tool refuses loudly (they belong in shards 3-42, which the frozen
    sizes prove they cannot leave: suh+svh alone are ~684 MB > the ~157.5 MB
    non-table remainder of shard 48).

Rewrite rule (weights-staging-plan.md section 5, updated by the 2026-09-12
follow-up):
  * validate the original index first (fail closed on anything unexpected);
  * classify EVERY entry pointing at shards 47/48;
  * remove ONLY the validated embed-table entries (the disk-resolved set);
  * with --projections-sidecar NAME --sidecar-bytes N: redirect the validated
    Engram projection entries to a small sidecar safetensors file (extracted
    from a permitted full source during Engram staging) instead of dropping
    them; without the flag their presence is a LOUD refusal -- the staging
    plan must change before a 46-shard index can be derived;
  * ALWAYS refuse if any expert/unknown entry points at 47/48;
  * recompute ``metadata.total_size`` over the retained files (46 shards,
    plus the sidecar bytes when redirecting), preserving other metadata keys;
  * write canonically (deterministic: sorted weight_map keys, fixed layout);
  * record a receipt with original/derived SHA256, the classification counts,
    the exact removed key/value set and the redirect map.

The ORIGINAL index is never modified; the derived file is a new path chosen by
the operator.  Nothing is written unless --commit is passed.

Usage:
  python3 filter_index.py --index model.safetensors.index.json \
      --sizes WEIGHTS-METADATA.json \
      --output model-tp3/model.safetensors.index.json \
      [--projections-sidecar engram-projections.safetensors --sidecar-bytes N]
      --receipt receipts/index-filter.json --commit

Exit codes: 0 ok, 1 environmental error, 2 validation refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

TOOL = "filter_index.py"
TOOL_VERSION = 2
INDEX_NAME = "model.safetensors.index.json"
TOTAL_SHARDS = 48
EXCLUDED_SHARDS = (47, 48)
ENGRAM_LAYERS = (1, 14)
SHARD_RE = re.compile(r"^model-(\d{5})-of-%05d\.safetensors$" % TOTAL_SHARDS)

# --- key taxonomy (bare names; optional "model." prefix tolerated) ---------
_ENGRAM_TABLE_RE = re.compile(
    r"^(?:model\.)?layers\.(\d+)\.engram\.embed\.(weight|scale)$")
_ENGRAM_PROJECTION_RE = re.compile(
    r"^(?:model\.)?layers\.(\d+)\.engram\.(k_weight|q_weight|wkv\.weight|wkv\.scale)$")
_EXL3_EXPERT_RE = re.compile(
    r"^(?:model\.)?layers\.(\d+)\.ffn\.experts\.(\d+)\.w[123]\.(trellis|suh|svh|mcg|mul1)$")

DISK_RESOLVED = "DISK_RESOLVED_ENGRAM_TABLE"
PROJECTION = "MODEL_REQUIRED_ENGRAM_PROJECTION"
EXPERT = "MODEL_REQUIRED_EXL3_EXPERT"
UNKNOWN = "UNKNOWN"

EXPECTED_TABLE_KEYS = tuple(
    "layers.%d.engram.embed.%s" % (layer, kind)
    for layer in ENGRAM_LAYERS for kind in ("weight", "scale"))
EXPECTED_PROJECTION_KEYS = tuple(
    "layers.%d.engram.%s" % (layer, kind)
    for layer in ENGRAM_LAYERS
    for kind in ("k_weight", "q_weight", "wkv.weight", "wkv.scale"))

SIDECAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.safetensors$")
FORBIDDEN_COMPONENTS = ()


class Refusal(Exception):
    """Validation refusal: deriving an index from this input is not safe."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb", buffering=0) as stream:
        while True:
            chunk = stream.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def shard_number(filename):
    match = SHARD_RE.match(filename)
    if not match:
        raise Refusal("value %r is not canonical shard syntax %s"
                      % (filename, SHARD_RE.pattern))
    number = int(match.group(1))
    if not 1 <= number <= TOTAL_SHARDS:
        raise Refusal("shard number %d out of range 1..%d" % (number, TOTAL_SHARDS))
    return number


def classify(key):
    """Classify a weight_map key against the pinned-source taxonomy.

    Returns (kind, layer or None).  Engram classes are only valid for layers
    1 and 14 (the selected config's engram_layer_ids); other layers degrade
    to UNKNOWN so they are refused rather than silently reclassified.
    """
    match = _ENGRAM_TABLE_RE.match(key)
    if match:
        layer = int(match.group(1))
        return (DISK_RESOLVED if layer in ENGRAM_LAYERS else UNKNOWN), layer
    match = _ENGRAM_PROJECTION_RE.match(key)
    if match:
        layer = int(match.group(1))
        return (PROJECTION if layer in ENGRAM_LAYERS else UNKNOWN), layer
    if _EXL3_EXPERT_RE.match(key):
        return EXPERT, int(_EXL3_EXPERT_RE.match(key).group(1))
    return UNKNOWN, None


def load_sizes(path):
    """Load {filename: bytes} from a WEIGHTS-METADATA.json-style file."""
    with open(path, "r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    rows = metadata.get("weights")
    if not isinstance(rows, list):
        raise Refusal("%s: no weights[] list" % path)
    sizes = {}
    for row in rows:
        name, size = row.get("path"), row.get("bytes")
        if not isinstance(name, str) or not isinstance(size, int) or size < 0:
            raise Refusal("%s: bad weights row %r" % (path, row))
        sizes[name] = size
    return sizes


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise Refusal("duplicate JSON key %r in index file" % key)
        seen[key] = value
    return seen


def validate_index(index_path):
    """Parse and validate the original index.

    Returns (doc, excluded) where excluded is an ordered {key: shard} dict of
    every entry pointing at shards 47/48, each key classified by ``classify``.

    Fail-closed rules:
      1. unique weight keys, canonical shard syntax for every value;
      2. every entry pointing at 47/48 is classified and layer-valid;
      3. all four expected embed-table keys exist and point at 47/48;
      4. embed-table keys never live outside 47/48.
    """
    with open(index_path, "rb") as handle:
        raw = handle.read()
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except Refusal:
        raise
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        raise Refusal("index is not valid UTF-8 JSON: %s" % exc)

    if not isinstance(doc, dict) or not isinstance(doc.get("weight_map"), dict):
        raise Refusal("index must be an object with a weight_map object")
    metadata = doc.get("metadata", {})
    if not isinstance(metadata, dict):
        raise Refusal("metadata must be an object")

    weight_map = doc["weight_map"]
    excluded, kept_shards, table_keys, projections, experts, unknowns = \
        {}, set(), {}, {}, {}, []
    for key, value in weight_map.items():
        number = shard_number(value)
        if number in EXCLUDED_SHARDS:
            kind, layer = classify(key)
            excluded[key] = value
            if kind == DISK_RESOLVED:
                table_keys[key] = value
            elif kind == PROJECTION:
                projections[key] = value
            elif kind == EXPERT:
                experts[key] = value
            else:
                unknowns.append(key)
        else:
            kept_shards.add(value)
            kind, _layer = classify(key)
            if kind == DISK_RESOLVED:
                raise Refusal(
                    "embed-table key %r points at shard %d; the Engram tables "
                    "live only in shards %s" % (key, number, list(EXCLUDED_SHARDS)))

    for expected in EXPECTED_TABLE_KEYS:
        if expected not in table_keys:
            raise Refusal(
                "expected embed-table key %r not found among 47/48 entries "
                "(found: %s)" % (expected, sorted(table_keys)))
    if not kept_shards:
        raise Refusal("index retains no shard 1-46 entries")

    classification = {
        "excluded_total": len(excluded),
        DISK_RESOLVED: len(table_keys),
        PROJECTION: len(projections),
        EXPERT: len(experts),
        UNKNOWN: len(unknowns),
        "unknown_keys": sorted(unknowns)[:20],
        "expert_keys_sample": sorted(experts)[:5],
    }
    return doc, excluded, table_keys, projections, experts, unknowns, kept_shards


def guard_sidecar(name):
    if not SIDECAR_RE.match(name) or SHARD_RE.match(name) \
            or any(part in FORBIDDEN_COMPONENTS for part in name.split("/")):
        raise Refusal(
            "sidecar name %r must be a bare *.safetensors filename that does "
            "not impersonate a model-NNNNN-of-00048 shard" % name)
    return name


def derive(doc, table_keys, projections, experts, unknowns, kept_shards, sizes,
           sidecar=None, sidecar_bytes=0):
    """Build the derived document and size accounting."""
    missing = sorted(name for name in kept_shards if name not in sizes)
    if missing:
        raise Refusal(
            "no byte size known for %d retained shard(s): %s%s"
            % (len(missing), ", ".join(missing[:5]),
               " ..." if len(missing) > 5 else ""))
    bad = sorted(name for name in kept_shards if sizes[name] <= 0)
    if bad:
        raise Refusal("non-positive size for retained shard(s): %s" % ", ".join(bad))

    # Loud refusals first: the staging plan does not authorize dropping
    # model-required payload that lives in the excluded shards.
    if experts:
        raise Refusal(
            "%d EXL3 expert entries point at shards %s (e.g. %s): MODEL-REQUIRED "
            "payload (exl3_moe.py loads trellis/suh/svh via the normal path). "
            "The frozen sizes leave only ~157.5 MB/table-shard after the Engram "
            "tables, so this contradicts WEIGHTS-METADATA.json -- re-run this "
            "tool on the real index and reconcile before staging."
            % (len(experts), list(EXCLUDED_SHARDS), sorted(experts)[:3]))
    if unknowns:
        raise Refusal(
            "%d unclassifiable entries point at shards %s: %s"
            % (len(unknowns), list(EXCLUDED_SHARDS), sorted(unknowns)[:5]))
    if projections and sidecar is None:
        raise Refusal(
            "%d MODEL-REQUIRED Engram projection entries point at shards %s "
            "(k_weight/q_weight/wkv.*; engram.py:1182-1194 loads them through "
            "the normal path). Removing them would leave those parameters "
            "uninitialized. The staging plan must change first: pass "
            "--projections-sidecar (a small extracted safetensors file) once a "
            "permitted source for those bytes exists."
            % (len(projections), list(EXCLUDED_SHARDS)))
    if not projections and sidecar is not None:
        raise Refusal("--projections-sidecar given but no projection entries "
                      "point at shards %s" % list(EXCLUDED_SHARDS))

    removed = dict(table_keys)
    redirect = dict(projections) if sidecar else {}
    new_map = {}
    for key, value in sorted(doc["weight_map"].items()):
        if key in removed:
            continue
        new_map[key] = sidecar if (sidecar and key in redirect) else value
    new_doc = {"metadata": dict(doc.get("metadata", {})),
               "weight_map": new_map}
    new_doc["metadata"]["total_size"] = sum(sizes[name] for name in kept_shards) \
        + (sidecar_bytes if sidecar else 0)
    removed_sizes = [sizes[v] for v in removed.values() if v in sizes]
    removed_bytes = (sum(removed_sizes)
                     if len(removed_sizes) == len(removed) else None)
    return new_doc, removed, redirect, removed_bytes


def atomic_write(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temp = tempfile.mkstemp(dir=directory, prefix=".filter-index-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--index", required=True, help="original %s" % INDEX_NAME)
    parser.add_argument("--sizes",
                        help="WEIGHTS-METADATA.json-style file mapping every shard path to bytes")
    parser.add_argument("--model-dir",
                        help="optional 46-shard directory: retained shard sizes are read from "
                             "the files, and the presence of shards 47/48 is refused")
    parser.add_argument("--projections-sidecar",
                        help="opt-in plan change: redirect the Engram projection entries to "
                             "this small sidecar safetensors file instead of dropping them")
    parser.add_argument("--sidecar-bytes", type=int,
                        help="byte size of the sidecar file (required with --projections-sidecar)")
    parser.add_argument("--output", help="derived index destination (new path)")
    parser.add_argument("--receipt", help="receipt JSON destination (new path)")
    parser.add_argument("--commit", action="store_true",
                        help="actually write output/receipt (default: validate and report only)")
    args = parser.parse_args(argv)

    def refuse(message):
        print("REFUSED: %s" % message, file=sys.stderr)
        return 2

    for path, label in ((args.index, "index"), (args.sizes, "sizes")):
        if path and any(part in FORBIDDEN_COMPONENTS for part in path.split(os.sep)):
            return refuse("%s path %r touches forbidden component %s"
                          % (label, path, FORBIDDEN_COMPONENTS))
        if path and not os.path.isfile(path):
            return refuse("%s path %r is not a file" % (label, path))
    if args.projections_sidecar is not None and args.sidecar_bytes is None:
        return refuse("--projections-sidecar requires --sidecar-bytes")

    try:
        (doc, excluded, table_keys, projections, experts, unknowns,
         kept_shards) = validate_index(args.index)
        sidecar = None
        if args.projections_sidecar is not None:
            sidecar = guard_sidecar(args.projections_sidecar)
        sizes = load_sizes(args.sizes) if args.sizes else {}
        if args.model_dir:
            for number in EXCLUDED_SHARDS:
                name = "model-%05d-of-%05d.safetensors" % (number, TOTAL_SHARDS)
                if os.path.exists(os.path.join(args.model_dir, name)):
                    return refuse("%s exists in %s: the derived index is only for "
                                  "46-shard directories" % (name, args.model_dir))
            for name in kept_shards:
                path = os.path.join(args.model_dir, name)
                if os.path.isfile(path):
                    sizes[name] = os.path.getsize(path)
        new_doc, removed, redirect, removed_bytes = derive(
            doc, table_keys, projections, experts, unknowns, kept_shards, sizes,
            sidecar=sidecar, sidecar_bytes=args.sidecar_bytes or 0)
    except Refusal as exc:
        # Loud classified refusal for the plan-relevant classes.
        try:
            counts = {"excluded": len(excluded), "tables": len(table_keys),
                      "projections": len(projections), "experts": len(experts),
                      "unknowns": len(unknowns)}
            print("47/48 ENTRY CLASSIFICATION: " + json.dumps(counts),
                  file=sys.stderr)
        except NameError:
            pass
        return refuse(str(exc))
    except OSError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    original_sha = sha256_file(args.index)
    text = json.dumps(new_doc, indent=2) + "\n"
    derived_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    original_total = doc.get("metadata", {}).get("total_size")

    summary = {
        "tool": TOOL,
        "tool_version": TOOL_VERSION,
        "at": datetime.now(timezone.utc).isoformat(),
        "index": os.path.abspath(args.index),
        "original_sha256": original_sha,
        "original_total_size": original_total,
        "excluded_entry_count": len(excluded),
        "classification": {
            DISK_RESOLVED: len(table_keys),
            PROJECTION: len(projections),
            EXPERT: len(experts),
            UNKNOWN: len(unknowns),
        },
        "removed_entries": dict(sorted(removed.items())),
        "removed_entry_count": len(removed),
        "removed_bytes": removed_bytes,
        "redirected_entries": dict(sorted(redirect.items())),
        "redirect_target": sidecar,
        "sidecar_bytes_in_total": args.sidecar_bytes if sidecar else 0,
        "derived_total_size": new_doc["metadata"]["total_size"],
        "retained_shard_count": len(kept_shards),
        "retained_shards": sorted(kept_shards),
        "derived_sha256": derived_sha,
        "derived_bytes": len(text.encode("utf-8")),
        "sizes_source": (os.path.abspath(args.sizes) if args.sizes
                         else (os.path.abspath(args.model_dir) if args.model_dir
                               else None)),
        "validation": "passed",
    }
    print(json.dumps(summary, indent=2))

    if not args.commit:
        print("dry-run: nothing written (pass --commit to write output/receipt)",
              file=sys.stderr)
        return 0
    if not args.output or not args.receipt:
        return refuse("--commit requires both --output and --receipt")
    for path in (args.output, args.receipt):
        if os.path.exists(path):
            return refuse("refusing to overwrite existing path %r" % path)
    atomic_write(args.output, text)
    atomic_write(args.receipt,
                 json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print("wrote %s and %s" % (args.output, args.receipt), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
