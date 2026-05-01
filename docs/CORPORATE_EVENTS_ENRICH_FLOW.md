# Corporate Events Enrich Flow

This document explains the complete enrichment flow for corporate events, including where parsers are triggered.

## Overview

The enrichment flow for corporate events has **two main paths**:
1. **Automatic enrichment** (during `/analyze` - currently not active, but infrastructure exists)
2. **Manual enrichment** (triggered by user actions in the frontend)

---

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER TRIGGERS ANALYSIS                       │
│              POST /analyze (company URL/name)                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PARALLEL AI TASKS                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Overview   │  │    Events    │  │  Management  │          │
│  │   (Wiki +    │  │ (SerpAPI +   │  │  (SerpAPI +  │          │
│  │   LLM)       │  │   LLM)       │  │   LLM)       │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                              │                                   │
│                              ▼                                   │
│              generate_corporate_events()                         │
│              Returns: events with source_url                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              AI EVENTS RETURNED (with source_url)               │
│  Note: Automatic enrichment (enrich_ai_events_with_llm)         │
│        is defined but NOT currently called in /analyze          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FRONTEND RENDERS EVENTS                      │
│  Each event card has enrichment buttons:                        │
│  - 🧠 Smart Enrich (calls /smart_enrich_event)                  │
│  - 🧪 Enrich Event (calls /enrich_event)                        │
│  - 📄 Parse from source (calls /extract_event_meta)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. Automatic Enrichment (Infrastructure Exists, Not Currently Active)

### Function: `enrich_ai_events_with_llm()`
**Location**: `server.py:456`

**Status**: ⚠️ **Defined but NOT called** in `/analyze` endpoint

**What it does**:
- Takes a list of AI events
- For events with `source_url` (up to `max_enrich=5`):
  1. Calls `fetch_html(source_url)` - uses Scrapfly if key exists, else direct GET
  2. Parses HTML with BeautifulSoup
  3. Calls `ai_extract_event_from_text(text, url)` - LLM extraction
  4. Merges extracted fields into event object

**Fields extracted**:
- `title` → mapped to `Event (short)` and `event_short`
- `announcement_date`
- `closed_date`
- `deal_type`
- `deal_status`
- `long_description`
- `investment_amount_m`
- `investment_currency`
- `funding_stage`
- `investment_amount_source`
- `counterparties` → stored as `ai_counterparties`

**To activate**: Add this line in `/analyze` endpoint after `ai_events` is fetched:
```python
ai_events = enrich_ai_events_with_llm(ai_events, max_enrich=5)
```

---

## 2. Manual Enrichment Flows

### A. Smart Enrich (`/smart_enrich_event`)
**Endpoint**: `POST /smart_enrich_event`  
**Location**: `server.py:772`  
**Triggered by**: "🧠 Smart Enrich" button in frontend

**Flow**:
```
1. User clicks "🧠 Smart Enrich" button
   ↓
2. Frontend sends: { "url": "...", "event": { title, company, counterparties, ... } }
   ↓
3. Step 1: Fetch and AI-parse source URL
   - fetch_html(url)
   - BeautifulSoup parse
   - ai_extract_event_from_text(body_text, url)
   ↓
4. Step 2: Check completeness (title, date, amount, description)
   - If completeness < 3/4, proceed to Step 3
   ↓
5. Step 3: Web search enrichment (if incomplete)
   - Build enrichment_event object
   - Call ai_enrich_single_event(enrichment_event)
   - Merge enriched data into result
   ↓
6. Step 4: Validate dates
   - validate_enriched_dates(result)
   - Fix future dates, swapped dates
   ↓
7. Return enriched_event to frontend
```

**Key Functions**:
- `fetch_html(url)` - `server.py:374` - Fetches HTML (Scrapfly or direct)
- `ai_extract_event_from_text(text, url)` - `server.py:419` - LLM extraction
- `ai_enrich_single_event(event)` - `server.py:520` - Full enrichment with evidence extraction
- `validate_enriched_dates(result)` - `server.py:610` - Date validation

---

### B. Standard Enrich (`/enrich_event`)
**Endpoint**: `POST /enrich_event`  
**Location**: `server.py:754`  
**Triggered by**: "🧪 Enrich Event" button (if exists)

**Flow**:
```
1. User clicks "🧪 Enrich Event" button
   ↓
2. Frontend sends: { "event": { ... } }
   ↓
3. Call ai_enrich_single_event(event)
   - Extracts source_url from event
   - fetch_html(source_url)
   - BeautifulSoup parse
   - LLM extraction with detailed prompt
   ↓
4. Return enriched_event
```

**LLM Prompt** (from `ai_enrich_single_event`):
- Extracts: title, announcement_date, deal_type, deal_status, long_description
- Amount (millions), currency, amount_status, amount_confidence
- Stage, stage_status, parties, evidence_links, evidence_summary

---

### C. Parse from Source (`/extract_event_meta`)
**Endpoint**: `POST /extract_event_meta`  
**Location**: `server.py:667`  
**Triggered by**: "📄 Parse from source" button

**Flow**:
```
1. User clicks "📄 Parse from source" button
   ↓
2. Frontend sends: { "url": "..." }
   ↓
3. Heuristic extraction (NO LLM):
   - fetch_html(url)
   - BeautifulSoup parse
   - Extract title (h1 > title tag > og:title > h2)
   - extract_first_date(body_text) - regex date parsing
   - extract_investment_fields(body_text) - regex amount/currency/stage
   - Extract long_description (first meaningful paragraphs)
   ↓
4. Return: { title, announcement_date, long_description, ...investment_fields }
```

