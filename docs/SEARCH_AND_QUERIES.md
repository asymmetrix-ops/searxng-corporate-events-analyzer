# How search works — SerpAPI queries and string construction

This document explains **how Google search is performed** in this project and **how query strings are assembled**. Implementation lives mainly in [`searxng_analyzer.py`](../searxng_analyzer.py); HTTP glue is in [`server.py`](../server.py).

---

## 1. Transport: what actually runs the search

### SerpAPI + Google organic

- Calls go to **`https://serpapi.com/search`** with standard parameters: `q` (the full query string), `hl=en`, `gl=us`, `num` (results per query), `api_key`, `output=json`.
- The code does **not** use SearXNG for these paths in the current analyzer; **`SERPAPI_KEY`** is required wherever Serp is used.

### Parallel execution and deduplication

[`serpapi_parallel_search(queries, api_key, num_results)`](../searxng_analyzer.py) (see `_run_parallel_searches` / `_serpapi_search_async`):

- Runs queries in **batches of 10** concurrent HTTP requests (aiohttp).
- After each batch, sleeps **0.3s** before the next batch (rate-limit hygiene).
- Merges all organic hits into one list **deduped by URL** (first occurrence wins).
- Each stored hit includes **`title`**, **`snippet`**, **`link`**, optional **`published_date`** (Google’s `date` field when present).

So “search” from the engine’s perspective is: **many hand-written query strings** → parallel SerpAPI → **one flat, URL-deduplicated result list** (then passed to prompts or parsers).

---

## 2. Company name / query input normalization

Across flows, user input may be:

- A **bare name** (`"Stripe"`), or  
- A **URL** (`https://www.acme.com`).

Patterns used in code:

- **Domain label**: from URL hostname, strip `www.`, sometimes first label only (e.g. `equifax` from `equifax.com`) for ticker/Yahoo-centric paths.
- **Quoted company phrase**: **`"{company_name}"`** wraps the company token so Google treats it as an exact-ish phrase — this appears in almost all corporate/event/overview query templates.

---

## 3. Corporate events (`generate_corporate_events`)

### Step A — classify “startup-ish” vs “enterprise-ish” (`detect_company_type`)

Before building the big query pack, three **discovery** queries run in parallel (`num_results=5`):

1. `"<company>" founded startup`  
2. `"<company>" series funding OR seed round OR accelerator`  
3. `"<company>" site:crunchbase.com OR site:linkedin.com/company`

Snippets are concatenated and scored against **startup** vs **enterprise** keyword lists (and optional founding-year regex). The winner picks which **18-query pack** is used next.

### Step B — query packs (always 18 strings unless testing)

- **`max_events <= 1`**: single minimal query, e.g.  
  `"<company>" acquisition OR merger OR investment OR funding`

- **Startup pack** (examples — all use quoted company name):

  - Funding: `raises` / `raised`, series rounds, recent years, `led by` VC, grants, etc.  
  - News sites: `site:techcrunch.com`, `site:crunchbase.com`, EU startup press, PR wires, etc.  
  - Deals/partnerships: accelerators, partnerships, acquisitions, `backed by`, sector tags, etc.

- **Enterprise pack**:

  - PR wires / wire services with `acquisition`  
  - Free-text acquisition wording (`acquires`, `merger`, “announces acquisition”, year slices)  
  - PE/investor angles, PitchBook/PE Hub, Crunchbase funding history  
  - Divestitures, JV/partnerships, bolt-on language, generic `M&A deal`

Execution: **`serpapi_parallel_search(queries, …, num_results=15)`** — all strings in one scheduling pass (still batched internally by 10).

### Step C — downstream use

Organic titles/snippets/links are stitched into **one numbered context blob** fed to **one large LLM extraction** (Claude) to produce structured events JSON.

---

## 4. Company overview (`generate_summary`)

Wikipedia text is preferred when rich enough; if not, parallel **fallback** searches (`num_results=5`):

| Purpose | Example query pattern |
|--------|-------------------------|
| General profile | `"<search_name>" company about headquarters` |
| LinkedIn company | `"<search_name>" site:linkedin.com/company` |
| Crunchbase | `"<search_name>" site:crunchbase.com` |
| Leadership/location hints | `"<search_name>" founded CEO location` |
| Own site crawl | `site:<domain>` if user pasted a website URL, else `"<search_name>" company` |

**Press / news hub** (optional):

- Not Serp-first: parallel **HEAD/GET** probes on `https://<host>/press`, `/news`, etc.  
- If still missing: Serp with  
  `site:<domain> press OR newsroom OR "press releases"`  
  and similar; URLs are filtered to “section” paths, not long article slugs.

**Financial / investor context** (parallel, `num_results=5`):

- `"<search_name>" enterprise value OR EV revenue EBITDA`  
- `"<search_name>" investors OR "backed by" OR "portfolio of"`  
- `"<search_name>" site:pitchbook.com OR site:crunchbase.com`

