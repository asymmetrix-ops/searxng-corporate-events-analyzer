# SearXNG Corporate Events Analyzer - Architecture

## High-Level System

- **Frontend (`templates/index.html`)**: single-page UI for AI vs DB comparison, data entry, enrichment controls, and profile-scope options.
- **Backend (`server.py`)**: FastAPI API that orchestrates Xano lookups, AI extraction, heuristic parsing, optional Scrapfly fetching, profile-scope filtering, and response assembly.
- **Analyzer (`searxng_analyzer.py`)**: legacy pipeline for search + AI extraction (SerpAPI + OpenRouter) used by `/analyze`.
- **Data store**: Xano endpoints for companies, events, investors, locations, roles, sectors, business focuses, currencies, and individuals.

## Architecture Diagram (current flow)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (SPA)                             │
│                         templates/index.html                            │
│  - Analyze form (URL or name) + profile scope toggles                   │
│  - AI vs DB overview + events + management + counterparties             │
│  - Catalog dropdowns (sectors, roles, business focus, currency)         │
│  - Buttons: Parse (heuristic), Enrich (LLM), Check DB, Add to DB        │
│  - Individual location search (📍) for management & counterparties      │
│  - Fuzzy name matching across individuals                                │
└─────────────────────────────────────────────────────────────────────────┘
                                 │  POST /analyze (query + options)
                                 │  POST /api/search_individual_location
                                 │  POST /api/search_individual_linkedin
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
│                                                                          │
│ Individual Location Extraction:                                         │
│  - get_raw_serpapi_results_for_person_location() → SerpAPI            │
│  - _extract_location_from_serpapi_with_ai() → OpenRouter LLM           │
│    (Analyzes organic results, distinguishes personal vs company location)│
│  - _normalize_location_with_ai() → Normalize format                     │
└─────────────────────────────────────────────────────────────────────────┘
                                 │  JSON (existing_company, db/ai data, matches)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND RENDER                            │
│  - Side-by-side AI vs DB cards                                          │
│  - Event actions: Parse, Enrich, Check DB (website-only), Add to DB     │
│  - Overview copy/edit/create; management add; API log                   │
│  - Counterparty cards with individual linking                            │
│  - Individual cross-referencing (fuzzy matching, data sharing)           │
│  - refreshXanoData() after saves to reflect changes immediately          │
└─────────────────────────────────────────────────────────────────────────┘

