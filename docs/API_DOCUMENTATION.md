# API Documentation (Internal + External Endpoints Used)

This repository exposes a small **internal FastAPI** API (served by `server.py`) and, in the UI (`templates/index.html`), calls a larger set of **external Xano endpoints** directly from the browser. The backend also calls **OpenRouter**, **SerpAPI**, and optionally **Scrapfly**.

This document is intentionally exhaustive: it lists **all HTTP endpoints used by the codebase**, what they’re used for, and the concrete request/response shapes observed in code.

---

## Conventions & Base URLs

### Internal app (FastAPI)

- **Base URL (local)**: `http://localhost:8000`
- **Content type**: JSON for API routes; HTML for `/`
- **Implemented in**: `server.py`

### External Xano (called from browser and backend)

- **Base URL**: `https://xdil-abvj-o7rq.e2.xano.io`
- **UI template variable**: `XANO_BASE` (rendered from FastAPI `/` handler)
- **Important**: Most Xano calls in `templates/index.html` are made **directly from the browser**, so CORS must be enabled on Xano for your deployed origin(s).

### External OpenRouter (server-side, used for LLM extraction/enrichment)

- **Endpoint**: `https://openrouter.ai/api/v1/chat/completions`
- **Auth**: `Authorization: Bearer $OPENROUTER_API_KEY`

### External SerpAPI (server-side, used for web search)

- **Endpoint**: `https://serpapi.com/search`
- **Auth**: `api_key` query param (`$SERPAPI_KEY`)

### External Scrapfly (server-side, optional page fetching)

- **Endpoint**: `https://api.scrapfly.io/scrape`
- **Auth**: `key` query param (`$SCRAPFLY_KEY`)

---

## Environment Variables (observed in code)

- **`OPENROUTER_API_KEY`** or **`OPEN_ROUTER_KEY`**: required by `searxng_analyzer.py` (OpenRouter calls).
- **`SERPAPI_KEY`**: optional (enables richer search; otherwise some paths fall back to Startpage scraping).
- **`SCRAPFLY_KEY`**: optional (enables Scrapfly fetch in `server.py` for difficult pages).
- **`PORT`**: server port (default 8000 when running `server.py` directly).

---

## Error Handling Patterns

### Internal API errors (FastAPI)

Most internal endpoints return JSON with an `"error"` key on failure and use HTTP status codes:

- **400** for missing required fields (e.g., missing `url` / `query` / `event`)
- **500** for unexpected server errors

### Xano API responses (external)

Xano endpoints in this project are treated as successful if:

- The HTTP status is 2xx, and either:
  - A `success` flag is `true`, **or**
  - The endpoint returns an updated row object directly (some endpoints do)

### “Clean payload” behavior in UI

The UI uses helper functions like `cleanPayloadKeepRequired(...)` to:

- Convert empty strings `""` and zeros `0` to `null` (to avoid Xano treating empties as overwrites)
- Preserve specific required fields even if they’re empty-ish (e.g., keep `company_id`, `description`, `company_name`)

This matters for PATCH/POST bodies below: the *logical* payload is shown; your actual request may contain `null` instead of `""` / `0`.

---

## Internal API (FastAPI) — All Endpoints

### GET `/`

- **Purpose**: Serves the HTML UI (`templates/index.html`) and injects `xano_base_url` for browser Xano calls.
- **Response**: HTML (`text/html`)

#### Template variables

- **`xano_base_url`**: set to Xano base `https://xdil-abvj-o7rq.e2.xano.io`

---

### POST `/analyze`

- **Purpose**: Main orchestration endpoint for the UI.
  - Pre-check company in Xano by URL
  - Fetch DB company + corporate events if company exists
  - Run AI analysis in parallel (overview, events, management)
  - Return combined payload to render left (AI) and right (DB) panels
- **Request JSON**

```json
{
  "query": "https://example.com/" 
}
```

- **Responses**
  - **200 JSON**:

```json
{
  "existing_company": { "id": 123, "...": "..." } ,
  "db_company": { "...": "..." } ,
  "db_overview": {
    "name": "",
    "city": "",
    "country": "",
    "ownership": "",
    "website": "",
    "linkedin": "",
    "description": "",
    "year_founded": "",
    "primary_business_focus": "",
    "primary_business_focus_id": 0,
    "sectors": [ { "sector": "Software", "id": 1, "importance": "Primary" } ],
    "webpage_monitored": ""
  },
  "ai_overview": {
    "name": "",
    "city": "",
    "country": "",
    "ownership": "",
    "website": "",
    "linkedin": "",
    "description": ""
  },
  "db_events": [ { "...": "..." } ],
  "ai_events": [ { "...": "..." } ],
  "missing_events": [ { "...": "..." } ],
  "matched_events": [ { "...": "..." } ],
  "top_management": [ { "...": "..." } ],
  "db_management": [ { "...": "..." } ]
}
```

  - **400 JSON** if `query` missing:

