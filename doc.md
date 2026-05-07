# SearXNG Corporate Events Analyzer — Research engine architecture

This document describes the **research engine**: web search, HTML evidence, model calls, heuristics, and how they orchestrate inside **`server.py`** + **`searxng_analyzer.py`**. **Persistence**, catalogs, and “saved company” payloads are application glue—they are intentionally out of scope here.

## Research engine vs app shell

| Layer | Responsibility |
|--------|----------------|
| **`searxng_analyzer.py`** | SerpAPI (parallel batches), Wikipedia/Yahoo helpers, prompts, OpenRouter completion helpers, **`generate_summary`**, **`generate_description`**, **`generate_corporate_events`**, **`get_top_management`**, **`detect_ownership_from_description`**, HQ/LinkedIn/person-location search stacks, ticker lookup, startup vs enterprise heuristic for query packs. |
| **`server.py`** | HTTP ingress, **`fetch_html`** (Scrapfly + fallback), heuristic extractors (**`extract_structured_data`**, **`extract_dates_with_context`**, **`extract_investment_fields`**), **`ai_extract_event_from_text`**, **`ai_enrich_single_event`**, **`smart_enrich_event`**, **`/analyze`** parallel fan-out (**`ThreadPoolExecutor`**), stripping counterparties when options say so. |
| **UI (`index.html`)** | Triggers engine routes; compares engine output side-by-side with any stored snapshot the app already holds (not documented here). |

## Architecture diagram — engine ingress

```
┌─────────────────────────────────────────────────────────────────────────┐
│ CLIENT (SPA)                                                            │
│  • Run analyze (+ profile scope flags: overview / events / people / CP) │
│  • Smart Enrich · optional HQ · person 📍 · person LinkedIn             │
└─────────────────────────────────────────────────────────────────────────┘
        │ POST /analyze  ──────────────────────────────────┐
        │ POST /smart_enrich_event  · enrich/extract APIs  │
        │ POST /api/search_*                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ server.py                                                               │
│  /analyze                                                               │
│   ├─ (optional — app merges stored snapshot separately, not documented) │
│   └─ parallel ThreadPoolExecutor(3):                                    │
│        • Overview lane  → searxng_analyzer: wiki, Yahoo if public,      │
│          Serp blitz + press probing, Sonar (/fallback) summary, GPT desc│
│        • Events lane    → detect_company_type, serpapi_parallel_search    │
│          (startup vs enterprise query packs), Claude extraction         │
│        • People lane    → wiki + mgmt Serp, Sonar list, Claude fallback,│
│          per-person LinkedIn search                                     │
│        Then (after overview returns): ownership LLM pass on description │
│        Then: keyword overlap between AI-event titles vs supplied DB list │
│  /smart_enrich_event                                                   │
│   └─ fetch → structure/heuristics → ai_extract_event_from_text → maybe  │
│      ai_enrich_single_event when fields still thin                      │
│  /api/search_individual_location                                        │
│   └─ Serp → LLM picks personal location → LLM normalization             │
│  Evidence helpers: fetch_html, BeautifulSoup text, corroborating search   │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ├── OpenRouter (Perplexity Sonar, Claude, GPT‑4o‑mini, …)
        ├── SerpAPI (Google organic, parallel aiohttp batches)
        ├── Scrapfly (optional robust GET)
        ├── Wikipedia REST
        └── Yahoo Finance (yfinance when ticker found)
```

## End-to-end — engine flows only

1) **`POST /analyze`** — For a company **query** (URL or name) plus **options**, run up to three **parallel** analyzer-backed tasks (`task_overview`, `task_events`, `task_management`). Each task is bounded network + LLM latency; overall wait ≈ slowest lane + ownership helper + lightweight server-side merging.
2) **Ownership inference** — After the overview lane returns **`description`**, **`detect_ownership_from_description`** runs (single LLM call) and is merged into the structured overview mapping in `server.py`.
3) **`/analyze` does not** batch-call **`enrich_ai_events_with_llm`** — no automatic “open every article URL” pass in shipped code.
4) **Auxiliary endpoints** — **`smart_enrich_event`**, **`enrich_event`**, **`extract_event_meta`**, **`ai_extract_event_from_url`**: ingest a URL (and sometimes an event skeleton), fetch HTML, run heuristics and/or **`ai_extract_event_from_text`**.
5) **Person / HQ assists** — **`/api/search_individual_location`** (two-stage normalization LLM stack), **`/api/search_individual_linkedin`**, **`/api/search_company_headquarters`**, **`/api/search_company_linkedin`** encapsulate narrower search stacks (implementations largely in **`searxng_analyzer.py`**).
6) **Out of scope** — Everything that only loads or saves a remote database (including HTTP proxies for that) is **not** the engine; see **Appendix: non-engine routes** at the end.