External services:
- Xano (companies, events, investors, locations, sectors, roles, business focuses, currencies, individuals, counterparties)
- OpenRouter AI (Claude 3.5 Sonnet, Perplexity, GPT-4o-mini) - for events, location extraction, normalization
- SerpAPI (search, individual location search)
- Yahoo Finance via `yfinance` (public-company EV, revenue, EBITDA, holders)
- Scrapfly (HTML fetch, optional)
- Wikipedia API (overview fallback)
```

## End-to-End Flow (current)

1) **Input**: User submits company URL or name plus profile-scope options → `POST /analyze`.
2) **Xano pre-check**: `GET /get_company_by_url`; if found, fetch company payload via `GET /Get_new_company/{id}` for selected profile fields. DB corporate events are fetched only when corporate events are included.
3) **AI analysis**: `searxng_analyzer` generates only the selected AI sections (overview, corporate events, individuals/key people). Unselected sections are skipped before search/LLM work starts.
4) **Evidence Enrichment Layer (auto in /analyze)**:
   - For AI events with `source_url`, fetch HTML (Scrapfly if `SCRAPFLY_KEY`, else direct GET).
   - Strip boilerplate marketing text; heuristic parse dates, amounts, currency, stage.
   - LLM extraction (`ai_extract_event_from_text`) returns minified JSON with title, dates, deal type/status, description, amount (millions), ISO currency, funding stage, counterparties.
   - Merge heuristic + LLM fields into AI events and return to frontend (not auto-saved).
5) **Matching**: Keyword/Jaccard compare AI vs DB events; mark matched vs missing.
6) **Response**: JSON includes `existing_company`, selected DB/AI sections, `missing_events`, `matched_events`, `top_management`, and the resolved `options`. If counterparties are excluded, counterparty/advisor payloads are stripped from event results.
7) **Frontend render**: Side-by-side AI vs DB, per-event cards with add/edit/save controls, catalogs as dropdowns, copy buttons, and enrichment/parse actions.
8) **Individual location search** (on-demand via 📍 button):
   - User clicks location search for an individual → `POST /api/search_individual_location`
   - Backend: `get_raw_serpapi_results_for_person_location()` → SerpAPI search
   - Backend: `_extract_location_from_serpapi_with_ai()` → LLM analyzes results, extracts personal location (not company HQ)
   - Backend: `_normalize_location_with_ai()` → Normalizes format (handles "Greater X Area", abbreviations)
   - Frontend: Auto-fills location input field
9) **Individual cross-referencing** (automatic):
   - `ensureCanonicalPeopleCache()` builds maps of all individuals (AI + DB)
   - `canonicalizePersonFromKeyPeople()` matches individuals by LinkedIn URL, exact name, or fuzzy similarity
   - Matched individuals share location and bio data across main management and counterparty sections
10) **Counterparty individual linking**:
    - Individuals created from counterparty cards are linked to counterparty's `company_id`
    - Pending link queue (`pendingIndividualIds`) auto-links individuals after counterparty creation
    - `refreshXanoData()` called after all saves to immediately reflect changes

## Evidence Enrichment Layer (details)

- **Fetch**: `fetch_html(url)` uses Scrapfly POST (url in params + body) when key exists; fallback `requests.get`.
- **Heuristics**: `extract_first_date` (supports YYYY-MM-DD, dd/mm/yyyy, dd.mm.yyyy), `extract_investment_fields` (amount, currency symbol→ISO, funding stage regexes).
- **LLM**: `ai_extract_event_from_text(url, text)` with strict minified JSON schema to reduce parse errors.
- **Batch**: `enrich_ai_events_with_llm(ai_events)` runs during `/analyze` for events with `source_url`.
- **Manual single enrichment**: `POST /enrich_event` for a specific event card (frontend “🧪 Enrich Event” button).
- **Note**: Scrapfly/LLM can be disabled by omitting keys; fallback still returns heuristic-only enrichment.

## Frontend Behavior (index.html)

- **Catalog dropdowns** (cached per session):
  - **Sectors**: multi-select with synonym map (e.g., "Environmental Services"→"Environment").
  - **Roles**: individuals use multi-select roles + title.
  - **Business Focus**: dropdown sourced from Xano `business_focuses` (ids like 74=Financial Services, 75=Data & Analytics, ...).
  - **Currencies**: dropdown for corporate events (ISO codes, mapped to Xano currency_id).
- **Corporate event cards**:
  - Fields: description, long description, announcement/closed dates, deal type/status, funding stage, investment amount (m), currency, source URL.
  - Buttons: "🧠 Parse from source" (heuristic), "🧪 Enrich Event" (LLM+heuristic), "🔍 Check DB" (counterparty).
  - Add-to-DB payload includes `currency_id`, `funding_stage`, `Amount`, `funding_source`, `investment_amount_m`.
- **Counterparty flows**:
  - **DB check**: Uses website URL (typed input has priority; otherwise counterparty website; otherwise origin of announcement URL). Looks up via Xano; no LinkedIn fallback.
  - **Individual linking**: Individuals created from counterparty cards are linked to the counterparty's `company_id` (not main company). Auto-linking queue (`pendingIndividualIds`) links individuals after counterparty creation.
  - **Counterparty creation**: Can be created without individuals; individuals can be added/linked later.
  - **"↔ Use main company" button**: Sets counterparty name to main company overview name.
- **Overview editing**:
  - Copy DB→AI, create new company via `post_company_overview`.
  - Description textarea, editable name/location/website/linkedin, former name, investor IDs, latest investment, revenue, EV, and EBITDA fields.
  - `refreshXanoData()` re-renders all DB panels after saves to reflect changes immediately.
- **Management & Individuals**:
  - Renders AI/DB executives; add individual via `post_individual` with locations lookup.
  - **Location search (📍 button)**: Available for both main management and counterparty individuals. Uses AI-powered SerpAPI analysis to extract individual's personal location (not company HQ).
  - **LinkedIn search**: Searches for LinkedIn profile and extracts location from SEO snippets in one call.
  - **Fuzzy name matching**: Cross-references individuals across main management and counterparties using similarity scoring (handles nicknames, initials, variations). Matched individuals share location and bio data.
  - **Canonical people cache**: Maintains `_canonicalPeopleByLinkedIn` and `_canonicalPeopleByName` maps for fast cross-referencing.
- **Derived DB data safety**:
  - `runAnalyze` initializes derived DB overview/management/events even if prior cache was empty.
  - `refreshXanoData()` ensures all DB sections (overview, individuals, events, counterparties) are re-rendered after any save operation.
- **Profile scope before search**:
  - Checkboxes control whether `/analyze` fetches company profile, corporate events, individuals/key people, and counterparties.
  - Excluding corporate events skips the AI event search and DB event follow-up fetch.
  - Excluding individuals skips top-management search and DB individual follow-up fetch.
  - Counterparties are a child option of corporate events: unchecking corporate events disables/excludes counterparties automatically in the UI and backend.
  - Excluding counterparties while keeping corporate events keeps event cards but removes counterparty/advisor data from AI and DB event payloads.

## Backend Endpoints (server.py)

- `POST /analyze`: orchestrates Xano pre-check, AI analysis, enrichment, matching, and response assembly.
- `POST /enrich_event`: single-event enrichment (Scrapfly/direct fetch + LLM + heuristics).
- `POST /extract_event_meta`: heuristic extraction from provided HTML/text (used by "Parse from source").
- `POST /ai_extract_event_from_url`: legacy AI extraction from URL (LLM over fetched HTML).
- `POST /api/search_individual_linkedin`: search for individual's LinkedIn profile and extract location from SEO snippets. Returns `{linkedin_url, location: {city, state, country}}`.
- `POST /api/search_individual_location`: AI-powered location extraction for individuals. Uses SerpAPI organic results analyzed by LLM to find person's actual location (not company HQ). Returns `{city, state, country}`.
- `POST /api/search_company_hq`: search for company headquarters location.
- `POST /refresh_db`: refresh database data without re-running AI analysis.
- `GET /health`: basic healthcheck.

## Data Mapping & Lookups

- **Locations**: `get_location` Xano lookup; `MAX_LOCATION_ID` constraint removed—any numeric id allowed.
- **Individual locations**: AI-powered extraction via `_extract_location_from_serpapi_with_ai()` that analyzes SerpAPI organic results. Focuses on **individual's personal location** (not company headquarters). Handles "Greater X Area" formats, normalizes to "City, State, Country".
- **Sectors**: lookup by name with synonym map; multi-select stored as array of IDs.
- **Business focus**: single select, sourced from Xano list (ids 74–110+).
- **Roles**: multi-select per individual (title + role id array).
- **Currencies**: fetched from Xano catalog; stored as `currency_id` in events; UI shows ISO code.
- **Company fetch**: `GET /Get_new_company/{id}` returns full company object, events, investors, locations, sectors, business focus, linkedin data, employees history.
- **Company financial metrics**: Overview payload supports `former_name`, `investors` (names from AI/search), `investors_new_company` (IDs), `investment` (`last_investment_amount`, `last_investment_currency`, `last_investment_date`, `last_investment_source`), `revenues` (`revenues_m`, `rev_source`, `revenues_currency`, `years_id`), `ev_data` (`ev_value`, `ev_currency`, `ev_year`, `ev_source`), and `EBITDA` (`EBITDA_m`, `EBITDA_source`, `EBITDA_currency`, `EBITDA_year`).
- **Individual cross-referencing**: Fuzzy name matching algorithm (`nameSimilarity()`) matches individuals by:
  - Exact name match (normalized)
  - LinkedIn URL match (most reliable)
  - Last name + first name similarity (handles nicknames, initials, variations)
  - Similarity threshold: ≥0.8 for matching

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
   │    └─ If found: fetch full company payload (overview, events, catalogs, counterparties)
   ├─ AI analysis (SerpAPI + OpenRouter) → AI overview + AI events
   ├─ Evidence Enrichment Layer on AI events with source_url
   │    ├─ fetch_html (Scrapfly/direct)
   │    ├─ strip boilerplate, heuristic dates/amount/stage/currency
   │    └─ ai_extract_event_from_text (LLM JSON) → merge
   ├─ Match AI events vs DB events (keyword/Jaccard)
   └─ Return combined JSON → frontend render (AI vs DB cards, actions)

Individual Location Search (on-demand):
   User clicks 📍 → POST /api/search_individual_location
   ├─ get_raw_serpapi_results_for_person_location() → SerpAPI
   ├─ _extract_location_from_serpapi_with_ai() → OpenRouter LLM
   │    └─ Analyzes organic results, distinguishes personal vs company location
   ├─ _normalize_location_with_ai() → Normalize format
   └─ Return {city, state, country} → Frontend auto-fills location input

Individual Cross-Referencing (automatic):
   Frontend: ensureCanonicalPeopleCache()
   ├─ Build _canonicalPeopleByLinkedIn map
   ├─ Build _canonicalPeopleByName map
   └─ When rendering individuals:
        ├─ Try LinkedIn URL match (most reliable)
        ├─ Try exact name match
        └─ Try fuzzy name match (nameSimilarity ≥0.8)
             └─ Share location & bio data between matched individuals

Counterparty Individual Linking:
   Individual created from counterparty card
   ├─ If counterparty exists in DB → Link immediately
   └─ If counterparty not yet created → Add to pendingIndividualIds queue
        └─ After counterparty creation → Auto-link all pending individuals
```