```json
{ "error": "Missing 'query' field" }
```

#### Notes

- The UI further augments DB panels by calling Xano directly (see Xano section), especially for `company_overview`, `company_individuals`, and `company_events`.

---

### POST `/refresh_db`

- **Purpose**: Refreshes **DB-only** data (no AI re-analysis). Used by the UI when it wants to reload Xano state after POST/PATCH operations.
- **Request JSON**

```json
{ "query": "https://example.com/" }
```

- **Response 200 JSON** (DB-only structure)

```json
{
  "existing_company": { "id": 123, "...": "..." },
  "db_company": { "...": "..." },
  "db_overview": { "...": "..." },
  "ai_overview": null,
  "db_events": [ { "...": "..." } ],
  "ai_events": [],
  "missing_events": [],
  "matched_events": [],
  "top_management": [],
  "db_management": [ { "...": "..." } ]
}
```

- **400 JSON** if `query` missing:

```json
{ "error": "Missing 'query' field" }
```

---

### POST `/smart_enrich_event`

- **Purpose**: “Smart” enrichment for a corporate event based on a source URL.
  - Fetches the page (Scrapfly-first if configured, else direct GET)
  - Extracts dates via structured data + context heuristics
  - Extracts investment fields via regex heuristics
  - Runs LLM extraction
  - If incomplete, performs additional web enrichment and merges results
  - Validates/sanitizes date logic
- **Request JSON**

```json
{
  "url": "https://example.com/press-release",
  "event": {
    "title": "optional hint",
    "company": "optional hint",
    "counterparties": [],
    "announcement_date": "optional",
    "deal_type": "optional"
  }
}
```

- **Response 200 JSON**

```json
{
  "enriched_event": {
    "title": "",
    "announcement_date": "YYYY-MM-DD",
    "closed_date": "YYYY-MM-DD",
    "deal_type": "",
    "deal_status": "",
    "long_description": "",
    "investment_amount_m": 200.0,
    "investment_currency": "USD",
    "funding_stage": "Series A",
    "counterparties": []
  },
  "source": "smart_enrich"
}
```

- **400 JSON** if `url` missing:

```json
{ "error": "Missing url" }
```

---

### POST `/enrich_event`

- **Purpose**: Enriches a provided event using server-side evidence extraction + LLM (`ai_enrich_single_event`).
- **Request JSON**

```json
{
  "event": {
    "source_url": "https://example.com/press-release",
    "title": "hint",
    "company": "hint",
    "counterparties": ["Investor A", "Target B"]
  }
}
```

- **Response 200 JSON**

```json
{
  "enriched_event": {
    "title": "",
    "announcement_date": "YYYY-MM-DD",
    "closed_date": "YYYY-MM-DD",
    "deal_type": "",
    "deal_status": "",
    "long_description": "",
    "amount": 200.0,
    "currency": "USD",
    "amount_status": "disclosed",
    "amount_confidence": 0.9,
    "amount_source_type": "company_pr",
    "amount_source_url": "https://...",
    "stage": "Series A",
    "stage_status": "disclosed",
    "parties": [{ "name": "X", "role": "Investor" }],
    "evidence_links": ["https://..."],
    "evidence_summary": "",
    "enrichment_version": 1
  }
}
```

- **400 JSON** if `event` missing:

```json
{ "error": "Missing event" }
```

---

### POST `/ai_extract_event_from_url`

- **Purpose**: Fetch a page and ask the LLM to extract a structured “corporate event” object.
- **Request JSON**

```json
{ "url": "https://example.com/press-release" }
```

- **Response 200 JSON** (LLM-extracted event)

```json
{
  "title": "",
  "announcement_date": "",
  "closed_date": "",
  "deal_type": "",
  "deal_status": "",
  "long_description": "",
  "investment_amount_m": null,
  "investment_currency": "",
  "funding_stage": "",
  "investment_amount_source": "",
  "source_url": "",
  "counterparties": [
    { "name": "", "role": "", "website": "", "linkedin": "" }
  ]
}
```

---

### POST `/extract_event_meta`

