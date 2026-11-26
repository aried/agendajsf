"""Small text helpers shared by sources and the report."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

_COUNT_RE = re.compile(r"^\s*([\d.,]+)\s*([kKmM]?)")
_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)

# Function words only, so recurring title terms are about the subject.
STOPWORDS = frozenset(
    """
    a o os as um uma uns umas de do da dos das em no na nos nas por para com sem que e ou
    se ao aos à às é foi ser como mais mas não nao já ja eu ele ela você voce meu minha seu
    sua isso este esta esse essa aqui muito sobre até ate entre quando onde quem qual
    the an of in on at to for with and or is are was were be this that it its as by from
    you your my we they i he she his her not no how what why when who which vs
    el la los las un una unos unas del al en con sin y que es son fue por para como más
    mas pero su sus mi tu lo le se si ya qué cómo
    """.split()
)


def norm(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def parse_count(label: str) -> float | None:
    """Turn labels such as '200+', '2K+' or '1.5M' into a number."""
    match = _COUNT_RE.match(label or "")
    if not match:
        return None
    digits = match.group(1).replace(",", "")
    try:
        value = float(digits)
    except ValueError:
        return None
    return value * {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()]


def iso_duration_seconds(value: str) -> int:
    """Parse an ISO 8601 duration such as 'PT1H2M3S'; unknown shapes give 0."""
    match = _DURATION_RE.match(value or "")
    if not match:
        return 0
    parts = {k: int(v or 0) for k, v in match.groupdict().items()}
    return parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def recurring_terms(titles: list[str], top: int = 15) -> list[tuple[str, int]]:
    """Words and two-word phrases that recur across titles, most frequent first.

    Each title counts a term once, so one repetitive title cannot dominate.
    """
    counts: Counter[str] = Counter()
    for title in titles:
        words = [w for w in norm(title).split() if len(w) > 2 and w not in STOPWORDS]
        seen = set(words)
        seen.update(f"{a} {b}" for a, b in zip(words, words[1:]))
        counts.update(seen)
    return [(term, n) for term, n in counts.most_common() if n >= 2][:top]
