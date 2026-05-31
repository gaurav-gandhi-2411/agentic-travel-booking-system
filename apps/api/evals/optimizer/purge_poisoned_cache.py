"""One-time cleanup utility: remove poisoned entries from judge_cache.json.

A poisoned entry is one where the judge failed to parse LLM output on all 3
attempts. These entries have either parse_failed=True (new-style) or
all_scores=[] (legacy, pre-parse_failed field). Genuine score=1 entries that
parsed correctly will have all_scores=[1, 1, 1] and are NOT purged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

_CACHE_FILE = Path(__file__).parent / "cache" / "judge_cache.json"
_logger = structlog.get_logger(__name__)


def _is_poisoned(entry: dict[str, Any]) -> bool:
    """Return True if this cache entry represents a failed parse rather than a real score."""
    if entry.get("parse_failed") is True:
        return True
    # Legacy entries written before parse_failed existed: empty all_scores means parse failed.
    if entry.get("all_scores") == []:
        return True
    return False


def purge_poisoned_entries(cache_path: Path) -> dict[str, int]:
    """Load cache at cache_path, remove poisoned entries, write back, return stats.

    Returns a dict with keys: scanned, purged, retained.
    """
    if not cache_path.exists():
        return {"scanned": 0, "purged": 0, "retained": 0}

    raw = cache_path.read_text(encoding="utf-8")
    cache: dict[str, Any] = json.loads(raw)

    scanned = len(cache)
    clean: dict[str, Any] = {k: v for k, v in cache.items() if not _is_poisoned(v)}
    purged = scanned - len(clean)

    cache_path.write_text(json.dumps(clean, indent=2), encoding="utf-8")

    return {"scanned": scanned, "purged": purged, "retained": len(clean)}


def main() -> None:
    """Run the purge against the default cache file and print a summary."""
    stats = purge_poisoned_entries(_CACHE_FILE)
    print(
        f"Cache purge complete — "
        f"scanned: {stats['scanned']}, "
        f"purged: {stats['purged']}, "
        f"retained: {stats['retained']}"
    )
    _logger.info(
        "judge_cache_purge_complete",
        scanned=stats["scanned"],
        purged=stats["purged"],
        retained=stats["retained"],
    )


if __name__ == "__main__":
    main()