- **Purpose**: Lightweight extraction of:
  - page title (`h1` then `<title>` then OG/meta)
  - best-effort announcement date (structured + context date extraction)
  - long description (first meaningful paragraphs)
  - investment heuristics (funding stage, amount, currency)
- **Request JSON**

```json
{ "url": "https://example.com/press-release" }
```

- **Response 200 JSON**

```json
{
  "title": "Page title",
  "announcement_date": "YYYY-MM-DD",
  "long_description": "Paragraphs...\n\nMore...",
  "funding_stage": "Series A",
  "investment_amount_m": 200.0,
  "investment_currency": "USD"
}
```

---

### POST `/api/search_company_headquarters`

- **Purpose**: Returns HQ city/state/country. Also supports a “state lookup only” fast path if caller already has `city` + `country`.
- **Request JSON**

```json
{
  "company": "Acme Corp",
  "website": "https://acme.com",
  "city": "San Francisco",
  "country": "USA"
}
```

- **Response 200 JSON**

```json
{ "city": "San Francisco", "state": "California", "country": "USA" }
```

---

### POST `/api/search_individual_linkedin`

- **Purpose**: Finds a LinkedIn URL for a person and also tries to extract location from SEO snippets; may normalize location via OpenRouter.
- **Request JSON**

```json
{ "name": "John Smith", "company": "Acme", "position": "CFO" }
```

- **Response 200 JSON**

```json
{
  "linkedin_url": "https://linkedin.com/in/...",
  "location": { "city": "", "state": "", "country": "" },
  "query": "John Smith CFO Acme linkedin"
}
```

---

### POST `/api/search_individual_location`

- **Purpose**: Gets likely location for a person using SerpAPI raw results + OpenRouter extraction (fallback to regex method if no SerpAPI results).
- **Request JSON**

```json
{
  "name": "Mary Meeker",
  "company": "Kleiner Perkins",
  "position": "Partner",
  "linkedin_url": "https://linkedin.com/in/..."
}
```

- **Response 200 JSON**

```json
{ "city": "", "state": "", "country": "" }
```

---

## External Xano API (Browser + Backend) — All Endpoints Used

This section lists the Xano endpoints referenced in:

- `templates/index.html` (browser calls)
- `server.py` and `app.py` (server calls)

### Xano “catalog” / lookup endpoints

#### GET `/api:8Bv5PK4I/get_sectors`

- **Purpose**: Fetch full sectors catalog for UI dropdowns (“Sectors”).
- **Request**: no params
- **Response (expected array)**: each row has at least `id` and `sector_name` (or `name`).

#### GET `/api:8Bv5PK4I/sectors_lookup?sector_name=...`

- **Purpose**: Resolve sector name → sector ID (used when an AI sector name doesn’t match catalog exactly).
- **Query params**
  - `sector_name` (string)
- **Response (expected object)**: `{ "id": <number>, ... }` if found

#### GET `/api:8Bv5PK4I/job_titles_list`

- **Purpose**: Fetch job titles catalog for “Position” dropdowns.
- **Response (expected array)**: objects like `{ id, job_title }` (or `title`/`name`)

#### GET `/api:8Bv5PK4I/job_title_lookup?job_title=...`

- **Purpose**: Resolve job title text → job title ID(s) (used when creating individuals and mapping positions).
- **Query params**
  - `job_title` (string)
- **Response (expected object)**: `{ "id": <number>, ... }`

#### GET `/api:8Bv5PK4I/counterparty_types`

- **Purpose**: Fetch counterparty type catalog (Target / Acquirer / etc.)
- **Response (expected array)**: objects with `{ id, name }`

#### GET `/api:8Bv5PK4I/currency_lookup`

- **Purpose**: Fetch currency catalog for currency selection.
- **Response (expected array)**: (shape depends on Xano), used by UI to match currency codes

#### GET `/api:8Bv5PK4I/business_focuses`

- **Purpose**: Fetch business focus catalog for dropdowns.
- **Response (expected array)**: objects like `{ id, business_focus }` (or `name`)

#### GET `/api:8Bv5PK4I/business_focus_lookup?business_focus_title=...`

- **Purpose**: Resolve business focus name → ID.
- **Query params**
  - `business_focus_title` (string)
- **Response (expected object)**: `{ "id": <number>, ... }`

#### GET `/api:8Bv5PK4I/years_lookup?year=...`

- **Purpose**: Resolve “year founded” (e.g., `"2012"`) → internal year record ID.
- **Query params**
  - `year` (string or number)
