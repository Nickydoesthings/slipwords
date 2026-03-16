from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Entry, SessionLocal

CEDICT_PATH = Path(__file__).resolve().parents[1] / "data" / "cedict_ts.u8"
# SUBTLEX-CH word frequency file (tab-separated with a header row, as distributed).
# Expected columns include at least "Word" and "logW" (log word frequency).
SUBTLEX_PATH = Path(__file__).resolve().parents[1] / "data" / "SUBTLEX-CH-WF.txt"


def parse_line(line: str) -> tuple[str, str, str, str, str, bool]:
    """Parse a single CC-CEDICT line into normalized fields."""
    line = line.strip()
    # Example: 皮實 皮实 [pi2 shi5] /(of things) durable/(of people) sturdy; tough/
    m = re.match(
        r"^(?P<trad>\S+)\s+(?P<simp>\S+)\s+\[(?P<pinyin>[^\]]+)\]\s+/(?P<defs>.+)/$",
        line,
    )
    if not m:
        raise ValueError(f"Could not parse line: {line}")

    trad = m.group("trad")
    simp = m.group("simp")
    pinyin_numbered = m.group("pinyin").strip()
    raw_defs = m.group("defs").strip()

    pinyin_toned = numbered_to_tone_marks(pinyin_numbered)
    pinyin_bare = strip_pinyin(pinyin_toned)

    is_variant = detect_variant(raw_defs)

    return simp, trad, pinyin_toned, pinyin_numbered, pinyin_bare, is_variant


def numbered_to_tone_marks(pinyin: str) -> str:
    """Convert numbered pinyin (xue2 xi2) to tone-mark pinyin (xué xí)."""
    # Adapted from common pinyin tone placement rules.
    tone_map = {
        "a": "āáǎàa",
        "e": "ēéěèe",
        "i": "īíǐìi",
        "o": "ōóǒòo",
        "u": "ūúǔùu",
        "v": "ǖǘǚǜü",  # v used for ü in numbered pinyin
        "ü": "ǖǘǚǜü",
    }

    # Convert a single numbered pinyin syllable to a tone-marked pinyin syllable.
    def convert_syllable(syl: str) -> str:
        m = re.match(r"^([a-züv]+)([1-5])$", syl)
        if not m:
            return syl
        base, tone_str = m.groups()
        tone = int(tone_str)
        if tone == 5:
            # Neutral tone: no diacritic
            return base.replace("v", "ü")

        # Tone placement priority: a, e, ou; otherwise last vowel
        vowels = "aeiouüv"
        idx = -1
        for ch in base:
            if ch in "aeA E":  # force on a or e if present
                idx = base.lower().find(ch.lower())
                break

        if idx == -1:
            if "ou" in base:
                idx = base.lower().find("ou")
            else:
                # last vowel
                for i in range(len(base) - 1, -1, -1):
                    if base[i].lower() in vowels:
                        idx = i
                        break

        if idx == -1:
            return base.replace("v", "ü")

        ch = base[idx]
        lower = ch.lower()
        key = "v" if lower == "v" else lower
        if key not in tone_map:
            return base.replace("v", "ü")
        tone_chars = tone_map[key]
        marked = tone_chars[tone - 1]

        if ch.isupper():
            marked = marked.upper()

        return base[:idx] + marked + base[idx + 1 :].replace("v", "ü")

    result: List[str] = []
    for syl in pinyin.split():
        result.append(convert_syllable(syl))
    return " ".join(result)


def strip_pinyin(pinyin: str) -> str:
    """Strip tone marks and numbers from pinyin, lowercase, collapse spaces."""
    # Remove numbers
    no_nums = re.sub(r"[1-5]", "", pinyin)

    # Replace tone-marked vowels with plain ones (25 chars: 4 each a,e,i,o,u + 5 ü)
    replace_map = str.maketrans(
        "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü",
        "aaaaeeeeiiiioooouuuuuuuuu",
    )
    plain = no_nums.translate(replace_map)

    # Normalize spaces and lowercase
    parts = plain.split()
    return " ".join(parts).lower()


