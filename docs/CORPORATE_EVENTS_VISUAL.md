# Corporate events — search & data gathering (visual)

This document is a **picture-first** companion to:

- [`SEARCH_AND_QUERIES.md`](SEARCH_AND_QUERIES.md) — exact **SerpAPI query strings**, packs, and patterns  
- [`CORPORATE_EVENTS_ENRICH_FLOW.md`](CORPORATE_EVENTS_ENRICH_FLOW.md) — **manual enrich** endpoints after the UI loads  
- [`doc.md`](../doc.md) — full research-engine map  

**Important naming note:** corporate-event **discovery** uses **Google via SerpAPI** (parallel HTTP), not a self-hosted SearXNG instance. The repo name is historical.

---

## 1. Where corporate events fit in `POST /analyze`

```mermaid
flowchart TB
  subgraph Client["Browser"]
    UI["index.html — Analyze + profile options"]
  end

  subgraph Server["server.py — /analyze"]
    Prefetch["Pre-check Xano: company by URL"]
    Pool["ThreadPoolExecutor — up to 3 lanes"]
    Own["After overview: detect_ownership_from_description"]
    Match["Keyword overlap: AI events vs DB events"]
    Resp["JSON: ai_events, db_events, missing, matched, …"]
  end

  subgraph EventsLane["Events lane — task_events()"]
    GCE["searxng_analyzer.generate_corporate_events(query)"]
  end

  subgraph OverviewLane["Overview lane — task_overview()"]
    Wiki["Wikipedia + Yahoo (if public) + Serp blitz"]
    Sum["generate_summary / generate_description"]
  end

  subgraph PeopleLane["People lane — task_management()"]
    Mgmt["get_top_management"]
  end

  UI -->|"POST /analyze { query, options }"| Prefetch
  Prefetch --> Pool
  Pool --> OverviewLane
  Pool --> EventsLane
  Pool --> PeopleLane
  OverviewLane --> Own
  EventsLane --> Match
  OverviewLane --> Match
  PeopleLane --> Match
  Own --> Resp
  Match --> Resp
  Resp --> UI
```

**Options** (from payload) can **skip** lanes: `include_overview`, `include_events`, `include_individuals`, and counterparties are tied to events.

---

## 2. Inside `generate_corporate_events` (search → context → LLM)

```mermaid
flowchart LR
  subgraph Input
    Q["Company name or URL-derived hint"]
  end

  subgraph Classify["detect_company_type"]
    D1["3 discovery Serp queries"]
    D2["Snippet score: startup vs enterprise"]
  end

  subgraph Pack["Query pack — 1, 18, or 18 strings"]
    P1["max_events ≤ 1 → 1 minimal query"]
    P2["Startup pack — 18 queries"]
    P3["Enterprise pack — 18 queries"]
  end

  subgraph Serp["serpapi_parallel_search"]
    B["Batches of 10 concurrent requests"]
    R["Merge + dedupe by URL"]
    H["Each hit: title, snippet, link, published_date"]
  end

  subgraph Context["Context blob"]
    C["Numbered blocks: [i] title, snippet, date, Source: URL"]
  end

  subgraph LLM["OpenRouter — Claude extraction"]
    PR["Prompt: checklist + field schema"]
    OUT["JSON array of events — source_url per row"]
  end

  Q --> Classify
  Classify --> Pack
  Pack --> Serp
  Serp --> Context
  Context --> LLM
  LLM --> OUT
```

**Data gathered at this stage** is **only what Serp returns** (snippets + metadata) — **not** full article HTML. Each extracted event should carry a **`source_url`** (best article URL from context) for later enrichment.

---

## 3. Data sources stack (corporate events path)

```mermaid
flowchart TB
  subgraph Discovery["Discovery — always for AI event list"]
    SerpAPI["SerpAPI → Google organic"]
  end

  subgraph Synthesis["Synthesis"]
    LLM1["LLM: structured events from snippets"]
  end

  subgraph Optional["Optional — not run automatically in shipped /analyze"]
    Auto["enrich_ai_events_with_llm (defined, not wired)"]
    Manual["UI: Smart Enrich / Enrich / Parse → server routes"]
  end

  subgraph Evidence["Evidence fetch (manual & helpers)"]
    FH["fetch_html — Scrapfly or requests"]
    BS["BeautifulSoup text + heuristics"]
    LLM2["ai_extract_event_from_text / ai_enrich_single_event"]
  end

  subgraph Storage["App / Xano — outside this pipeline"]
    Xano["DB events from Get_investors_corporate_events / company_events"]
  end

  SerpAPI --> LLM1
  LLM1 --> Optional
  Manual --> Evidence
  Auto --> Evidence
  Xano -.->|"compared in /analyze"| LLM1
```

---

## 4. Manual enrichment after cards render (high level)

```mermaid
sequenceDiagram
  participant U as User
  participant FE as index.html
  participant S as server.py
  participant W as Web
  participant L as LLM

  U->>FE: Smart Enrich / Enrich / Parse
  FE->>S: POST /smart_enrich_event or /enrich_event or /extract_event_meta
  S->>W: fetch_html(source URL)
  W-->>S: HTML
  S->>S: heuristics (dates, amounts, …)
  S->>L: ai_extract_event_from_text (and maybe ai_enrich_single_event)
  L-->>S: structured fields
  S-->>FE: merged event JSON
  FE-->>U: updated card fields
```

See [`CORPORATE_EVENTS_ENRICH_FLOW.md`](CORPORATE_EVENTS_ENRICH_FLOW.md) for step-by-step text.

---

## 5. ASCII — one-page mental model

```
  User query (name or URL)
           │
           ▼
  ┌──────────────────────────────────────┐
  │ POST /analyze                         │
  │  • Xano pre-check → db_events        │
  │  • Parallel: overview | EVENTS | mgmt │
  └──────────────────────────────────────┘
           │
           │  EVENTS lane
           ▼
  detect_company_type (Serp × 3)
           │
           ▼
  choose 1 / 18 / 18 query strings (quoted company)
           │
           ▼
  serpapi_parallel_search → dedupe URLs
           │
           ▼
  big numbered “snippet journal” string
           │
           ▼
  Claude (OpenRouter) → ai_events[]
           │
           ├── source_url on each event (for later)
           │
           ▼
  server matches titles vs db_events → missing / matched
           │
           ▼
  UI: optional HTML fetch + LLM enrich per button (not automatic)
```

---

## 6. Files to open in the repo

| Topic | File |
|--------|------|
| Event query packs + LLM prompt | `searxng_analyzer.py` — `generate_corporate_events`, `detect_company_type` |
| Parallel search + dedup | `searxng_analyzer.py` — `serpapi_parallel_search` |
| `/analyze` orchestration | `server.py` — `analyze`, `task_events` |
| Enrich routes | `server.py` — `smart_enrich_event`, `enrich_event`, … |

---

## Viewing Mermaid

- **GitHub / GitLab**: Mermaid renders in `.md` preview.  
- **VS Code / Cursor**: use a Mermaid preview extension if the built-in preview does not render diagrams.  
- **Export**: paste diagrams into [mermaid.live](https://mermaid.live) for PNG/SVG.
