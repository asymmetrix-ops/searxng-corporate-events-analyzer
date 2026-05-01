# Lookup Endpoints Only (Filtered)

This file lists **only lookup/search/catalog endpoints** used by the repo — i.e., endpoints that *fetch reference data*, *resolve IDs*, or *search for existing records*.  
It intentionally excludes create/update/delete (POST create, PATCH edit, etc.).

---

## Internal (FastAPI) lookup/search endpoints

### POST `/api/search_company_headquarters`

- **Use**: Find/normalize company HQ location (city/state/country).
- **Body**:

```json
{ "company": "Acme", "website": "https://acme.com", "city": "", "country": "" }
```

- **Returns**:

```json
{ "city": "", "state": "", "country": "" }
```

### POST `/api/search_company_linkedin`

- **Use**: Recover the current LinkedIn company URL for a company whose old LinkedIn URL/slug is stale.
- **Body**:

```json
{ "company": "Acme", "website": "https://acme.com", "old_linkedin_url": "https://www.linkedin.com/company/acme-old/" }
```

- **Returns**:

```json
{
  "linkedin_url": "https://www.linkedin.com/company/acme/",
  "source": "xano",
  "matched_by": "website_domain",
  "query_used": "https://acme.com",
  "queries_used": ["https://acme.com"],
  "changed": true,
  "old_linkedin_url": "https://www.linkedin.com/company/acme-old/",
  "company": "Acme",
  "website": "https://acme.com"
}
```

### POST `/api/search_individual_linkedin`

- **Use**: Find an individual's LinkedIn URL (and sometimes location).
- **Body**:

```json
{ "name": "John Smith", "company": "Acme", "position": "CFO" }
```

- **Returns**:

```json
{ "linkedin_url": "https://...", "location": { "city": "", "state": "", "country": "" }, "query": "..." }
```

### POST `/api/search_individual_location`

- **Use**: Infer an individual's location (city/state/country).
- **Body**:

```json
{ "name": "Mary Meeker", "company": "", "position": "", "linkedin_url": "" }
```

- **Returns**:

```json
{ "city": "", "state": "", "country": "" }
```

### POST `/extract_event_meta`

- **Use**: Lightweight extraction from an announcement page (title/date/description + investment heuristics).
- **Body**:

```json
{ "url": "https://example.com/press-release" }
```

- **Returns** (shape):

```json
{
  "title": "",
  "announcement_date": "YYYY-MM-DD",
  "long_description": "",
  "funding_stage": "",
  "investment_amount_m": 0,
  "investment_currency": "USD"
}
```

---

## External (Xano) lookup/catalog/search endpoints

> **Xano base**: `https://xdil-abvj-o7rq.e2.xano.io`

### Company existence / pre-check

#### GET `/api:8Bv5PK4I/get_company_by_url?website_url=...`

- **Use**: Check if company exists by website URL.
- **Query params**: `website_url`
- **Returns**: list or object containing at least `id` when found.

### Sector catalogs & lookups

#### GET `/api:8Bv5PK4I/get_sectors`

- **Use**: Fetch sectors catalog for dropdowns.
- **Returns**: array of `{ id, sector_name }` (or `{ id, name }`).

#### GET `/api:8Bv5PK4I/sectors_lookup?sector_name=...`

- **Use**: Resolve sector name → sector ID.
- **Query params**: `sector_name`
- **Returns**: `{ id: number, ... }` when found.

### Business focus catalogs & lookups

#### GET `/api:8Bv5PK4I/business_focuses`

- **Use**: Fetch business focus catalog for dropdowns.
- **Returns**: array of `{ id, business_focus }` (or `{ id, name }`).

#### GET `/api:8Bv5PK4I/business_focus_lookup?business_focus_title=...`

- **Use**: Resolve business focus title → ID.
- **Query params**: `business_focus_title`
- **Returns**: `{ id: number, ... }` when found.

### Job title catalogs & lookups

#### GET `/api:8Bv5PK4I/job_titles_list`

- **Use**: Fetch job titles catalog for dropdowns.
- **Returns**: array of `{ id, job_title }` (or `{ id, title/name }`).

#### GET `/api:8Bv5PK4I/job_title_lookup?job_title=...`

- **Use**: Resolve job title text → job title ID.
- **Query params**: `job_title`
- **Returns**: `{ id: number, ... }` when found.

### Counterparty type catalog

#### GET `/api:8Bv5PK4I/counterparty_types`

- **Use**: Fetch counterparty role/type catalog.
- **Returns**: array of `{ id, name }`.

### Currency catalog

#### GET `/api:8Bv5PK4I/currency_lookup`

- **Use**: Fetch currency catalog (used to match code → ID in UI).
- **Returns**: array (shape depends on your Xano schema).

### Year lookup

#### GET `/api:8Bv5PK4I/years_lookup?year=...`

- **Use**: Resolve a year like `"2012"` → internal year record ID.
- **Query params**: `year`
- **Returns**: `{ id: number, ... }` when found.

### Location lookup

#### GET `/api:8Bv5PK4I/get_location?city=...&state=...&country=...`

- **Use**: Resolve (city/state/country) → `locations_id`.
- **Query params**: `city`, `state`, `country`
- **Returns**: one of several handled shapes:
  - `{ "id": number }`
  - `{ "locations_id": number }`
  - `{ "location": { "id": number } }`
  - `{ "_locations": { "id": number } }`

### “Does this counterparty company exist?” lookup

#### GET `/api:8Bv5PK4I/check_counterpaty?linkedin_url=...&company_name=...`

- **Use**: Find an existing company ID for counterparties.
- **Query params**: `linkedin_url`, `company_name`
- **Returns**: object containing `id` when found (plus optional metadata like name/website/linkedin).

### Individual lookup (by LinkedIn)

#### GET `/api:8Bv5PK4I/individuals/get_by_linkedin?linkedin_url=...&name=...`

- **Use**: Find an individual (and sometimes a role ID) by LinkedIn URL + name.
- **Query params**: `linkedin_url`, `name`
- **Returns**: variable shapes (UI normalizes):
  - `{ result1: {...}, role: [{id: ...}] }`
  - `{ ...row... }`
  - `[ { ...row... } ]`

### Individual search (used by UI)

#### GET `/api:8Bv5PK4I/search_individual?...`

- **Use**: Search individuals by query params assembled by UI.
- **Query params**: constructed via `URLSearchParams()` (seen: `name`, plus potentially `linkedin_url`, etc.).
- **Returns**: UI expects something like `{ exists: true, individual_id: 123 }` when found.

### Corporate event ID lookup (by description)

#### GET `/api:8Bv5PK4I/query_existing_corporate_event?description=...`

- **Use**: Resolve event description text → event ID (used when event ID isn’t already known in UI).
- **Query params**: `description`
- **Returns**: implementation-dependent; UI uses it to derive an `eventId`.

