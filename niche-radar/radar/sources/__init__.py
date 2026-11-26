"""One module per data source. Each returns plain data and raises SourceError on failure."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Signal:
    """One observation: a term seen on a source at collection time."""

    source: str
    market: str | None
    term: str
    rank: int | None = None
    value: float | None = None
    label: str | None = None
    url: str | None = None
    extra: dict = field(default_factory=dict)