- **Response**: `{ "id": <number>, ... }`

#### GET `/api:8Bv5PK4I/get_location?city=...&state=...&country=...`

- **Purpose**: Resolve a location tuple → `locations_id`.
- **Query params**: `city`, `state`, `country` (strings; may be empty)
- **Response**: several shapes are handled by UI:
  - `{ "id": <number> }`
  - `{ "locations_id": <number> }`
  - `{ "location": { "id": <number> } }`
  - `{ "_locations": { "id": <number> } }`

---

### Xano company overview / hydration endpoints (DB panels)

#### GET `/api:8Bv5PK4I/Get_new_company/{company_id}`

- **Purpose**: Fetch “full company” record by ID. Used by:
  - UI refresh via direct Xano fetch (with cache-buster)
  - `server.py` DB fetch (`get_company_by_id`)
- **Path params**
  - `{company_id}` (integer)

#### GET `/api:8Bv5PK4I/get_company_by_url?website_url=...`

- **Purpose**: “Does company exist?” pre-check.
- **Query params**
  - `website_url` (string)
- **Response**: code handles both:
  - list: `[{"id":..., "name":..., "url":...}]`
  - object: `{...}`
  - `null` / `{}` for not found

#### GET `/api:8Bv5PK4I/company_overview?company_id=...`

- **Purpose**: UI uses this as authoritative normalized DB overview.
- **Query params**
  - `company_id` (integer)
  - `_t` (timestamp cache-buster; UI adds it)
- **Response**: UI expects an array; uses `json[0]`.
- **Company financial/profile fields consumed by UI**:
  - `former_name`
  - `investors` as comma-separated investor names when available from AI/search
  - `investors_new_company` as an array of investor IDs
  - `investment`: `{ "last_investment_amount": "", "last_investment_currency": 15, "last_investment_date": "YYYY-MM-DD", "last_investment_source": "https://..." }`
  - `revenues`: `{ "revenues_m": "", "rev_source": "https://...", "revenues_currency": 15, "years_id": 77 }`
  - `ev_data`: `{ "ev_value": "", "ev_currency": 15, "ev_year": 77, "ev_source": "https://..." }`
  - `EBITDA`: `{ "EBITDA_m": "", "EBITDA_source": "https://...", "EBITDA_currency": 15, "EBITDA_year": 77 }`

#### PATCH `/api:8Bv5PK4I/edit_company/{company_id}`

- **Purpose**: Update company fields from the UI (including saving business focus, year founded, ownership type, sectors, etc.)
- **Path params**
  - `{company_id}` = `window._dbCompanyId`
- **Request JSON (pattern)**

```json
{
  "new_company_id": 123,
  "updates": {
    "field_name": "new value",
    "ownership_type_id": 5,
    "year_founded": 77,
    "primary_business_focus_id": [74],
    "sectors_id": [1, 2, 3],
    "former_name": "Former Company Name",
    "investors": "Accel, Sequoia Capital",
    "investors_new_company": [101, 102],
    "investment": {
      "last_investment_amount": "50",
      "last_investment_currency": 15,
      "last_investment_date": "2025-01-31",
      "last_investment_source": "https://..."
    },
    "revenues": {
      "revenues_m": "125",
      "rev_source": "https://...",
      "revenues_currency": 15,
      "years_id": 77
    },
    "ev_data": {
      "ev_value": "2500",
      "ev_currency": 15,
      "ev_year": 77,
      "ev_source": "https://..."
    },
    "EBITDA": {
      "EBITDA_m": "25",
      "EBITDA_source": "https://...",
      "EBITDA_currency": 15,
      "EBITDA_year": 77
    }
  }
}
```

##### Concrete examples used in UI

- **Save primary business focus**:
  - Looks up focus ID via `business_focus_lookup`
  - PATCHes `primary_business_focus_id: [focusId]`

- **Save year founded**:
  - Looks up year ID via `years_lookup`
  - PATCHes `year_founded: <yearId>`

- **Save sectors**:
  - Loads sector IDs via `get_sectors` and/or `sectors_lookup`
  - PATCHes `sectors_id: [sectorIds...]`

- **Save company financials**:
  - Uses the currency catalog (`currency_lookup`) for currency IDs
  - Uses `years_lookup` for revenue/EV/EBITDA year IDs when a 4-digit year is entered
  - PATCHes `former_name`, `investors`, `investors_new_company`, `investment`, `revenues`, `ev_data`, and `EBITDA`

---

### Xano corporate events endpoints (company events + single hydration)