## Evidence Enrichment Layer (details)

- **Fetch**: `fetch_html(url)` uses Scrapfly POST (url in params + body) when key exists; fallback `requests.get`.
- **Heuristics**: `extract_first_date` (supports YYYY-MM-DD, dd/mm/yyyy, dd.mm.yyyy), `extract_investment_fields` (amount, currency symbol→ISO, funding stage regexes).
- **LLM**: `ai_extract_event_from_text(url, text)` with strict minified JSON schema to reduce parse errors.
- **Batch helper**: `enrich_ai_events_with_llm(ai_events)` exists (`server.py`) but **`/analyze` does not invoke it** in the shipped app — wiring it back in would revisit every event URL (slow/credit-heavy).
- **Smart enrich (SPA)**: `POST /smart_enrich_event` — primary UX path for assisted event fill from a URL.
- **Deep enrich (API / integration)**: `POST /enrich_event` → `ai_enrich_single_event`; **`/extract_event_meta`** heuristic-only; **`/ai_extract_event_from_url`** LLM-after-fetch.
- **Note**: Omitting Scrapfly/LLM keys yields partial/heuristic behaviour where code allows it.

## UI triggers (engine only)

What the SPA does that **hits Python search/LLM code**:

- **Run analyze** — `POST /analyze` with `query` + `options` (which lanes to run: overview, events, individuals; counterparties toggled with events).
- **Smart Enrich** — `POST /smart_enrich_event` (new-event sheet + event cards).
- **Company HQ assist** — `POST /api/search_company_headquarters`.
- **Person location** — `POST /api/search_individual_location`.
- **Person LinkedIn** — `POST /api/search_individual_linkedin`.

Client-side fuzzy person matching (`nameSimilarity`, canonical maps) is **browser-only**, not engine calls.

## HTTP surface — research routes

These are the **engine** entrypoints in [`server.py`](server.py):

| Method | Route | Engine behavior |
|--------|-------|------------------|
| `POST` | `/analyze` | Parallel **overview / events / management**; ownership LLM after description; keyword overlap vs any event list bundled in the JSON request. |
| `POST` | `/smart_enrich_event` | `fetch_html` → heuristics + `ai_extract_event_from_text`; may call `ai_enrich_single_event`. |
| `POST` | `/enrich_event` | `ai_enrich_single_event` (deep path). |
| `POST` | `/extract_event_meta` | Fetch + heuristic / structured scrape only. |
| `POST` | `/ai_extract_event_from_url` | Fetch + `ai_extract_event_from_text`. |
| `POST` | `/api/search_company_headquarters` | `search_company_headquarters` + US city→state shortcut table in handler. |
| `POST` | `/api/search_company_linkedin` | `search_company_linkedin_detailed`; handler may short-circuit when a canonical URL is already known — otherwise Serp/Startpage discovery dominates. |
| `POST` | `/api/search_individual_linkedin` | Serp/snippet → profile URL + location hints. |
| `POST` | `/api/search_individual_location` | Serp → `_extract_location_from_serpapi_with_ai` → `_normalize_location_with_ai`. |

**Non-engine routes** (`GET /`, `/refresh_db`, `/investors_*`) — **Appendix** at end of doc.

There is **no `GET /health`** in [`server.py`](server.py); add one if your platform requires a probe.

---

## External services (engine)

| Service | Role |
|---------|------|
| **OpenRouter** | Chat: Perplexity Sonar (overview, management, CEO lines), Claude (events extraction, enrich fallbacks), GPT‑4o‑mini (description writer, helpers). |
| **SerpAPI** | Google organic hits; **`serpapi_parallel_search`** batches queries with aiohttp. |
| **Scrapfly** | Optional HTML fetch before parsing when keys present. |
| **Wikipedia** | Overview/management grounding text when available. |
| **Yahoo (`yfinance`)** | Listed-company fundamentals when ticker resolves. |

