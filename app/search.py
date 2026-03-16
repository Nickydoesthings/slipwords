# Search logic: input detection + query construction (all query logic lives here, not in main.py)
from __future__ import annotations

import re
from typing import Literal

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.models import Entry
from app.pinyin_syllables import VALID_BARE_SYLLABLES

# Maximum number of results returned per search.
MAX_RESULTS = 30

# Unicode range for CJK Unified Ideographs (Chinese characters).
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# Unicode tone-marked vowels (used to detect "toned pinyin" input).
_TONE_MARKS = "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ"


def _has_chinese(s: str) -> bool:
    """Return True if string contains at least one Chinese character."""
    return bool(_CJK_RE.search(s))


def _has_tone_marks(s: str) -> bool:
    """Return True if string contains Unicode pinyin tone marks."""
    return any(c in _TONE_MARKS for c in s)


def _is_bare_pinyin_input(s: str) -> bool:
    """Return True if string is non-empty, ASCII-only, and every space-separated token is a valid bare syllable."""
    s = s.strip()
    if not s:
        return False
    # Allow only letters and spaces (no digits, no tone marks).
    if not re.match(r"^[a-zA-Z\s]+$", s):
        return False
    tokens = s.lower().split()
    return all(t in VALID_BARE_SYLLABLES for t in tokens)


def detect_query_type(query: str) -> Literal["hanzi", "toned_pinyin", "bare_pinyin", "fts"]:
    """
    Classify the search input so we can run the right query.
    Order: Chinese chars → toned pinyin → bare pinyin → fallback to full-text on definitions.
    """
    q = query.strip()
    if not q:
        return "fts"
    if _has_chinese(q):
        return "hanzi"
    if _has_tone_marks(q):
        return "toned_pinyin"
    if _is_bare_pinyin_input(q):
        return "bare_pinyin"
    return "fts"


def search_hanzi(session: Session, q: str, limit: int = MAX_RESULTS) -> list[Entry]:
    """
    Search by simplified/traditional characters. Exact matches first, then prefix matches.
    Within each group, more frequent words (freq_log) are preferred.
    """
    q = q.strip()
    stmt = (
        select(Entry)
        .where(
            or_(
                Entry.simplified == q,
                Entry.traditional == q,
                Entry.simplified.startswith(q),
                Entry.traditional.startswith(q),
            )
        )
        .order_by(
            # Exact match first (either column), then by frequency (more common words first),
            # then by simplified form for a stable order.
            or_(Entry.simplified == q, Entry.traditional == q).desc(),
            Entry.freq_log.desc().nullslast(),
            Entry.simplified,
        )
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def search_toned_pinyin(session: Session, q: str, limit: int = MAX_RESULTS) -> list[Entry]:
    """
    Search by pinyin with tone marks. Exact matches first, then contains.
    Within each group, more frequent words (freq_log) are preferred.
    """
    q = q.strip()
    stmt = (
        select(Entry)
        .where(
            or_(
                Entry.pinyin_toned == q,
                Entry.pinyin_toned.contains(q),
            )
        )
        .order_by(
            (Entry.pinyin_toned == q).desc(),
            Entry.freq_log.desc().nullslast(),
            Entry.pinyin_toned,
        )
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def search_bare_pinyin(session: Session, q: str, limit: int = MAX_RESULTS) -> list[Entry]:
    """
    Search by bare pinyin (no tones). Normalize to lowercase; exact match first, then contains.
    Within each group, more frequent words (freq_log) are preferred.
    """
    q = q.strip().lower()
    stmt = (
        select(Entry)
        .where(
            or_(
                Entry.pinyin_bare == q,
                Entry.pinyin_bare.contains(q),
            )
        )
        .order_by(
            (Entry.pinyin_bare == q).desc(),
            Entry.freq_log.desc().nullslast(),
            Entry.pinyin_bare,
        )
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def search_definitions_fts(session: Session, q: str, limit: int = MAX_RESULTS) -> list[Entry]:
    """
    Full-text search on the definitions column. Uses GIN index idx_definitions_fts.
    Results are ordered by:
      1. SUBTLEX log frequency (freq_log) if available, then
      2. Normalized ts_rank (per definition segment).
    """
    q = q.strip()
    if not q:
        return []
    # plainto_tsquery tokenizes the query, drops stopwords (e.g. "to"), and builds an AND query.
    # Ranking:
    #   * Primary: SUBTLEX-CH log frequency (freq_log) so common words surface first.
    #   * Secondary: normalized ts_rank_cd = ts_rank_cd / segment_count, where segment_count is
    #     the number of slash-separated glosses in the definitions field. ts_rank_cd takes into
    #     account coverage and proximity of the query terms, which helps multi-word queries.
    stmt = text(
        """
        SELECT id, simplified, traditional, pinyin_toned, pinyin_numbered, pinyin_bare, definitions, is_variant, freq_log
        FROM entries
        WHERE to_tsvector('english', definitions) @@ plainto_tsquery('english', :q)
        ORDER BY
            freq_log DESC NULLS LAST,
            ts_rank_cd(
                to_tsvector('english', definitions),
                plainto_tsquery('english', :q)
            )
            / GREATEST(
                COALESCE(array_length(string_to_array(definitions, '/'), 1), 1),
                1
            ) DESC
        LIMIT :lim
        """
    )
    rows = session.execute(stmt, {"q": q, "lim": limit}).fetchall()
    # Map rows back to Entry-like objects for the template. We have (id, simplified, traditional, ...).
    return [
        Entry(
            id=r.id,
            simplified=r.simplified,
            traditional=r.traditional,
            pinyin_toned=r.pinyin_toned,
            pinyin_numbered=r.pinyin_numbered,
            pinyin_bare=r.pinyin_bare,
            definitions=r.definitions,
            is_variant=r.is_variant,
            freq_log=getattr(r, "freq_log", None),
        )
        for r in rows
    ]


def run_search(session: Session, query: str) -> tuple[list[Entry], Literal["hanzi", "toned_pinyin", "bare_pinyin", "fts"]]:
    """
    Detect query type and run the appropriate search. Returns (results, query_type).
    """
    qtype = detect_query_type(query)
    if qtype == "hanzi":
        results = search_hanzi(session, query)
    elif qtype == "toned_pinyin":
        results = search_toned_pinyin(session, query)
    elif qtype == "bare_pinyin":
        results = search_bare_pinyin(session, query)
    else:
        results = search_definitions_fts(session, query)
    return results, qtype