#### GET `/api:8Bv5PK4I/company_events?company_id=...`

- **Purpose**: Fetch events list for DB right panel.
- **Query params**
  - `company_id` (integer)
  - `_t` (timestamp cache-buster; UI adds it)
- **Response**: array of event rows.

#### GET `/api:8Bv5PK4I/corporate_event?corporate_events_id=...`

- **Purpose**: Fetch a single event with richer “counterparty” hydration.
  - Used as a fallback when `company_events` is eventually consistent after create/update.
- **Query params**
  - `corporate_events_id` (integer)
- **Response**: object; UI normalizes from:
  - `raw.result1` or `raw.corporate_event` or `raw`

#### POST `/api:8Bv5PK4I/create_corporate_event`

Used in two places in UI:

1) **Create a “new manual event” from the “New Event” form**

```json
{
  "company_id": 123,
  "description": "Event title",
  "announcement_date": "YYYY-MM-DD",
  "closed_date": "YYYY-MM-DD",
  "deal_type": "Acquisition",
  "deal_status": "Completed",
  "source_url": "https://...",
  "long_description": "...",
  "investment_amount_m": 200.0,
  "currency_id": 15,
  "funding_stage": "Series A"
}
```

2) **Create an event from an “AI event card” (“Add to DB”)**

The UI builds a “simplified payload”:

```json
{
  "company_id": 123,
  "description": "Event (short)",
  "long_description": "",
  "announcement_date": "",
  "closed_date": "",
  "deal_type": "Acquisition",
  "deal_status": "Completed",
  "source_url": "",
  "currency_id": 15,
  "funding_stage": "",
  "investment_amount_m": 200.0,
  "funding_source": "https://source-of-amount.example"
}
```

Notes:

- The payload includes both `Amount` and `investment_amount_m` in some paths for backward compatibility.
- The UI then calls `refreshXanoData({ expectedEventId, minDbEvents })` to wait for eventual consistency.

#### PATCH `/api:8Bv5PK4I/edit_corporate_event/{event_id}`

- **Purpose**: Update a corporate event in-place (e.g., toggling publish flag, editing fields).
- **Request JSON**

```json
{
  "event_id": 999,
  "updates": {
    "deal_status": "Completed",
    "ready_to_publish": true
  }
}
```

---

### Xano counterparties endpoints

#### POST `/api:8Bv5PK4I/create_counterparty`

Used in two flows:

1) **Create/link counterparty for an existing event**

```json
{
  "corporate_event_id": 999,
  "company_id": 0,
  "counterparty_type_id": 17,
  "press_release_url": "https://...",
  "company_url": "https://...",
  "company_name": "Counterparty Ltd"
}
```

- The UI may pass a known `company_id` when `check_counterpaty` found it; otherwise `0` to let Xano create/resolve by name.
- The UI avoids sending an empty `individual_role_ids` mapping.

2) **Create counterparties after creating a new manual event**

```json
{
  "corporate_event_id": 999,
  "company_id": 0,
  "company_name": "Counterparty Ltd",
  "counterparty_type_id": 17,
  "company_url": "https://...",
  "linkedin_url": "https://linkedin.com/company/...",
  "press_release_url": "https://source-of-event..."
}
```

#### PATCH `/api:8Bv5PK4I/edit_counterparty/{counterparty_id}`

- **Purpose**: Edit counterparty fields (type, URLs, etc.)
- **Request JSON**

```json
{
  "counterparty_id": 2528,
  "updates": {
    "counterparty_type_id": 17,
    "press_release_url": "https://...",
    "company_url": "https://..."
  }
}
```

---

### Xano “check if counterparty company exists” endpoint

#### GET `/api:8Bv5PK4I/check_counterpaty?linkedin_url=...&company_name=...`

- **Purpose**: Determine whether a counterparty company already exists in DB; if so returns an `id` that the UI uses as `company_id` when creating counterparties.
- **Query params**
  - `linkedin_url` (string; may be empty)
  - `company_name` (string; may be empty)
- **Response (expected)**: object with an `id` when found (plus optional `name`, `company_name`, `website`, `linkedin_url`).

---

### Xano individuals endpoints

#### GET `/api:8Bv5PK4I/individuals/get_by_linkedin?linkedin_url=...&name=...`

- **Purpose**: Find an individual record (and sometimes role ID) by LinkedIn + name.
- **Response**: UI normalizes several shapes, including:
  - `{ result1: {...}, role: [{id: ...}] }`
  - `{ ...row... }`
  - `[ { ...row... } ]`