def detect_variant(definitions: str) -> bool:
    """Return True if any definition marks this as a variant."""
    parts = [d.strip().lower() for d in definitions.split("/") if d.strip()]
    for d in parts:
        if (
            d.startswith("variant of")
            or d.startswith("old variant of")
            or d.startswith("archaic form of")
            or d.startswith("archaic variant of")
        ):
            return True
    return False


def load_frequencies(path: Path) -> dict[str, float]:
    """Load SUBTLEX-CH word frequencies keyed by simplified word."""
    if not path.exists():
        print(f"SUBTLEX file not found at {path} — continuing without frequency data.")
        return {}

    freq_by_word: dict[str, float] = {}
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            return {}

        # Find indices for the word and log-frequency columns.
        word_idx = None
        logw_idx = None
        for idx, name in enumerate(header):
            lowered = name.strip().lower()
            if lowered == "word":
                word_idx = idx
            elif lowered == "logw":
                logw_idx = idx

        if word_idx is None or logw_idx is None:
            print("SUBTLEX header does not contain expected 'Word' and 'logW' columns.")
            return {}

        for row in reader:
            if len(row) <= max(word_idx, logw_idx):
                continue
            word = row[word_idx].strip()
            logw_raw = row[logw_idx].strip()
            if not word or not logw_raw:
                continue
            try:
                logw = float(logw_raw)
            except ValueError:
                continue
            # Use the simplified word as the key; SUBTLEX-CH is simplified-only.
            freq_by_word[word] = logw

    print(f"Loaded {len(freq_by_word)} SUBTLEX frequency entries from {path.name}.")
    return freq_by_word


def iter_entries(path: Path, freq_by_word: dict[str, float] | None = None) -> Iterable[Entry]:
    """Yield Entry ORM objects parsed from a CEDICT file, with optional frequency info."""
    if freq_by_word is None:
        freq_by_word = {}

    lines_tried = 0
    parse_failures = 0
    first_failure_msg: str | None = None
    first_failure_line: str | None = None

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line or line.startswith("#"):
                continue
            lines_tried += 1
            try:
                (
                    simplified,
                    traditional,
                    pinyin_toned,
                    pinyin_numbered,
                    pinyin_bare,
                    is_variant,
                ) = parse_line(line)
            except ValueError as e:
                parse_failures += 1
                if first_failure_msg is None:
                    first_failure_msg = str(e)
                    first_failure_line = line.strip()[:100]
                continue

            defs = line.split("/", 1)[1].rsplit("/", 1)[0].strip()
            freq_log = freq_by_word.get(simplified)

            yield Entry(
                simplified=simplified,
                traditional=traditional,
                pinyin_toned=pinyin_toned,
                pinyin_numbered=pinyin_numbered,
                pinyin_bare=pinyin_bare,
                definitions=defs,
                is_variant=is_variant,
                freq_log=freq_log,
            )

    if lines_tried > 0:
        print(f"Diagnostic: {lines_tried} non-comment lines read, {parse_failures} parse failures.")
        if first_failure_msg is not None:
            print(f"First failure: {first_failure_msg}")
            print(f"First failing line (truncated): {first_failure_line!r}")


def import_cedict(session: Session, truncate: bool = True, batch_size: int = 1000) -> None:
    """Import all entries from CC-CEDICT into the Postgres entries table."""
    if truncate:
        session.execute(text("TRUNCATE TABLE entries RESTART IDENTITY CASCADE;"))

    freq_by_word = load_frequencies(SUBTLEX_PATH)

    buffer: list[Entry] = []
    for entry in iter_entries(CEDICT_PATH, freq_by_word=freq_by_word):
        buffer.append(entry)
        if len(buffer) >= batch_size:
            session.bulk_save_objects(buffer)
            session.commit()
            buffer.clear()

    if buffer:
        session.bulk_save_objects(buffer)
        session.commit()


def main() -> None:
    """Entry point: import CC-CEDICT into Postgres."""
    if not CEDICT_PATH.exists():
        raise FileNotFoundError(f"CEDICT file not found at {CEDICT_PATH}")

    with SessionLocal() as session:
        import_cedict(session)


if __name__ == "__main__":
    main()