## Data flow — engine paths only

```
POST /analyze
 └─ ThreadPoolExecutor: overview ∥ events ∥ management
      ├─ overview  → generate_summary + generate_description; then detect_ownership_from_description
      ├─ events    → generate_corporate_events
      └─ management → get_top_management
 └─ server maps markdown summary → structured ai_* fields; optional event-title overlap scoring

POST /smart_enrich_event
 └─ fetch_html → structured + heuristic dates/amounts → ai_extract_event_from_text
      → optionally ai_enrich_single_event when sparse

POST /api/search_individual_location
 └─ Serp(person) → LLM extract → LLM normalize

POST /api/search_company_headquarters
 └─ search_company_headquarters (Serp-heavy) [+ trivial US lookups in handler]
```

## Individual cross-referencing (browser)

Fuzzy **`nameSimilarity`**, **`_canonicalPeopleByLinkedIn`**, **`_canonicalPeopleByName`** — UI dedupe/display only.

---

## Deal Types Supported

| Deal Type | Description | Typical for |
|-----------|-------------|-------------|
| Acquisition | Company acquired another | Enterprise |
| Sale | Company was sold/acquired | Both |
| IPO | Initial public offering | Both |
| MBO | Management buyout | Enterprise |
| Investment | VC/PE investment, funding round | Both |
| Strategic Review | Exploring strategic options | Enterprise |
| Divestment | Selling off assets/divisions | Enterprise |
| Restructuring | Corporate restructuring | Enterprise |
| Grant | Government/innovation grant | Startup |
| Partnership | Strategic partnership | Both |
| Accelerator | Accelerator/incubator program | Startup |
| Award | Competition win, prize | Startup |
| Debt financing | Loan/debt financing | Both |

## Advisors

- Highlighted section per event (yellow). Shown for AI + DB events.
- Display format: `• Advisor Name (Type) → advised Party [Link]`.
- AI extraction structure:
```json
{
  "advisor_name": "Goldman Sachs",
  "advisor_type": "Financial Advisor",
  "advised_party": "S&P Global",
  "announcement_url": "https://..."
}
```

## Startup vs Enterprise Detection (legacy analyzer)

```
STARTUP signals (+1): startup, founded 20XX, seed/series, accelerator/incubator,
early-stage, venture-backed, pre-seed/angel, climate/fintech startup, scale-up.
ENTERPRISE signals (+1): fortune 500, nasdaq/nyse, publicly traded, billion revenue,
global leader/multinational, established 19XX/100+ years, s&p 500/dow jones/ftse 100.
Decision: higher score picks query set; unknown defaults to startup queries.
```

## File Structure

```
SearXNG-OpenRouter-30-10-main/
├── server.py                 # FastAPI backend server + enrichment endpoints
├── searxng_analyzer.py       # Core AI analysis (SerpAPI + OpenRouter)
├── searxng_db.py             # Supabase (legacy) helpers
├── searxng_crawler.py        # Web scraping utilities
├── templates/
│   └── index.html            # Frontend UI (Jinja2 + heavy JS)
├── docs/
│   └── flow-diagram.txt      # Text diagram of data flow
├── requirements.txt
├── Dockerfile
├── fly.toml
└── .env                      # API keys/config
```

## Environment variables (engine)

```bash
OPENROUTER_API_KEY=...   # Required for all LLM calls
SERPAPI_KEY=...          # Required for Google-organic search stacks
SCRAPFLY_KEY=...         # Optional — prefer anti-bot HTML on article fetches
```

Other `.env` keys (**`XANO_*`**, Supabase remnants) support **storage / SPA integration** (`/analyze` may still merge persisted rows on the server) — **not needed** to understand the search + extraction engine in isolation.


## Individual Location Extraction (AI-Powered)

The system uses AI to analyze SerpAPI search results for accurate individual location extraction:

1. **Search Query Construction**: `"{Person Name}" {Company} {Position} location`
   - Example: `"Daniel Maguire" London Stock Exchange Group plc CEO location`