The UI normalization attempts to produce:

```json
{
  "id": 57,
  "role_id": 1234,
  "name": "",
  "bio": "",
  "linkedin_url": "",
  "locations_id": 0,
  "phone": "",
  "email": ""
}
```

#### POST `/api:8Bv5PK4I/create_individual`

- **Purpose**: Create an individual record (optionally linked to a company, counterparty, advisor) and add job title IDs.
- **Request JSON (logical)**

```json
{
  "name": "Jane Doe",
  "status": "Current",
  "location_text": "London, United Kingdom",
  "bio": "...",
  "linkedin_url": "https://linkedin.com/in/...",
  "linkedin_URL": "https://linkedin.com/in/...",
  "current_employer_url": "https://...",
  "current_employee_url": "https://...",
  "company_id": 123,
  "counterparty_id": 2528,
  "advisor_id": 0,
  "location_id": 999,
  "job_title_ids": [12, 34]
}
```

Notes:

- The UI sends both `linkedin_url` and `linkedin_URL` for compatibility.
- `job_title_ids` is included only if lookup finds IDs.

#### PATCH `/api:8Bv5PK4I/update_individual/{role_id}`

- **Purpose**: Update an individual “role” record (UI calls this “roleId”).
- **Request JSON**

```json
{
  "role_id": 1234,
  "job_title_ids": [12],
  "status": "Current",
  "linkedin_url": "https://...",
  "bio": "..."
}
```

The UI constructs:

- `payload = { role_id: roleId, ...updates }`

#### POST `/api:8Bv5PK4I/assign_individual_to_counterparty/{counterparty_id}`

- **Purpose**: Link an existing individual record to a counterparty (after individual is created or found).
- **Request JSON**

```json
{ "counterparty_id": 2528, "individual_id": 57 }
```

- **Response**: UI expects `{ "success": true, "already_assigned": false }`-like shape.

#### GET `/api:8Bv5PK4I/company_individuals?company_id=...`

- **Purpose**: Fetch normalized management list for DB right panel.
- **Query params**: `company_id`, `_t`
- **Response**: array of individuals with job titles and role IDs; UI normalizes into rows for rendering.

---

### Xano advisors endpoints

#### POST `/api:8Bv5PK4I/create_advisor`

- **Purpose**: Attach an advisor to a corporate event.
- **Request JSON**

```json
{
  "corporate_event_id": 999,
  "advisor_name": "Some Bank",
  "advisor_type": "Financial Advisor",
  "advised_party": "Buyer",
  "announcement_url": "https://..."
}
```

---

### Xano “post company overview” endpoint (create company)

#### POST `/api:8Bv5PK4I/post_company_overview`

- **Purpose**: Create a new company record in DB from the AI panel (when company not found).
- **Request JSON (logical)**

```json
{
  "name": "Company Name",
  "website": "https://...",
  "linkedin": "https://linkedin.com/company/...",
  "description": "...",
  "new_company_id": 0,
  "locations_id": 999,
  "ownership_type_id": 5,
  "year_founded": 77,
  "webpage_monitored": "https://.../press",
  "primary_business_focus_id": [74],
  "sectors_id": [1, 2, 3],
  "former_name": "Former Company Name",
  "investors": "Accel, Sequoia Capital",
  "investors_new_company": [101, 102],
  "investment": {
    "last_investment_amount": "50",
    "last_investment_currency": 15,
    "last_investment_date": "2025-01-31",
    "last_investment_source": "https://..."
  },
  "revenues": {
    "revenues_m": "125",
    "rev_source": "https://...",
    "revenues_currency": 15,
    "years_id": 77
  },
  "ev_data": {
    "ev_value": "2500",
    "ev_currency": 15,
    "ev_year": 77,
    "ev_source": "https://..."
  },
  "EBITDA": {
    "EBITDA_m": "25",
    "EBITDA_source": "https://...",
    "EBITDA_currency": 15,
    "EBITDA_year": 77
  }
}
```

- The UI uses lookup endpoints first:
  - `get_location` → `locations_id`
  - `years_lookup` → `year_founded` ID
  - `business_focus_lookup` → `primary_business_focus_id`
  - `sectors_lookup` / `get_sectors` → `sectors_id`

---

### Xano event ID lookup endpoint (used by UI helper)

#### GET `/api:8Bv5PK4I/query_existing_corporate_event?description=...`

- **Purpose**: Find an existing event by description text (used by UI before adding advisors or edits when event ID not readily available).
- **Query params**
  - `description` (string)