**CEO** (separate helper `get_ceo_from_serpapi_ai`, `num_results=10`) — **unquoted** company in natural phrases:

- `<company> current CEO`, `<company> CEO`, `who is the CEO of <company>`, `chief executive officer`

Those hits are fed to a **small strict LLM** prompt to output a single name or `NONE`.

---

## 5. Top management (`get_top_management`)

Three parallel leadership queries (`num_results=10`):

1. `"<company_name>" leadership team CEO CFO executives`  
2. `"<company_name>" management team board directors`  
3. `<company_name> CEO "chief executive" OR CFO OR CTO`

Results append snippet text to Wikipedia context, then **Sonar** (and optionally **Claude**) extract a JSON list; **per-executive LinkedIn** uses additional dedicated query builders (see §7).

---

## 6. Person location (two paths)

### Fast path (`search_individual_location` style — regex/snippet)

Strategy comment in code: mimic a good Google query:

- **`"<Person>" <Company> <Role> location`** (role may be shortened to C-level acronyms)  
- Falls back: person+company, person+role, `"<Person>" location`, or raw LinkedIn URL + `location`

Up to **4** of these may be sent through **`serpapi_parallel_search`**; snippets are scanned for “Location:” style patterns.

### API path (`get_raw_serpapi_results_for_person_location` + LLM)

Single composed query (space-separated, person quoted):

- `("<Person>")` + optional company + optional shortened position + **`location`**

Example shape: `"Daniel Maguire" London Stock Exchange Group plc CEO location`

That **one** query’s organic block is returned for LLM interpretation (personal vs HQ disambiguation + normalization).

---

## 7. LinkedIn profile discovery (individuals)

Helpers build **stacks** of queries such as:

- `"<person>" <company> <role> linkedin site:linkedin.com/in`  
- Variants with quoted company, role-only fallbacks, and generic `"<person>" linkedin site:linkedin.com/in`

These are tried (Serp and sometimes Startpage) until a profile URL or usable snippet appears. Exact lists differ slightly between “search” vs “enrich” helpers — same idea: **quoted person name** + **company/role** + **`site:linkedin.com/in`**.

---

## 8. Company HQ (`search_company_headquarters`)

Uses a small set of **headquarters-oriented** queries (quoted clean name), e.g.:

- `"<clean_name>" headquarters location`  
- `"<clean_name>" head office city`  
- Plus fallbacks like `"<clean_name>" company location city`

Startpage may be used as a fallback path in the same family of “location in prose” extraction.

---

## 9. Ticker lookup (`lookup_ticker`)

Serp parallel (`num_results=5`), e.g.:

- `"<clean_name>" stock ticker symbol NASDAQ NYSE`  
- `"<clean_name>" ticker site:finance.yahoo.com`  
- Optional third query if a website string is provided

Snippets are scanned for exchange + symbol patterns.

---

## 10. Company LinkedIn URL (`search_company_linkedin_detailed`)

Serp-oriented patterns include:

- `"<clean_name>" site:linkedin.com/company/`  
- With optional **domain hint**: `"<clean_name>" <domain> site:linkedin.com/company/`  
- Fallback: `"<company_name>" linkedin`

Additional Startpage/other fallbacks exist in the same function family.

---

## 11. Design patterns (how to think about adding queries)

| Pattern | When to use |
|--------|--------------|
| **Quoted company/person** | Reduce ambiguity for short or generic names (`"Stripe" acquisition …`). |
| **`site:` filters** | Force tier-1 sources (PR wires, TechCrunch, LinkedIn routes). |
| **`OR` clusters** | Widen recall within one Serp API call (`prnewswire OR businesswire`). |
| **Year buckets** | Enterprise M&A packs split years so one bad query doesn’t drop all recall. |
| **Parallel packs** | Many narrow queries outperform one giant query; dedup merges overlap. |

---

## 12. Related utilities (not SerpAPI)

- **HTTP direct**: `requests` / Scrapfly **`fetch_html`** for article URLs (enrichment), not query construction.  
- **Wikipedia REST**: overview text pull (no Serp `q`).  
- **Yahoo Finance (`yfinance`)**: fundamentals when ticker resolves — separate from Google query strings.

---

## 13. Files to read in the repo

| Concern | Location |
|---------|----------|
| Parallel Serp + dedup | `searxng_analyzer.py` — `serpapi_parallel_search`, `_run_parallel_searches` |
| Event query packs + type detection | `searxng_analyzer.py` — `generate_corporate_events`, `detect_company_type` |
| Overview / press / financial Serp | `searxng_analyzer.py` — `generate_summary` |
| Management Serp | `searxng_analyzer.py` — `get_top_management` |
| CEO Serp | `searxng_analyzer.py` — `get_ceo_from_serpapi_ai` |
| Person location | `searxng_analyzer.py` — `search_individual_location`, `get_raw_serpapi_results_for_person_location` |
| HQ / ticker / company LinkedIn | `searxng_analyzer.py` — `search_company_headquarters`, `lookup_ticker`, `search_company_linkedin_detailed` |
