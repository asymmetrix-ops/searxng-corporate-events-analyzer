# SearXNG Corporate Events Analyzer - Architecture

## High-Level System

- **Frontend (`templates/index.html`)**: single-page UI for AI vs DB comparison, data entry, and enrichment controls.
- **Backend (`server.py`)**: FastAPI API that orchestrates Xano lookups, AI extraction, heuristic parsing, optional Scrapfly fetching, and response assembly.
- **Analyzer (`searxng_analyzer.py`)**: legacy pipeline for search + AI extraction (SerpAPI + OpenRouter) used by `/analyze`.
- **Data store**: Xano endpoints for companies, events, investors, locations, roles, sectors, business focuses, currencies, and individuals.

## Architecture Diagram (current flow)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (SPA)                             │
│                         templates/index.html                            │
│  - Analyze form (URL or name)                                           │
│  - AI vs DB overview + events + management                              │
│  - Catalog dropdowns (sectors, roles, business focus, currency)         │
│  - Buttons: Parse (heuristic), Enrich (LLM), Check DB, Add to DB        │
└─────────────────────────────────────────────────────────────────────────┘
                                 │  POST /analyze (query)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            FASTAPI SERVER (server.py)                   │
│ 1) Xano pre-check: GET /get_company_by_url                              │
│    └─ If found: GET /Get_new_company/{id} (overview, events, catalogs)  │
│ 2) AI analysis (searxng_analyzer): SerpAPI + OpenRouter → AI overview   │
│    and AI events                                                        │
│ 3) Evidence Enrichment Layer (auto) for AI events with source_url:      │
│    ├─ fetch_html (Scrapfly if key else requests)                        │
│    ├─ strip boilerplate; heuristic dates/amount/stage/currency          │
│    └─ ai_extract_event_from_text (LLM JSON) → merge                     │
│ 4) Matching: keyword/Jaccard AI vs DB events                            │
│ 5) Response JSON → frontend                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                 │  JSON (existing_company, db/ai data, matches)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND RENDER                            │
│  - Side-by-side AI vs DB cards                                          │
│  - Event actions: Parse, Enrich, Check DB (website-only), Add to DB     │
│  - Overview copy/edit/create; management add; API log                   │
└─────────────────────────────────────────────────────────────────────────┘