- **Response**: implementation-dependent; UI expects an event ID or “exists”-like result.

---

### Xano endpoints used by backend (server-side)

These are called by `server.py` and/or `app.py`:

#### GET `/api:8Bv5PK4I/get_company_by_url`
See above.

#### GET `/api:8Bv5PK4I/Get_new_company/{company_id}`
See above.

#### GET `/api:y4OAXSVm/Get_investors_corporate_events?new_company_id=...`

- **Purpose**: Fetch corporate events in an alternative Xano API group.
- **Query params**: `new_company_id` (integer)
- **Response**: backend expects an object containing `New_Events_Wits_Advisors` array.

#### POST `/api:617tZc8l/create_corporate_event` (used by `app.py`)

- **Purpose**: Streamlit (`app.py`) test/create corporate event endpoint (different Xano API group).
- **Request JSON**: `event_data` with:
  - `title`, `announcement_date`, `closed_date`, `deal_type`, `deal_status`, `investment_amount`, `currency_id`, `counterparties` list, etc.

#### GET `/api:GYQcK4au/Get_new_company/{company_id}` (used by `app.py`)

- **Purpose**: Streamlit’s DB fetch uses a different Xano API identifier for “Get_new_company”.

---

## External OpenRouter API (server-side)

### POST `https://openrouter.ai/api/v1/chat/completions`

Used by `searxng_analyzer.openrouter_chat(...)` and by `server.py` via that helper.

- **Headers**
  - `Authorization: Bearer <OPENROUTER_API_KEY>`
  - `Content-Type: application/json`
  - `X-Title: <title>` (request label)

- **Request JSON**

```json
{
  "model": "openai/gpt-4o-mini",
  "messages": [
    { "role": "user", "content": "..." }
  ]
}
```

- **Response**: standard OpenAI-style chat completion; code reads:
  - `response.json()["choices"][0]["message"]["content"]`

---

## External SerpAPI (server-side)

### GET `https://serpapi.com/search`

Used in async and sync modes.

- **Query params (typical)**
  - `q`: search query
  - `hl`: `"en"`
  - `gl`: `"us"`
  - `num`: result count
  - `api_key`: `SERPAPI_KEY`
  - `output`: `"json"` (async path)

- **Response**: JSON containing `organic_results` (array). The code extracts `organic_results`.

---

## External Scrapfly (server-side, optional)

### POST `https://api.scrapfly.io/scrape`

Used by `server.py.fetch_html(url, force_scrapfly=True)` when `SCRAPFLY_KEY` is set and `force_scrapfly` is requested.

- **Query params**
  - `key`: `SCRAPFLY_KEY`
  - `url`: target page
  - `render`: `"html"` (render JavaScript)
  - `country`: `"US"`

- **Request JSON**

```json
{ "url": "https://...", "format": "raw" }
```

- **Response JSON**: code reads `data.result.content` as HTML string.

---

## Sector / “Sub-sector” Coverage (what’s actually implemented)

### Sectors

Sectors are fully supported and used throughout the UI:

- Fetch catalog: `GET /api:8Bv5PK4I/get_sectors`
- Lookup ID by name: `GET /api:8Bv5PK4I/sectors_lookup?sector_name=...`
- Persist sector IDs on company: `PATCH /api:8Bv5PK4I/edit_company/{id}` with `updates.sectors_id = [ ...ids ]`

### Sub-sectors

No dedicated “sub-sector” endpoint is referenced in this codebase (no `get_sub_sectors`, `subsectors`, etc.). If your Xano backend has a sub-sector table, you’ll need to add and wire the relevant endpoint(s); once added, it should be documented alongside the sector calls above.

---

## Common End-to-End Flows (from the UI)

### 1) Analyze a company

1. Browser calls internal:
   - `POST /analyze` with `{ "query": "<url or name>" }`
2. Server may call Xano:
   - `GET /get_company_by_url`
   - `GET /Get_new_company/{id}`
   - `GET /Get_investors_corporate_events?new_company_id=...`
3. Server calls OpenRouter/SerpAPI for AI outputs (overview/events/management), and `yfinance` for public-company EV/revenue/EBITDA when a ticker is found.
4. Browser then calls Xano directly to hydrate DB panels:
   - `GET /company_overview?company_id=...`
   - `GET /company_individuals?company_id=...`
   - `GET /company_events?company_id=...`

### 2) Refresh DB after changes

- Browser calls Xano directly (preferred):
  - `GET /Get_new_company/{companyId}?_t=<timestamp>`