## UI Rendering Snapshot

- **Side-by-side AI vs DB overview** (editable AI fields, copy-from-DB, create-in-DB).
- **Events**: matched/missing markers, enrichment/parse buttons, currency/funding-stage fields, counterparties with DB check, add-to-DB.
- **Management**: AI + DB executives with add-to-DB, location search (📍), LinkedIn search, fuzzy matching indicators.
- **Counterparties**: 
  - Counterparty cards with company name, role, website, LinkedIn
  - "🔍 Check DB" button for counterparty company lookup
  - "↔ Use main company" button to copy main company name
  - Individual rows within counterparty cards with location search
  - Auto-linking queue for individuals created before counterparty
- **API log panel** with step-by-step messages.
- **Data refresh**: `refreshXanoData()` re-renders all DB sections after saves (overview, individuals, events, counterparties).

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

## Individual Cross-Referencing & Data Sharing

- **Fuzzy Name Matching**: `nameSimilarity()` algorithm matches individuals across sections:
  - Exact normalized name match
  - LinkedIn URL match (most reliable)
  - Last name match + first name similarity (handles nicknames, initials)
  - Similarity threshold: ≥0.8 required for matching

- **Canonical People Cache**: Maintains two maps:
  - `_canonicalPeopleByLinkedIn`: Keyed by normalized LinkedIn URL
  - `_canonicalPeopleByName`: Keyed by normalized full name

- **Data Sharing**: When individuals are matched (main management ↔ counterparty individuals):
  - Location data is shared between matched individuals
  - Bio data is shared between matched individuals
  - Ensures consistency across the UI

## Performance / Behavior Notes

- Catalogs cached client-side; fetched once per session when possible.
- `MAX_LOCATION_ID` removed—accept any numeric location id from Xano.
- LLM prompt enforces minified JSON to reduce parse errors.
- Scrapfly can fail on protected pages; fallback GET still runs; enrichment may be partial.
- Counterparty DB check relies solely on website URL (or origin of announcement URL).
- Individual location extraction prioritizes LinkedIn results and distinguishes personal location from company HQ.
- `refreshXanoData()` is called after all DB save operations to immediately reflect changes in UI.
- Counterparty individuals are linked to counterparty's `company_id`, not main company's `company_id`.