External services:
- Xano (companies, events, investors, locations, sectors, roles, business focuses, currencies, individuals)
- OpenRouter AI (Claude 3.5 Sonnet, Perplexity, GPT-4o-mini)
- SerpAPI (search)
- Scrapfly (HTML fetch, optional)
- Wikipedia API (overview fallback)
```

## End-to-End Flow (current)

1) **Input**: User submits company URL or name → `POST /analyze`.
2) **Xano pre-check**: `GET /get_company_by_url`; if found, fetch full payload via `GET /Get_new_company/{id}` (includes overview, events, investors, locations, roles, sectors, business_focus, currencies).
3) **AI analysis**: `searxng_analyzer` generates AI overview + AI corporate events (SerpAPI + OpenRouter).
4) **Evidence Enrichment Layer (auto in /analyze)**:
   - For AI events with `source_url`, fetch HTML (Scrapfly if `SCRAPFLY_KEY`, else direct GET).
   - Strip boilerplate marketing text; heuristic parse dates, amounts, currency, stage.
   - LLM extraction (`ai_extract_event_from_text`) returns minified JSON with title, dates, deal type/status, description, amount (millions), ISO currency, funding stage, counterparties.
   - Merge heuristic + LLM fields into AI events and return to frontend (not auto-saved).
5) **Matching**: Keyword/Jaccard compare AI vs DB events; mark matched vs missing.
6) **Response**: JSON includes `existing_company`, `db_company`, `db_overview`, `db_events`, `ai_overview`, `ai_events` (enriched), `missing_events`, `matched_events`, `top_management`.
7) **Frontend render**: Side-by-side AI vs DB, per-event cards with add/edit/save controls, catalogs as dropdowns, copy buttons, and enrichment/parse actions.

## Evidence Enrichment Layer (details)

- **Fetch**: `fetch_html(url)` uses Scrapfly POST (url in params + body) when key exists; fallback `requests.get`.
- **Heuristics**: `extract_first_date` (supports YYYY-MM-DD, dd/mm/yyyy, dd.mm.yyyy), `extract_investment_fields` (amount, currency symbol→ISO, funding stage regexes).
- **LLM**: `ai_extract_event_from_text(url, text)` with strict minified JSON schema to reduce parse errors.
- **Batch**: `enrich_ai_events_with_llm(ai_events)` runs during `/analyze` for events with `source_url`.
- **Manual single enrichment**: `POST /enrich_event` for a specific event card (frontend “🧪 Enrich Event” button).
- **Note**: Scrapfly/LLM can be disabled by omitting keys; fallback still returns heuristic-only enrichment.

## Frontend Behavior (index.html)

- **Catalog dropdowns** (cached per session):
  - **Sectors**: multi-select with synonym map (e.g., “Environmental Services”→“Environment”).
  - **Roles**: individuals use multi-select roles + title.
  - **Business Focus**: dropdown sourced from Xano `business_focuses` (ids like 74=Financial Services, 75=Data & Analytics, ...).
  - **Currencies**: dropdown for corporate events (ISO codes, mapped to Xano currency_id).
- **Corporate event cards**:
  - Fields: description, long description, announcement/closed dates, deal type/status, funding stage, investment amount (m), currency, source URL.
  - Buttons: “🧠 Parse from source” (heuristic), “🧪 Enrich Event” (LLM+heuristic), “🔍 Check DB” (counterparty).
  - Add-to-DB payload includes `currency_id`, `funding_stage`, `Amount`, `funding_source`, `investment_amount_m`.
- **Counterparty DB check**:
  - Uses only website URL (typed input has priority; otherwise counterparty website; otherwise origin of announcement URL). Looks up via Xano; no LinkedIn fallback.
- **Overview editing**:
  - Copy DB→AI, create new company via `post_company_overview`.
  - Description textarea, editable name/location/website/linkedin.
- **Management**:
  - Renders AI/DB executives; add individual via `post_individual` with locations lookup.
- **Derived DB data safety**:
  - `runAnalyze` initializes derived DB overview/management/events even if prior cache was empty.

## Backend Endpoints (server.py)

- `POST /analyze`: orchestrates Xano pre-check, AI analysis, enrichment, matching, and response assembly.
- `POST /enrich_event`: single-event enrichment (Scrapfly/direct fetch + LLM + heuristics).
- `POST /extract_event_meta`: heuristic extraction from provided HTML/text (used by “Parse from source”).
- `POST /ai_extract_event_from_url`: legacy AI extraction from URL (LLM over fetched HTML).
- `GET /health`: basic healthcheck.

## Data Mapping & Lookups

- **Locations**: `get_location` Xano lookup; `MAX_LOCATION_ID` constraint removed—any numeric id allowed.
- **Sectors**: lookup by name with synonym map; multi-select stored as array of IDs.
- **Business focus**: single select, sourced from Xano list (ids 74–110+).
- **Roles**: multi-select per individual (title + role id array).
- **Currencies**: fetched from Xano catalog; stored as `currency_id` in events; UI shows ISO code.
- **Company fetch**: `GET /Get_new_company/{id}` returns full company object, events, investors, locations, sectors, business focus, linkedin data, employees history.

## External Services

- **SerpAPI**: search queries for company/event discovery (startup vs enterprise query sets).
- **OpenRouter AI**: multiple models; primary for events (Claude 3.5 Sonnet) and summaries (Perplexity/others).
- **Scrapfly**: optional HTML fetch with anti-bot support; POST with url in params + body; fallback to direct GET.
- **Wikipedia API**: summary fallback for overview.
- **Xano**: system-of-record for companies/events/individuals/catalogs.

## Data Flow (updated)

```
User Input → /analyze
   ├─ Xano pre-check (company exists?)
   │    └─ If found: fetch full company payload (overview, events, catalogs)
   ├─ AI analysis (SerpAPI + OpenRouter) → AI overview + AI events
   ├─ Evidence Enrichment Layer on AI events with source_url
   │    ├─ fetch_html (Scrapfly/direct)
   │    ├─ strip boilerplate, heuristic dates/amount/stage/currency
   │    └─ ai_extract_event_from_text (LLM JSON) → merge
   ├─ Match AI events vs DB events (keyword/Jaccard)
   └─ Return combined JSON → frontend render (AI vs DB cards, actions)
```

## UI Rendering Snapshot

- Side-by-side AI vs DB overview (editable AI fields, copy-from-DB, create-in-DB).
- Events: matched/missing markers, enrichment/parse buttons, currency/funding-stage fields, counterparties with DB check, add-to-DB.
- Management: AI + DB executives with add-to-DB.
- API log panel with step-by-step messages.

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

## Environment Variables

```bash
OPENROUTER_API_KEY=...      # AI extraction/enrichment
SERPAPI_KEY=...             # Search
XANO_BASE_URL=https://xdil-abvj-o7rq.e2.xano.io
SCRAPFLY_KEY=...            # Optional; enables Scrapfly fetch
# (Supabase legacy)
SUPABASE_URL=...
SUPABASE_KEY=...
```

## Performance / Behavior Notes

- Catalogs cached client-side; fetched once per session when possible.
- `MAX_LOCATION_ID` removed—accept any numeric location id from Xano.
- LLM prompt enforces minified JSON to reduce parse errors.
- Scrapfly can fail on protected pages; fallback GET still runs; enrichment may be partial.
- Counterparty DB check relies solely on website URL (or origin of announcement URL).