- If Xano direct fetch fails, browser falls back to internal:
  - `POST /refresh_db` with `{ "query": "<original query>" }`

### 3) Create event + counterparties + individuals (manual “New Event” form)

1. `POST /api:8Bv5PK4I/create_corporate_event`
2. For each counterparty:
   - `POST /api:8Bv5PK4I/create_counterparty`
3. For each individual:
   - `POST /api:8Bv5PK4I/create_individual`
   - `POST /api:8Bv5PK4I/assign_individual_to_counterparty/{counterparty_id}`
4. UI refresh/hydration:
   - `GET /company_events?company_id=...`
   - If missing due to eventual consistency: `GET /corporate_event?corporate_events_id=...`

### 4) Save sectors on a company (AI panel)

1. Catalog load:
   - `GET /get_sectors`
2. Name fallback:
   - `GET /sectors_lookup?sector_name=...`
3. Save:
   - `PATCH /edit_company/{id}` with `updates: { "sectors_id": [ ... ] }`

---

## Appendix: Quick Endpoint Index (one-liners)

### Internal (FastAPI)

- **GET** `/` — UI HTML
- **POST** `/analyze` — main analysis + DB/AI payload
- **POST** `/refresh_db` — DB-only refresh
- **POST** `/smart_enrich_event` — “smart” event enrichment from URL
- **POST** `/enrich_event` — event enrichment from source_url
- **POST** `/ai_extract_event_from_url` — LLM extract event JSON from URL
- **POST** `/extract_event_meta` — cheap meta extract (title/date/desc/amount)
- **POST** `/api/search_company_headquarters` — HQ lookup
- **POST** `/api/search_individual_linkedin` — find LinkedIn + location
- **POST** `/api/search_individual_location` — location inference

### External (Xano; browser)

- **GET** `/api:8Bv5PK4I/get_sectors`
- **GET** `/api:8Bv5PK4I/sectors_lookup?sector_name=...`
- **GET** `/api:8Bv5PK4I/job_titles_list`
- **GET** `/api:8Bv5PK4I/job_title_lookup?job_title=...`
- **GET** `/api:8Bv5PK4I/counterparty_types`
- **GET** `/api:8Bv5PK4I/currency_lookup`
- **GET** `/api:8Bv5PK4I/business_focuses`
- **GET** `/api:8Bv5PK4I/business_focus_lookup?business_focus_title=...`
- **GET** `/api:8Bv5PK4I/years_lookup?year=...`
- **GET** `/api:8Bv5PK4I/get_location?city=...&state=...&country=...`
- **POST** `/api:8Bv5PK4I/post_company_overview`
- **PATCH** `/api:8Bv5PK4I/edit_company/{company_id}`
- **GET** `/api:8Bv5PK4I/company_overview?company_id=...`
- **GET** `/api:8Bv5PK4I/company_individuals?company_id=...`
- **GET** `/api:8Bv5PK4I/company_events?company_id=...`
- **GET** `/api:8Bv5PK4I/corporate_event?corporate_events_id=...`
- **POST** `/api:8Bv5PK4I/create_corporate_event`
- **PATCH** `/api:8Bv5PK4I/edit_corporate_event/{event_id}`
- **GET** `/api:8Bv5PK4I/query_existing_corporate_event?description=...`
- **GET** `/api:8Bv5PK4I/check_counterpaty?linkedin_url=...&company_name=...`
- **POST** `/api:8Bv5PK4I/create_counterparty`
- **PATCH** `/api:8Bv5PK4I/edit_counterparty/{counterparty_id}`
- **GET** `/api:8Bv5PK4I/individuals/get_by_linkedin?linkedin_url=...&name=...`
- **POST** `/api:8Bv5PK4I/create_individual`
- **PATCH** `/api:8Bv5PK4I/update_individual/{role_id}`
- **POST** `/api:8Bv5PK4I/assign_individual_to_counterparty/{counterparty_id}`
- **POST** `/api:8Bv5PK4I/create_advisor`

### External (Xano; backend)

- **GET** `/api:8Bv5PK4I/get_company_by_url?website_url=...`
- **GET** `/api:8Bv5PK4I/Get_new_company/{company_id}`
- **GET** `/api:y4OAXSVm/Get_investors_corporate_events?new_company_id=...`

### External (OpenRouter / SerpAPI / Scrapfly)

- **POST** `https://openrouter.ai/api/v1/chat/completions`
- **GET** `https://serpapi.com/search`
- **POST** `https://api.scrapfly.io/scrape`