**Heuristic Functions**:
- `extract_first_date(text)` - Parses dates (YYYY-MM-DD, dd/mm/yyyy, dd.mm.yyyy)
- `extract_investment_fields(text)` - Extracts amount, currency, funding stage via regex

---

## 3. Core Parser Functions

### `ai_extract_event_from_text(text, url)`
**Location**: `server.py:419`

**Purpose**: LLM-based extraction from page text

**Input**: 
- `text`: Page text (truncated to 8000 chars)
- `url`: Source URL

**Output**: JSON with fields:
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
  "counterparties": [{"name": "", "role": "", "website": "", "linkedin": ""}]
}
```

**LLM Model**: `openai/gpt-4o-mini`  
**Prompt**: Structured extraction with strict JSON format

---

### `fetch_html(url, force_scrapfly=False)`
**Location**: `server.py:374`

**Purpose**: Fetch HTML from URL with optional Scrapfly support

**Flow**:
```
1. Check if SCRAPFLY_KEY exists
   ↓
2. If Scrapfly key exists (and force_scrapfly=True):
   - POST to Scrapfly API
   - url in params + body
   ↓
3. Else:
   - Direct requests.get(url)
   - Headers: User-Agent, Accept
   - Timeout: 10s
   ↓
4. Return HTML text
```

**Error Handling**: Returns None on failure, logs errors

---

### `ai_enrich_single_event(event)`
**Location**: `server.py:520`

**Purpose**: Full enrichment with evidence extraction

**Input**: Event dict with `source_url`

**Process**:
1. Extract `source_url`, `title`, `company`, `counterparties` from event
2. `fetch_html(source_url)`
3. BeautifulSoup parse → extract text
4. LLM prompt with detailed schema (evidence extraction)
5. Parse JSON response
6. Return enriched data

**LLM Output Schema**:
```json
{
  "title": "",
  "announcement_date": "",
  "deal_type": "",
  "deal_status": "",
  "long_description": "",
  "amount": null,
  "currency": "",
  "amount_status": "",
  "amount_confidence": 0.0,
  "amount_source_type": "",
  "amount_source_url": "",
  "stage": "",
  "stage_status": "",
  "parties": [{"name": "", "role": ""}],
  "evidence_links": [],
  "evidence_summary": "",
  "enrichment_version": 1
}
```

---

## 4. Frontend Integration

### Smart Enrich Button
**Location**: `templates/index.html:7651` (AI events) and `templates/index.html:6404` (new event form)

**Code Flow**:
```javascript
smartEnrichBtn.onclick = async () => {
  const urlToParse = sourceInput.value.trim();
  const body = {
    url: urlToParse,
    event: {
      title: titleInput.value,
      company: document.getElementById("ai_name")?.value || "",
      counterparties: [...],
      announcement_date: annInput?.value || null,
      deal_type: typeSelect?.value || ""
    }
  };
  
  const res = await fetch("/smart_enrich_event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  
  const data = await res.json();
  const enr = data.enriched_event || {};
  
  // Update form fields with enriched data
  // ...
}
```

---

## 5. Complete Flow Summary

### During `/analyze`:
1. ✅ `generate_corporate_events()` - Fetches events via SerpAPI + LLM
2. ⚠️ `enrich_ai_events_with_llm()` - **NOT CALLED** (but available)
3. ✅ Events returned to frontend with `source_url`
4. ✅ Frontend renders events with enrichment buttons

### User-Triggered Enrichment:
1. **Smart Enrich** (`/smart_enrich_event`):
   - Parses source URL first
   - Checks completeness
   - Searches web if incomplete
   - Validates dates

2. **Standard Enrich** (`/enrich_event`):
   - Full LLM extraction from source URL
   - Evidence-based extraction

3. **Parse from Source** (`/extract_event_meta`):
   - Heuristic extraction only
   - No LLM calls
   - Fast, basic parsing

---

## 6. Key Files

- **`server.py`**:
  - `enrich_ai_events_with_llm()` - Line 456 (batch enrichment, not used)
  - `ai_extract_event_from_text()` - Line 419 (LLM extraction)
  - `ai_enrich_single_event()` - Line 520 (full enrichment)
  - `fetch_html()` - Line 374 (HTML fetching)
  - `/smart_enrich_event` - Line 772 (smart enrichment endpoint)
  - `/enrich_event` - Line 754 (standard enrichment endpoint)
  - `/extract_event_meta` - Line 667 (heuristic parsing endpoint)

- **`searxng_analyzer.py`**:
  - `generate_corporate_events()` - Line 2414 (initial event discovery)

- **`templates/index.html`**:
  - Smart Enrich button handlers - Lines 6404, 7651
  - Event rendering and form updates

---

## 7. Notes

1. **Automatic enrichment is disabled**: The `enrich_ai_events_with_llm()` function exists but is not called in `/analyze`. This was likely disabled for performance reasons.

2. **Manual enrichment is active**: Users can trigger enrichment via buttons in the frontend.

3. **Scrapfly is optional**: If `SCRAPFLY_KEY` is not set, the system falls back to direct HTTP GET requests.

4. **Date validation**: The `validate_enriched_dates()` function ensures dates are logical (no future dates, closed_date >= announcement_date).

5. **Error handling**: All enrichment functions return error objects on failure, which are handled gracefully in the frontend.

