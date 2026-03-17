# Slipwords — Data Reference

## Source: CC-CEDICT

- **What it is:** A free, community-maintained Chinese-English dictionary.
- **License:** Creative Commons BY-SA 4.0. Must attribute if redistributed.
- **Download:** https://www.mdbg.net/chinese/dictionary?page=cedict
- **Format:** Plain `.txt` file, one entry per line, with a specific syntax (see below).
- **Size:** ~120,000 entries. Parsed and imported into Postgres — do not query the raw file at runtime.

### CC-CEDICT Line Format
```
Traditional Simplified [pin1 yin1] /gloss; gloss; .../gloss; gloss; .../
```

Example:
```
皮實 皮实 [pi2 shi5] /(of things) durable/(of people) sturdy; tough/
```

- Tone numbers (1–4, 5 = neutral) are used in the raw file. These get normalized on import.
- Lines beginning with `#` are comments and should be skipped during parsing.

---

## Database: PostgreSQL

### Table: `entries`

This is the only table for v1.

| Column | Type | Description |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | Auto-incrementing integer ID |
| `simplified` | `TEXT NOT NULL` | Simplified Chinese characters (e.g. 学习) |
| `traditional` | `TEXT NOT NULL` | Traditional Chinese characters (e.g. 學習). Same as simplified if no difference. |
| `pinyin_toned` | `TEXT NOT NULL` | Pinyin with Unicode tone marks (e.g. xué xí) |
| `pinyin_numbered` | `TEXT NOT NULL` | Pinyin with tone numbers as in source file (e.g. xue2 xi2) |
| `pinyin_bare` | `TEXT NOT NULL` | Pinyin with all tone marks and numbers stripped, lowercase (e.g. xue xi). Used for bare pinyin search. |
| `definitions` | `TEXT NOT NULL` | Raw definitions string from CC-CEDICT, stored as slash-separated (e.g. "to learn/to study"). Split on display. |
| `is_variant` | `BOOLEAN DEFAULT FALSE` | True if CC-CEDICT marks this entry as a variant/alternate form. Useful for de-prioritizing in results. |
| `freq_log`        | `DOUBLE PRECISION`      | Optional SUBTLEX-CH log word frequency for the simplified form. Higher = more frequent. May be NULL. |


### Indexes

Indexes are critical. Without them, every search scans all 120,000 rows.

| Index | Column | Type | Purpose |
|---|---|---|---|
| `idx_simplified` | `simplified` | btree | Exact hanzi lookup |
| `idx_traditional` | `traditional` | btree | Traditional hanzi lookup |
| `idx_pinyin_bare` | `pinyin_bare` | btree | Bare pinyin search |
| `idx_pinyin_toned` | `pinyin_toned` | btree | Toned pinyin search |
| `idx_definitions_fts` | `definitions` | GIN (full-text) | English keyword search |

The full-text search index (`GIN`) is a special Postgres feature for searching within text. It's what makes English searches like "to study" fast.

### SQL to create the table and indexes

```sql
CREATE TABLE entries (
    id SERIAL PRIMARY KEY,
    simplified TEXT NOT NULL,
    traditional TEXT NOT NULL,
    pinyin_toned TEXT NOT NULL,
    pinyin_numbered TEXT NOT NULL,
    pinyin_bare TEXT NOT NULL,
    definitions TEXT NOT NULL,
    is_variant BOOLEAN DEFAULT FALSE
    freq_log DOUBLE PRECISION

);

CREATE INDEX idx_simplified ON entries(simplified);
CREATE INDEX idx_traditional ON entries(traditional);
CREATE INDEX idx_pinyin_bare ON entries(pinyin_bare);
CREATE INDEX idx_pinyin_toned ON entries(pinyin_toned);
CREATE INDEX idx_definitions_fts ON entries USING GIN(to_tsvector('english', definitions));
```

---

## Normalization Rules (applied during import in `parse_cedict.py`)

These transformations happen once at import time, not at query time.

**Tone mark conversion:**
CC-CEDICT uses tone numbers (xue2). Convert to Unicode tone marks (xué) using a standard mapping. Libraries like `dragonmapper` or `pinyin` handle this. Store both forms.

**Bare pinyin:**
Strip all tone marks and numbers, lowercase everything, collapse multiple spaces.
- `xué xí` → `xue xi`
- `Zhōng guó` → `zhong guo`

**Definitions:**
Store as-is from CC-CEDICT (slash-separated string). Split into a list only at display time in the Jinja2 template. Storing as a single string keeps the schema simple and avoids a separate definitions table for v1.

---

### Frequency data (SUBTLEX-CH)
We enrich some entries with word frequency information from **SUBTLEX-CH** (film/TV subtitle corpus):
- Source: SUBTLEX-CH word frequency list (`SUBTLEX-CH-WF`).
- Join key: simplified word form (SUBTLEX-CH is simplified-only).
- Column: `freq_log` stores the published `logW` value (log word frequency). Higher = more frequent in the subtitle corpus.

---


