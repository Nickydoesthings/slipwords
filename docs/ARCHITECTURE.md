# Slipwords — Architecture Overview

## What This Project Is
A Chinese-English dictionary website targeting learners of Mandarin Chinese.
Data source: CC-CEDICT (open-source, ~120,000 entries).
Primary audience: Simplified Chinese learners; Traditional Chinese supported in data layer.

---

## Tech Stack
| Layer | Technology | Purpose |
|---|---|---|
| Web framework | FastAPI (Python) | Handles HTTP requests, routing, business logic |
| Templating | Jinja2 | Renders server-side HTML pages |
| Database | PostgreSQL | Stores all dictionary entries |
| Hosting | Railway | Runs the FastAPI app |
| DB Hosting | Supabase or Neon | Managed Postgres instance |
| CDN / DNS | Cloudflare | Caching, DDoS protection, SSL |
| Domain | slipwords.com | Registered separately, DNS pointed at Cloudflare |

---

## Project Folder Structure

```
slipwords/
│
├── app/                        # All application code
│   ├── main.py                 # FastAPI app entry point; defines all routes
│   ├── database.py             # Database connection setup
│   ├── models.py               # SQLAlchemy table definitions (mirrors DATA.md schema)
│   ├── search.py               # Search logic: input detection + query construction
│   ├── templates/              # Jinja2 HTML templates
│   │   ├── base.html           # Base layout (header, search bar, footer)
│   │   ├── index.html          # Homepage
│   │   ├── results.html        # Search results page
│   │   └── entry.html          # Single word/character detail page
│   └── static/                 # Static assets served directly
│       ├── style.css           # All site CSS (one file for now)
│       └── favicon.ico
│
├── scripts/                    # One-off utility scripts (not part of the web app)
│   ├── parse_cedict.py         # Parses CC-CEDICT .txt file and populates the database
│   └── update_cedict.py        # (Future) pulls latest CC-CEDICT and updates DB
│
├── docs/                       # Project documentation
│   ├── ARCHITECTURE.md         # This file
│   ├── DATA.md                 # Database schema and data source notes
│   └── UI_REFERENCE.md         # UI/design reference and principles
│
├── .cursorrules                # LLM behavior rules for Cursor (see bottom of this file)
├── .env                        # Local environment variables (never commit this)
├── .env.example                # Template showing what .env variables are needed
├── .gitignore                  # Excludes .env, __pycache__, etc.
├── requirements.txt            # Python dependencies
└── README.md                   # Basic setup instructions
```

---

## Request Flow

A user visits `slipwords.com/search?q=学习`:

```
Browser request
  → Cloudflare (cache check; if cached, returns immediately)
  → Railway (FastAPI app)
      → main.py router receives request
      → calls search.py to detect input type and build query
      → search.py queries Postgres via database.py
      → results returned to main.py
      → main.py passes results to Jinja2 template (results.html)
      → rendered HTML returned to browser
```

For a direct entry lookup (`slipwords.com/entry/学习`):
- Same flow but skips search detection, does a direct DB lookup by hanzi key.

---

## Core Modules

### `main.py`
Defines all URL routes. Keeps route handlers thin — they receive a request, call the appropriate function from `search.py`, and pass results to a template. No business logic lives here.

Routes:
- `GET /` → renders `index.html` (homepage)
- `GET /search?q={query}` → runs search, renders `results.html`
- `GET /entry/{hanzi}` → fetches one entry, renders `entry.html`

### `search.py`
The most important module. Responsible for:
1. **Input detection** — determining whether the query is hanzi, toned pinyin, bare pinyin, or English
2. **Query construction** — building the appropriate Postgres query based on detected type
3. **Result ranking** — ordering results by relevance (exact match first, then partial matches)

Input detection logic (in order):
- Contains Chinese characters → hanzi search
- Contains tone numbers or tone marks (ā á ǎ à etc.) → toned pinyin search
- All ASCII letters and spaces, matches known pinyin syllables → bare pinyin search
- Fallback → English definition search

### `database.py`
Sets up the SQLAlchemy connection to Postgres using the `DATABASE_URL` environment variable. Provides a session factory used by all other modules. No query logic lives here.

### `models.py`
Defines the `Entry` table using SQLAlchemy ORM. Mirrors the schema in DATA.md exactly. If the schema changes, update both this file and DATA.md together.

### `parse_cedict.py` (script, not part of web app)
Run once to populate the database. Reads the CC-CEDICT `.txt` file line by line, parses each entry, normalizes pinyin, and inserts rows into the database. Should be idempotent (safe to re-run without creating duplicates).

---

## Environment Variables (`.env`)
```
DATABASE_URL=postgresql://user:password@host:port/dbname
DEBUG=True   # Set to False in production
```

---

## `.cursorrules` Contents
```
- This is a Chinese-English dictionary web app called Slipwords.
- Backend: FastAPI (Python 3.11+). Always use type hints.
- Templating: Jinja2 (server-rendered HTML). Do not introduce React, Vue, or any JS framework.
- Database: PostgreSQL via SQLAlchemy ORM. Schema is defined in docs/DATA.md and app/models.py.
- Keep functions small and single-purpose. Add a one-line docstring to every function.
- Do not add new Python dependencies without flagging it explicitly.
- Do not generate code that modifies the database schema without being explicitly asked.
- Search logic lives in search.py only. Do not put query construction in main.py.
- When generating SQL or ORM queries, always consider whether an index exists for the column being searched (see DATA.md).
```

---

## What Is Not In Scope (v1)
- User accounts or login
- Flashcard or SRS features
- Stroke order diagrams
- HSK frequency levels
- Radical decomposition
- LLM-generated content
- API access for third-party developers
