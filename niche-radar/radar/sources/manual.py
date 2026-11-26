"""Signals typed in by hand.

TikTok, Instagram, X and Reddit offer no free API that an unattended script may
use, so what you see there goes into data/manual_signals.csv:

    date,source,market,term,note
    2026-11-26,tiktok_search_insights,br,termo que apareceu,content gap
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import Signal


def load(path: Path) -> list[Signal]:
    if not path.exists():
        return []
    signals = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            term = (row.get("term") or "").strip()
            if not term:
                continue
            signals.append(
                Signal(
                    source="manual:" + (row.get("source") or "outro").strip(),
                    market=(row.get("market") or "").strip() or None,
                    term=term,
                    label=(row.get("note") or "").strip() or None,
                    extra={"date": (row.get("date") or "").strip()},
                )
            )
    return signals