2. **SerpAPI Results**: Fetches top 10 organic results via `get_raw_serpapi_results_for_person_location()`

3. **AI Analysis** (`_extract_location_from_serpapi_with_ai()`):
   - **Critical distinction**: Finds **individual's personal location**, NOT company headquarters
   - Prioritizes LinkedIn profile titles: `"Name - City, State, Country"` format
   - Ignores company addresses, "c/o" mailing addresses, office locations
   - Uses OpenRouter LLM to intelligently parse context from search results

4. **Location Normalization** (`_normalize_location_with_ai()`):
   - Converts "Greater X Area" → main city (e.g., "Greater Chicago Area" → "Chicago, Illinois, United States")
   - Expands abbreviations (CA → California, UK → United Kingdom)
   - Handles various formats: "City, Country", "City, State, Country", etc.

5. **Fallback**: If SerpAPI fails, falls back to regex-based extraction from LinkedIn SEO snippets.

## Performance / behavior (engine)

- **Parallelism**: `/analyze` runs overview, events, management in one pool; wall clock ≈ slowest lane + ownership pass.
- **SerpAPI batching**: `_run_parallel_searches`/`serpapi_parallel_search` issues many queries in aiohttp batches (~0.3s delay between batches — rate limiting).
- **`openrouter_chat`** default timeout is **20s** per call; corporate-event extraction elsewhere may use longer client timeouts — tune for provider tail latency.
- **Scrapfly / direct GET**: enrichment tries Scrapfly when keys exist; bot-protected pages may still return thin HTML.
- **`enrich_ai_events_with_llm`** exists but **`/analyze` does not call it** (no automatic per-URL article crawl on full run).

---

## Plain-English workflows (engine only — rough time shares)

**How to read the percentages:** rounded, illustrative; parallel lanes mean you wait mostly on the **slowest** branch, not the sum of lines.

### 1) Full company run (`POST /analyze`)

**Setup / JSON merge (~3–8%)** → **three parallel lanes (~85–93% total wall clock, dominated by slowest):**

- **Overview:** Wikipedia/fallback Serp context, Yahoo if public, press-path probes, financial Serp bursts → **Sonar** (or fallback) structured markdown → **`generate_description`** → optional **ownership** LLM.
- **Events:** **`detect_company_type`** → **large parallel Serp packs** (startup vs enterprise) → **single large Claude parse** of all snippets.
- **People:** Wiki + leadership Serp → **Sonar** JSON list → possible **Claude fallback** → **per-executive LinkedIn searches**.

Then **light overlap scoring** if the request included a list of existing event titles (~**1–3%**) → **JSON assembly** (~**1–4%**).

### 2) Person location (`POST /api/search_individual_location`)

Serp (~**35–45%**) → LLM “which line is *personal* HQ?” (~**35–45%**) → LLM normalize geography (~**15–25%**).

### 3) Person LinkedIn (`POST /api/search_individual_linkedin`)

Search (~**40–50%**) → snippet parsing / small model assists (~**50–60%**).

### 4) Smart enrich (`POST /smart_enrich_event`)

Fetch article (~**25–40%**) → heuristics (~**15–25%**) → LLM table fill (~**25–35%**) → optional **`ai_enrich_single_event`** escalation (another **large chunk** when triggered).

### 5) HQ suggest (`POST /api/search_company_headquarters`)

Cheap table path if US city known (**~instant**); else **`search_company_headquarters`** (**most of wait**).

### 6) Deep / API-only enrich paths

**`/enrich_event`**, **`/extract_event_meta`**, **`/ai_extract_event_from_url`** — same family as above: **fetch + rules ± LLM** depending on route (see HTTP table).

### 7) Browser-only dedupe

Name/LinkedIn fuzzy merge in JS — **negligible** vs network/LLM above.

---

## Appendix: non-engine FastAPI routes

| Method | Route | Role (not research) |
|--------|-------|---------------------|
| `GET` | `/` | Static HTML shell |
| `POST` | `/refresh_db` | Reload stored snapshot — **no search/LLM** |
| `GET` | `/investors_search` | Proxy list API |
| `POST` | `/investors_create` | Proxy create API |

Persistence/Catalog UI traffic that never hits these routes is outside this document.
