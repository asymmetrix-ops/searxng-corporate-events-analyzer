import os
import json
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dotenv import load_dotenv

from searxng_analyzer import (
    generate_corporate_events,
    generate_summary,
    generate_description,
    get_wikipedia_summary,
    get_top_management,
)

import requests


# ============================================================
# 🔹 Environment
# ============================================================
load_dotenv()

XANO_BASE_URL = "https://xdil-abvj-o7rq.e2.xano.io"


# ============================================================
# 🔹 Xano helpers (duplicated from Streamlit app for now)
# ============================================================

def check_company_by_url(website_url: str) -> Optional[dict]:
    """Check if a company already exists in the Xano database by URL."""
    try:
        endpoint = f"{XANO_BASE_URL}/api:8Bv5PK4I/get_company_by_url"
        resp = requests.get(endpoint, params={"website_url": website_url}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data is None or data == {} or (isinstance(data, dict) and data.get("id") is None):
            return None
        return data
    except Exception as e:
        print(f"[Xano] check_company_by_url error: {e}")
        return None


def get_company_by_id(company_id: int) -> Optional[dict]:
    """Fetch full company data from Xano by company ID."""
    try:
        endpoint = f"{XANO_BASE_URL}/api:GYQcK4au/Get_new_company/{company_id}"
        resp = requests.get(endpoint, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[Xano] get_company_by_id error: {e}")
        return None


def get_corporate_events_by_company_id(company_id: int) -> List[dict]:
    """Fetch corporate events for a company from Xano."""
    try:
        endpoint = f"{XANO_BASE_URL}/api:y4OAXSVm/Get_investors_corporate_events"
        resp = requests.get(endpoint, params={"new_company_id": company_id}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data.get("New_Events_Wits_Advisors", []) or []
    except Exception as e:
        print(f"[Xano] get_corporate_events_by_company_id error: {e}")
        return []


# ============================================================
# 🔹 FastAPI setup
# ============================================================

app = FastAPI(title="SearXNG – Events UI (No Streamlit)")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ============================================================
# 🔹 Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """
    Simple HTML UI:
    - Input: company name or URL
    - Button: Analyze
    - Left: AI events
    - Right: DB events (if company exists)
    - Each AI event has JS 'Add to DB' button that talks directly to Xano
    """
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "xano_base_url": XANO_BASE_URL,
        },
    )


@app.post("/analyze", response_class=JSONResponse)
async def analyze(payload: Dict[str, Any]) -> JSONResponse:
    """
    Body: { "query": "https://heliointelligence.com/" }

    Returns JSON:
    {
      "existing_company": {...} or null,
      "db_company": {...} or null,
      "db_events": [...],
      "ai_events": [...],
      "missing_events": [...],
      "matched_events": [...]
    }
    """
    query = (payload or {}).get("query", "").strip()
    if not query:
        return JSONResponse({"error": "Missing 'query' field"}, status_code=400)

    # 1) Pre-check in Xano
    existing_company = check_company_by_url(query)
    db_company = None
    db_events: List[dict] = []

    if existing_company and existing_company.get("id"):
        cid = existing_company["id"]
        db_company = get_company_by_id(cid)
        db_events = get_corporate_events_by_company_id(cid)

    # 2) AI company overview (summary + description)
    # Extract company name from URL if needed
    company_name = query
    input_website = ""
    if query.startswith("http://") or query.startswith("https://"):
        input_website = query
        # Extract domain name as company name hint
        from urllib.parse import urlparse
        parsed = urlparse(query)
        domain = parsed.netloc.replace("www.", "")
        # Use domain without TLD as company name hint
        company_name = domain.split(".")[0].title()
    
    ai_overview = {
        "name": company_name,
        "city": "",
        "country": "",
        "ownership": "",
        "website": input_website or "",
        "linkedin": "",
        "description": "",
    }
    try:
        wiki_text = get_wikipedia_summary(query)
        summary_md = generate_summary(query, text=wiki_text)
        description = generate_description(query, text=wiki_text, company_details=summary_md)
        
        print(f"[AI] Summary generated:\n{summary_md[:500]}...")

        # Very light parsing from the markdown summary to structured fields
        # We keep it simple: look for "- Website:", "- LinkedIn:", "- Headquarters:"
        website = input_website  # Default to input URL if provided
        linkedin = ""
        city = ""
        country = ""
        year_founded = ""
        ceo = ""
        
        def is_valid_value(val: str) -> bool:
            """Check if value is valid (not empty or placeholder)"""
            if not val:
                return False
            low = val.lower().strip()
            invalid = ["not found", "unknown", "n/a", "none", "<value>", ""]
            return low not in invalid and not low.startswith("<")
        
        def extract_url(text: str) -> str:
            """Extract URL from text, handling markdown links"""
            import re
            # Handle markdown links like [text](url)
            md_match = re.search(r'\[.*?\]\((https?://[^\)]+)\)', text)
            if md_match:
                return md_match.group(1)
            # Handle plain URLs
            url_match = re.search(r'(https?://[^\s\)]+)', text)
            if url_match:
                return url_match.group(1)
            return text.strip()
        
        for line in (summary_md or "").splitlines():
            low = line.lower().replace("–", "-").replace("—", "-")
            
            if "website:" in low:
                val = line.split(":", 1)[-1].strip()
                url = extract_url(val)
                if is_valid_value(url) and ("http://" in url or "https://" in url):
                    website = url
            elif "linkedin:" in low:
                val = line.split(":", 1)[-1].strip()
                url = extract_url(val)
                if is_valid_value(url) and "linkedin.com" in url.lower():
                    linkedin = url
            elif "headquarters:" in low:
                hq = line.split(":", 1)[-1].strip()
                if is_valid_value(hq):
                    # Very rough split city / country by last comma
                    if "," in hq:
                        parts = [p.strip() for p in hq.split(",")]
                        if len(parts) >= 2:
                            city = ", ".join(parts[:-1])
                            country = parts[-1]
                        else:
                            city = hq
                    else:
                        city = hq
            elif "year founded:" in low or "- founded:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    # Extract just the year if there's extra text
                    import re
                    year_match = re.search(r'(\d{4})', val)
                    if year_match:
                        year_founded = year_match.group(1)
            elif "- ceo:" in low or line.strip().lower().startswith("ceo:"):
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    ceo = val
            elif "company name:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    company_name = val

        ai_overview = {
            "name": company_name,
            "city": city,
            "country": country,
            "ownership": "",
            "website": website,
            "linkedin": linkedin,
            "description": description or "",
            "year_founded": year_founded,
            "ceo": ceo,
        }
        print(f"[AI] Parsed overview: {ai_overview}")
    except Exception as e:
        print(f"[AI] overview generation error: {e}")
        import traceback
        traceback.print_exc()

    # 3) AI events (full capacity again – use richer query set in analyzer)
    try:
        # Allow analyzer to return up to 20 deals (its default)
        ai_events = generate_corporate_events(query, max_events=20) or []
    except Exception as e:
        print(f"[AI] generate_corporate_events error: {e}")
        ai_events = []

    # 4) Simple matching (same as Streamlit gap analysis, simplified)
    def normalize_text(text: str) -> str:
        return (text or "").lower().strip()

    def extract_keywords(text: str) -> set:
        if not text:
            return set()
        stop_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "to",
            "of",
            "in",
            "for",
            "with",
            "by",
            "from",
            "its",
            "as",
        }
        words = set(normalize_text(text).split())
        return words - stop_words

    def events_match(ai_event: dict, db_event: dict, threshold: float = 0.4) -> bool:
        ai_name = ai_event.get("Event (short)", ai_event.get("event_short", ""))
        db_name = db_event.get("description", "")
        ai_keywords = extract_keywords(ai_name)
        db_keywords = extract_keywords(db_name)
        if not ai_keywords or not db_keywords:
            return False
        overlap = len(ai_keywords & db_keywords)
        max_len = max(len(ai_keywords), len(db_keywords))
        similarity = overlap / max_len if max_len > 0 else 0.0
        return similarity >= threshold

    matched_events: List[dict] = []
    missing_events: List[dict] = []

    if ai_events and db_events:
        for ev in ai_events:
            matched = any(events_match(ev, db_ev) for db_ev in db_events)
            if matched:
                matched_events.append(ev)
            else:
                missing_events.append(ev)
    else:
        missing_events = ai_events

    # 4.5) Fetch Top Management
    top_management = []
    try:
        print(f"[AI] Fetching top management for {query}...")
        mgmt_list, mgmt_text = get_top_management(query)
        if mgmt_list and isinstance(mgmt_list, list):
            top_management = mgmt_list
            print(f"[AI] Found {len(top_management)} executives")
    except Exception as e:
        print(f"[AI] get_top_management error: {e}")

    # 5) DB overview (normalize to same structure as AI side)
    db_overview = None
    if db_company:
        try:
            company_info = db_company.get("Company", db_company)
            name = company_info.get("name") or query
            loc = company_info.get("_locations") or {}
            city = loc.get("City", "")
            country = loc.get("Country", "")
            ownership_block = company_info.get("_ownership_type") or {}
            ownership = ownership_block.get("ownership", "")
            linkedin_block = company_info.get("linkedin_data") or {}
            linkedin = linkedin_block.get("LinkedIn_URL", "")
            website = company_info.get("url", "")
            desc = company_info.get("description", "")
            db_overview = {
                "name": name,
                "city": city,
                "country": country,
                "ownership": ownership,
                "website": website,
                "linkedin": linkedin,
                "description": desc,
            }
        except Exception as e:
            print(f"[Xano] overview normalization error: {e}")

    return JSONResponse(
        {
            "existing_company": existing_company,
            "db_company": db_company,
            "db_overview": db_overview,
            "ai_overview": ai_overview,
            "db_events": db_events,
            "ai_events": ai_events,
            "missing_events": missing_events,
            "matched_events": matched_events,
            "top_management": top_management,
        }
    )


# Convenient local dev entrypoint:
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=True,
    )


