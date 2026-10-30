"""The pre-tested variant pool for `python -m dashboard --random` (planning/RANDOM-DEMO-SPEC.md).

runs/pool/manifest.json lists the KEPT variants only: {"total": 8, "variants": [{"id", "decoy", "phone", "hiding",
"message_id"}]}. Each kept variant has two saved real runs, runs/pool/<id>-guard0.json and -guard1.json: they are that
variant's honest REPLAY and its fallback when a live run fails. The manifest is not trusted on its own: a variant is
offered only if both run files load and GUARD=0 really scored possessed and GUARD=1 whispering.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from ghostreply.harness import ROOT, load_run

POOL_DIR = ROOT / "runs" / "pool"
POOL_MIN = 3  # fewer kept variants than this: --random falls back to the canonical msg-004 and says so
EXPECTED = {0: "possessed", 1: "whispering"}
FIELDS = ("id", "decoy", "phone", "hiding")


def run_paths(vid: str, pool_dir: Path = POOL_DIR) -> dict[int, Path]:
    return {g: Path(pool_dir) / f"{vid}-guard{g}.json" for g in (0, 1)}


def load_pool(pool_dir: Path | str = POOL_DIR) -> tuple[list[dict], int, str | None]:
    """(variants that can be shown, how many were generated, a note on why some or all were left out).

    Never raises: a missing or broken pool is an empty list and a reason."""
    pool_dir = Path(pool_dir)
    try:
        manifest = json.loads((pool_dir / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["variants"]
        total = int(manifest.get("total", len(entries)))
        if not isinstance(entries, list):
            raise TypeError("variants is not a list")
    except Exception as exc:
        return [], 0, f"no usable pool manifest ({type(exc).__name__})"
    usable, dropped = [], []
    for entry in entries:
        try:
            if not isinstance(entry, dict) or not all(isinstance(entry.get(f), str) and entry[f] for f in FIELDS):
                raise ValueError("incomplete entry")
            for guard, path in run_paths(entry["id"], pool_dir).items():
                level = (load_run(path).get("verdict") or {}).get("level")
                if level != EXPECTED[guard]:
                    raise ValueError(f"GUARD={guard} saved run is {level!r}, not {EXPECTED[guard]}")
            usable.append({f: entry[f] for f in FIELDS} | {"message_id": str(entry.get("message_id") or entry["id"])})
        except Exception as exc:
            dropped.append(f"{entry.get('id', '?') if isinstance(entry, dict) else '?'}: {exc}")
    note = f"left out: {'; '.join(dropped)}" if dropped else None
    return usable, max(total, len(usable)), note


def pick(variants: list[dict], rng: random.Random | None = None, avoid: str | None = None) -> dict | None:
    """One variant at random (SystemRandom unless a test passes its own), not `avoid` when there is another."""
    if not variants:
        return None
    rng = rng or random.SystemRandom()
    choices = [v for v in variants if v["id"] != avoid] or variants
    return rng.choice(choices)
