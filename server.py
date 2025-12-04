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
    """Fetch full company data from Xano by company ID (new_company_id)."""
    try:
        # Use the correct API endpoint for fetching company by new_company_id
        endpoint = f"{XANO_BASE_URL}/api:8Bv5PK4I/Get_new_company/{company_id}"
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


@app.post("/refresh_db", response_class=JSONResponse)
async def refresh_db(payload: Dict[str, Any]) -> JSONResponse:
    """
    Body: { "query": "https://heliointelligence.com/" }

    Returns ONLY database data (no AI re-analysis):
    {
      "existing_company": {...} or null,
      "db_company": {...} or null,
      "db_overview": {...},
      "db_events": [...],
      "db_management": [...],
      "top_management": [],  // Empty since no AI analysis
      "ai_events": [],       // Empty since no AI analysis
      "missing_events": [],  // Empty since no AI analysis
      "matched_events": []   // Empty since no AI analysis
    }
    """
    query = (payload or {}).get("query", "").strip()
    if not query:
        return JSONResponse({"error": "Missing 'query' field"}, status_code=400)

    # 1) Pre-check in Xano
    existing_company = check_company_by_url(query)
    db_company = None
    db_events: List[dict] = []
    db_management: List[dict] = []

    if existing_company and existing_company.get("id"):
        cid = existing_company["id"]
        db_company = get_company_by_id(cid)
        db_events = get_corporate_events_by_company_id(cid)

    # 2) DB overview (extract from db_company)
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

            # Year founded - check _years.Year first (proper structure), fallback to year_founded
            years_block = company_info.get("_years") or {}
            if isinstance(years_block, dict) and years_block.get("Year"):
                year_founded = str(years_block.get("Year"))
            else:
                # Fallback to direct year_founded field (but ignore if it's just a reference ID like 10)
                year_founded_raw = company_info.get("year_founded", 0)
                # If year_founded is a 4-digit number (actual year), use it; otherwise treat as empty
                if year_founded_raw and isinstance(year_founded_raw, (int, str)):
                    year_str = str(year_founded_raw)
                    if len(year_str) == 4 and year_str.isdigit():
                        year_founded = year_str
                    else:
                        year_founded = ""
                else:
                    year_founded = ""

            # Primary business focus - can be nested object or direct reference
            business_focus_block = company_info.get("primary_business_focus_id") or company_info.get("_primary_business_focus") or {}
            if isinstance(business_focus_block, dict):
                primary_business_focus = business_focus_block.get("business_focus", "") or business_focus_block.get("primary_business_focus", "") or business_focus_block.get("name", "")
                primary_business_focus_id = business_focus_block.get("id", 0)
            else:
                primary_business_focus = ""
                primary_business_focus_id = business_focus_block if isinstance(business_focus_block, int) else 0

            # Sectors - try new_sectors_data first (has actual sector names), fallback to sectors_id
            sectors = []
            primary_sectors = []
            secondary_sectors = []

            # First check new_sectors_data (JSON string payload with real data)
            new_sectors_data = company_info.get("new_sectors_data") or []
            if isinstance(new_sectors_data, list) and len(new_sectors_data) > 0:
                try:
                    sectors_payload = new_sectors_data[0].get("sectors_payload", "")
                    if sectors_payload:
                        import json
                        parsed_sectors = json.loads(sectors_payload)
                        # Extract primary sectors
                        for s in parsed_sectors.get("primary_sectors", []):
                            if isinstance(s, dict) and s.get("sector_name"):
                                primary_sectors.append({
                                    "sector": s.get("sector_name"),
                                    "id": s.get("id", 0),
                                    "importance": "Primary"
                                })
                        # Extract secondary sectors
                        for s in parsed_sectors.get("secondary_sectors", []):
                            if isinstance(s, dict) and s.get("sector_name"):
                                secondary_sectors.append({
                                    "sector": s.get("sector_name"),
                                    "id": s.get("id", 0),
                                    "importance": "Secondary"
                                })
                        sectors = primary_sectors + secondary_sectors
                except Exception as e:
                    print(f"[Xano] new_sectors_data parse error: {e}")

            # Fallback to sectors_id if new_sectors_data didn't yield results
            if not sectors:
                sectors_data = company_info.get("sectors_id") or company_info.get("_sectors") or company_info.get("sectors") or []
                if isinstance(sectors_data, list):
                    for s in sectors_data:
                        if isinstance(s, dict):
                            sector_name = s.get("sector_name") or s.get("sector") or s.get("name", "")
                            importance = s.get("Sector_importance", "")
                            if sector_name and sector_name.strip():
                                sectors.append({"sector": sector_name, "id": s.get("id", 0), "importance": importance})
                        elif isinstance(s, str) and s.strip():
                            sectors.append({"sector": s})

            # Webpage monitored (press/news page URL)
            webpage_monitored = company_info.get("webpage_monitored", "") or company_info.get("press_page_url", "") or company_info.get("news_url", "") or ""

            db_overview = {
                "name": name,
                "city": city,
                "country": country,
                "ownership": ownership,
                "website": website,
                "linkedin": linkedin,
                "description": desc,
                "year_founded": year_founded,
                "primary_business_focus": primary_business_focus,
                "primary_business_focus_id": primary_business_focus_id,
                "sectors": sectors,
                "webpage_monitored": webpage_monitored,
            }
        except Exception as e:
            print(f"[Xano] overview normalization error: {e}")

    # 3) DB management roles (key people from Xano)
    if db_company:
        try:
            company_info = db_company.get("Company", db_company)
            # Current management roles - fix typo: should be "Management_Roles_current" not "Managmant_Roles_current"
            mgmt_current = company_info.get("Management_Roles_current") or company_info.get("Managmant_Roles_current") or []
            for role in mgmt_current:
                # Extract job titles from job_titles_id array
                job_titles = []
                job_titles_id = role.get("job_titles_id", [])
                if isinstance(job_titles_id, list):
                    for jt in job_titles_id:
                        if isinstance(jt, dict):
                            job_title = jt.get("job_title", "")
                            if job_title:
                                job_titles.append(job_title)

                db_management.append({
                    "Individual_text": role.get("Individual_text", ""),
                    "name": role.get("Individual_text", role.get("advisor_individuals", "")),
                    "position": ", ".join(job_titles) if job_titles else "",
                    "job_titles_id": job_titles_id,
                    "Status": role.get("Status", "Current"),
                    "linkedin_url": "",  # Not available in this endpoint
                    "current_employee_url": role.get("current_employer_url", ""),  # Note: Xano uses "current_employer_url"
                    "individuals_id": role.get("individuals_id", 0),
                    "role_id": role.get("id", 0),
                })
            # Past management roles - fix typo: should be "Management_Roles_past" not "Managmant_Roles_past"
            mgmt_past = company_info.get("Management_Roles_past") or company_info.get("Managmant_Roles_past") or []
            for role in mgmt_past:
                # Extract job titles from job_titles_id array
                job_titles = []
                job_titles_id = role.get("job_titles_id", [])
                if isinstance(job_titles_id, list):
                    for jt in job_titles_id:
                        if isinstance(jt, dict):
                            job_title = jt.get("job_title", "")
                            if job_title:
                                job_titles.append(job_title)

                db_management.append({
                    "Individual_text": role.get("Individual_text", ""),
                    "name": role.get("Individual_text", role.get("advisor_individuals", "")),
                    "position": ", ".join(job_titles) if job_titles else "",
                    "job_titles_id": job_titles_id,
                    "Status": role.get("Status", "Past"),
                    "linkedin_url": "",
                    "current_employee_url": role.get("current_employer_url", ""),  # Note: Xano uses "current_employer_url"
                    "individuals_id": role.get("individuals_id", 0),
                    "role_id": role.get("id", 0),
                })
            print(f"[Xano] Found {len(db_management)} management roles")
        except Exception as e:
            print(f"[Xano] management extraction error: {e}")

    return JSONResponse(
        {
            "existing_company": existing_company,
            "db_company": db_company,
            "db_overview": db_overview,
            "ai_overview": None,  # No AI analysis
            "db_events": db_events,
            "ai_events": [],      # No AI analysis
            "missing_events": [], # No AI analysis
            "matched_events": [], # No AI analysis
            "top_management": [], # No AI analysis
            "db_management": db_management,
        }
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
        press_page = ""
        city = ""
        country = ""
        year_founded = ""
        ceo = ""
        ownership = ""
        ai_sectors: List[Dict[str, Any]] = []
        primary_business_focus = ""
        primary_business_focus_id = 0
        
        # Primary Business Focus mapping (name -> id)
        # This maps AI-detected business focus to the predefined list with IDs
        BUSINESS_FOCUS_MAP = {
            "Financial Services": {"id": 74, "name": "Financial Services"},
            "Data & Analytics": {"id": 75, "name": "Data & Analytics"},
            "Software": {"id": 76, "name": "Software"},
            "Business Services": {"id": 77, "name": "Business Services"},
            "Consumer Internet": {"id": 78, "name": "Consumer Internet"},
            "Consumer Media": {"id": 79, "name": "Consumer Media"},
            "Aerospace": {"id": 80, "name": "Aerospace"},
            "Food": {"id": 81, "name": "Food"},
            "Insurance": {"id": 82, "name": "Insurance"},
            "Satellites": {"id": 83, "name": "Satellites"},
            "Events": {"id": 84, "name": "Events"},
            "Retail": {"id": 85, "name": "Retail"},
            "Wholesale": {"id": 86, "name": "Wholesale"},
            "Industrials": {"id": 87, "name": "Industrials"},
            "Agriculture": {"id": 88, "name": "Agriculture"},
            "Telecommunications": {"id": 89, "name": "Telecommunications"},
            "Healthcare": {"id": 90, "name": "Healthcare"},
            "Law": {"id": 91, "name": "Law"},
            "Pharmaceuticals": {"id": 92, "name": "Pharmaceuticals"},
            "Education & Training": {"id": 93, "name": "Education & Training"},
            "Real Estate": {"id": 94, "name": "Real Estate"},
            "Defence": {"id": 95, "name": "Defence"},
            "Entertainment": {"id": 96, "name": "Entertainment"},
            "Medical Equipment": {"id": 97, "name": "Medical Equipment"},
            "Laboratory Equipment": {"id": 98, "name": "Laboratory Equipment"},
            "Shipping": {"id": 99, "name": "Shipping"},
            "Academic Publishing": {"id": 100, "name": "Academic Publishing"},
            "Trade Association": {"id": 101, "name": "Trade Association"},
            "Fitness": {"id": 102, "name": "Fitness"},
            "Chemicals": {"id": 103, "name": "Chemicals"},
            "Not-for-Profit": {"id": 104, "name": "Not-for-Profit"},
            "Semiconductors": {"id": 105, "name": "Semiconductors"},
            "Natural Resources": {"id": 106, "name": "Natural Resources"},
            "Power Generation": {"id": 107, "name": "Power Generation"},
            "Consumer Electronics": {"id": 108, "name": "Consumer Electronics"},
            "Energy & Commodities": {"id": 109, "name": "Energy & Commodities"},
            "Crypto": {"id": 110, "name": "Crypto"},
            "Engineering": {"id": 111, "name": "Engineering"},
            "Aviation": {"id": 112, "name": "Aviation"},
            "Automotive": {"id": 113, "name": "Automotive"},
            "Digital Infrastructure": {"id": 114, "name": "Digital Infrastructure"},
            "Professional Body": {"id": 115, "name": "Professional Body"},
            "Manufacturing": {"id": 117, "name": "Manufacturing"},
            "Marketplace": {"id": 118, "name": "Marketplace"},
            "Business Media": {"id": 119, "name": "Business Media"},
            "Government Agency": {"id": 120, "name": "Government Agency"},
            "Real Estate Broker": {"id": 121, "name": "Real Estate Broker"},
        }
        
        def map_business_focus(ai_value: str) -> tuple[str, int]:
            """
            Map AI-detected business focus to predefined list.
            Returns (name, id) tuple.
            """
            if not ai_value or not is_valid_value(ai_value):
                return ("", 0)
            
            ai_lower = ai_value.strip()
            
            # Try exact match first
            for key, value in BUSINESS_FOCUS_MAP.items():
                if key.lower() == ai_lower.lower():
                    return (value["name"], value["id"])
            
            # Try fuzzy matching for common variations
            fuzzy_matches = {
                "fintech": "Financial Services",
                "banking": "Financial Services",
                "payments": "Financial Services",
                "data analytics": "Data & Analytics",
                "analytics": "Data & Analytics",
                "big data": "Data & Analytics",
                "saas": "Software",
                "software as a service": "Software",
                "enterprise software": "Software",
                "b2b services": "Business Services",
                "professional services": "Business Services",
                "consulting": "Business Services",
                "e-commerce": "Consumer Internet",
                "online marketplace": "Marketplace",
                "marketplace": "Marketplace",
                "ecommerce": "Consumer Internet",
                "pharma": "Pharmaceuticals",
                "pharmaceutical": "Pharmaceuticals",
                "drug development": "Pharmaceuticals",
                "medical devices": "Medical Equipment",
                "healthcare services": "Healthcare",
                "health tech": "Healthcare",
                "healthtech": "Healthcare",
                "telecom": "Telecommunications",
                "telecommunications": "Telecommunications",
                "defense": "Defence",
                "defence": "Defence",
                "non-profit": "Not-for-Profit",
                "nonprofit": "Not-for-Profit",
                "nfp": "Not-for-Profit",
                "energy": "Energy & Commodities",
                "commodities": "Energy & Commodities",
                "cryptocurrency": "Crypto",
                "blockchain": "Crypto",
                "real estate": "Real Estate",
                "property": "Real Estate",
            }
            
            for fuzzy_key, mapped_name in fuzzy_matches.items():
                if fuzzy_key in ai_lower:
                    mapped = BUSINESS_FOCUS_MAP.get(mapped_name)
                    if mapped:
                        return (mapped["name"], mapped["id"])
            
            # If no match found, return the AI value as-is with ID 0
            print(f"[AI] Business focus '{ai_value}' not found in predefined list - using as-is")
            return (ai_value.strip(), 0)
        
        # Country normalization mapping (DB standard names)
        country_normalization = {
            # UK variations
            "england": "UK",
            "scotland": "UK", 
            "wales": "UK",
            "northern ireland": "UK",
            "britain": "UK",
            "great britain": "UK",
            "united kingdom": "UK",
            "u.k.": "UK",
            "u.k": "UK",
            # USA variations
            "united states": "USA",
            "united states of america": "USA",
            "america": "USA",
            "us": "USA",
            "u.s.": "USA",
            "u.s.a.": "USA",
            "u.s.a": "USA",
            # UAE variations
            "united arab emirates": "UAE",
            "u.a.e.": "UAE",
            # Other common normalizations
            "the netherlands": "Netherlands",
            "holland": "Netherlands",
            "republic of ireland": "Ireland",
            "south korea": "Korea",
            "republic of korea": "Korea",
        }
        
        def normalize_country(country_val: str) -> str:
            """Normalize country name to DB standard"""
            if not country_val:
                return ""
            normalized = country_normalization.get(country_val.lower().strip(), country_val)
            return normalized
        
        # Valid ownership status values - comprehensive classification
        valid_ownership_types = {
            # Public vs Private
            "public", "private",
            # By Investor/Owner Type
            "venture-backed", "private equity-backed", "family-owned",
            "employee-owned", "founder-owned", "institutional-owned",
            # Special Categories
            "government-owned", "non-profit", "subsidiary",
            "cooperative", "partnership"
        }
        
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
            elif "press page:" in low or "press-page:" in low or "presspage:" in low:
                val = line.split(":", 1)[-1].strip()
                url = extract_url(val)
                if is_valid_value(url) and ("http://" in url or "https://" in url):
                    press_page = url
            elif "headquarters:" in low:
                hq = line.split(":", 1)[-1].strip()
                if is_valid_value(hq):
                    # Very rough split city / country by last comma
                    if "," in hq:
                        parts = [p.strip() for p in hq.split(",")]
                        if len(parts) >= 2:
                            city = ", ".join(parts[:-1])
                            country = normalize_country(parts[-1])
                        else:
                            city = hq
                    else:
                        city = hq
            elif "ownership status:" in low or "ownership:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    val_lower = val.lower().strip()
                    
                    # Smart matching for ownership types
                    # Check for PUBLIC indicators first (most important distinction)
                    public_indicators = ["public", "publicly traded", "publicly held", "listed", "nasdaq", "nyse", "lse", "stock exchange", "ipo"]
                    if any(ind in val_lower for ind in public_indicators):
                        ownership = "Public"
                    # Check for PE-backed
                    elif "private equity" in val_lower or "pe-backed" in val_lower or "pe backed" in val_lower:
                        ownership = "Private Equity-Backed"
                    # Check for VC-backed
                    elif "venture" in val_lower or "vc-backed" in val_lower or "vc backed" in val_lower:
                        ownership = "Venture-Backed"
                    # Check for government
                    elif "government" in val_lower or "state-owned" in val_lower or "state owned" in val_lower:
                        ownership = "Government-Owned"
                    # Check for non-profit
                    elif "non-profit" in val_lower or "nonprofit" in val_lower or "not-for-profit" in val_lower:
                        ownership = "Non-Profit"
                    # Check for family
                    elif "family" in val_lower:
                        ownership = "Family-Owned"
                    # Check for employee
                    elif "employee" in val_lower or "esop" in val_lower:
                        ownership = "Employee-Owned"
                    # Check for founder
                    elif "founder" in val_lower:
                        ownership = "Founder-Owned"
                    # Check for subsidiary
                    elif "subsidiary" in val_lower or "owned by" in val_lower:
                        ownership = "Subsidiary"
                    # Check for institutional
                    elif "institutional" in val_lower:
                        ownership = "Institutional-Owned"
                    # Check for partnership
                    elif "partnership" in val_lower or "llp" in val_lower:
                        ownership = "Partnership"
                    # Check for cooperative
                    elif "cooperative" in val_lower or "co-op" in val_lower:
                        ownership = "Cooperative"
                    # Default to Private if nothing else matches but it's clearly private
                    elif "private" in val_lower:
                        ownership = "Private"
                    # Exact match fallback
                    elif val_lower in valid_ownership_types:
                        ownership = val.strip()
                    else:
                        # Log for debugging
                        print(f"[AI] Unknown ownership value: '{val}' - defaulting to empty")
            elif "primary business focus:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    mapped_name, mapped_id = map_business_focus(val)
                    primary_business_focus = mapped_name
                    primary_business_focus_id = mapped_id
                    if mapped_id > 0:
                        print(f"[AI] Mapped business focus '{val}' -> '{mapped_name}' (ID: {mapped_id})")
                    else:
                        print(f"[AI] Business focus '{val}' not mapped - using as-is")
            elif "primary sectors:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    # Expect comma-separated list of sector names
                    parts = [p.strip() for p in val.split(",") if p.strip()]
                    for s in parts:
                        ai_sectors.append({"sector": s, "importance": "Primary"})
            elif "secondary sectors:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val) and val.lower().strip() not in ["none", "n/a", "unknown"]:
                    parts = [p.strip() for p in val.split(",") if p.strip()]
                    for s in parts:
                        ai_sectors.append({"sector": s, "importance": "Secondary"})
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
            "ownership": ownership,
            "website": website,
            "linkedin": linkedin,
            "webpage_monitored": press_page,  # Press/news page URL
            "description": description or "",
            "year_founded": year_founded,
            "ceo": ceo,
            "sectors": ai_sectors,
            "primary_business_focus": primary_business_focus,
            "primary_business_focus_id": primary_business_focus_id,
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

    # 5) DB management roles (key people from Xano)
    db_management = []
    if db_company:
        try:
            company_info = db_company.get("Company", db_company)
            # Current management roles - fix typo: should be "Management_Roles_current" not "Managmant_Roles_current"
            mgmt_current = company_info.get("Management_Roles_current") or company_info.get("Managmant_Roles_current") or []
            for role in mgmt_current:
                # Extract job titles from job_titles_id array
                job_titles = []
                job_titles_id = role.get("job_titles_id", [])
                if isinstance(job_titles_id, list):
                    for jt in job_titles_id:
                        if isinstance(jt, dict):
                            job_title = jt.get("job_title", "")
                            if job_title:
                                job_titles.append(job_title)
                
                db_management.append({
                    "Individual_text": role.get("Individual_text", ""),
                    "name": role.get("Individual_text", role.get("advisor_individuals", "")),
                    "position": ", ".join(job_titles) if job_titles else "",
                    "job_titles_id": job_titles_id,
                    "Status": role.get("Status", "Current"),
                    "linkedin_url": "",  # Not available in this endpoint
                    "current_employee_url": role.get("current_employer_url", ""),  # Note: Xano uses "current_employer_url"
                    "individuals_id": role.get("individuals_id", 0),
                    "role_id": role.get("id", 0),
                })
            # Past management roles - fix typo: should be "Management_Roles_past" not "Managmant_Roles_past"
            mgmt_past = company_info.get("Management_Roles_past") or company_info.get("Managmant_Roles_past") or []
            for role in mgmt_past:
                # Extract job titles from job_titles_id array
                job_titles = []
                job_titles_id = role.get("job_titles_id", [])
                if isinstance(job_titles_id, list):
                    for jt in job_titles_id:
                        if isinstance(jt, dict):
                            job_title = jt.get("job_title", "")
                            if job_title:
                                job_titles.append(job_title)
                
                db_management.append({
                    "Individual_text": role.get("Individual_text", ""),
                    "name": role.get("Individual_text", role.get("advisor_individuals", "")),
                    "position": ", ".join(job_titles) if job_titles else "",
                    "job_titles_id": job_titles_id,
                    "Status": role.get("Status", "Past"),
                    "linkedin_url": "",
                    "current_employee_url": role.get("current_employer_url", ""),  # Note: Xano uses "current_employer_url"
                    "individuals_id": role.get("individuals_id", 0),
                    "role_id": role.get("id", 0),
                })
            print(f"[Xano] Found {len(db_management)} management roles")
        except Exception as e:
            print(f"[Xano] management extraction error: {e}")

    # 6) DB overview (normalize to same structure as AI side)
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
            
            # Year founded - check _years.Year first (proper structure), fallback to year_founded
            years_block = company_info.get("_years") or {}
            if isinstance(years_block, dict) and years_block.get("Year"):
                year_founded = str(years_block.get("Year"))
            else:
                # Fallback to direct year_founded field (but ignore if it's just a reference ID like 10)
                year_founded_raw = company_info.get("year_founded", 0)
                # If year_founded is a 4-digit number (actual year), use it; otherwise treat as empty
                if year_founded_raw and isinstance(year_founded_raw, (int, str)):
                    year_str = str(year_founded_raw)
                    if len(year_str) == 4 and year_str.isdigit():
                        year_founded = year_str
                    else:
                        year_founded = ""
                else:
                    year_founded = ""
            
            # Primary business focus - can be nested object or direct reference
            business_focus_block = company_info.get("primary_business_focus_id") or company_info.get("_primary_business_focus") or {}
            if isinstance(business_focus_block, dict):
                primary_business_focus = business_focus_block.get("business_focus", "") or business_focus_block.get("primary_business_focus", "") or business_focus_block.get("name", "")
                primary_business_focus_id = business_focus_block.get("id", 0)
            else:
                primary_business_focus = ""
                primary_business_focus_id = business_focus_block if isinstance(business_focus_block, int) else 0
            
            # Sectors - try new_sectors_data first (has actual sector names), fallback to sectors_id
            sectors = []
            primary_sectors = []
            secondary_sectors = []
            
            # First check new_sectors_data (JSON string payload with real data)
            new_sectors_data = company_info.get("new_sectors_data") or []
            if isinstance(new_sectors_data, list) and len(new_sectors_data) > 0:
                try:
                    sectors_payload = new_sectors_data[0].get("sectors_payload", "")
                    if sectors_payload:
                        import json
                        parsed_sectors = json.loads(sectors_payload)
                        # Extract primary sectors
                        for s in parsed_sectors.get("primary_sectors", []):
                            if isinstance(s, dict) and s.get("sector_name"):
                                primary_sectors.append({
                                    "sector": s.get("sector_name"),
                                    "id": s.get("id", 0),
                                    "importance": "Primary"
                                })
                        # Extract secondary sectors
                        for s in parsed_sectors.get("secondary_sectors", []):
                            if isinstance(s, dict) and s.get("sector_name"):
                                secondary_sectors.append({
                                    "sector": s.get("sector_name"),
                                    "id": s.get("id", 0),
                                    "importance": "Secondary"
                                })
                        sectors = primary_sectors + secondary_sectors
                except Exception as e:
                    print(f"[Xano] new_sectors_data parse error: {e}")
            
            # Fallback to sectors_id if new_sectors_data didn't yield results
            if not sectors:
                sectors_data = company_info.get("sectors_id") or company_info.get("_sectors") or company_info.get("sectors") or []
                if isinstance(sectors_data, list):
                    for s in sectors_data:
                        if isinstance(s, dict):
                            sector_name = s.get("sector_name") or s.get("sector") or s.get("name", "")
                            importance = s.get("Sector_importance", "")
                            if sector_name and sector_name.strip():
                                sectors.append({"sector": sector_name, "id": s.get("id", 0), "importance": importance})
                        elif isinstance(s, str) and s.strip():
                            sectors.append({"sector": s})
            
            # Webpage monitored (press/news page URL)
            webpage_monitored = company_info.get("webpage_monitored", "") or company_info.get("press_page_url", "") or company_info.get("news_url", "") or ""
            
            db_overview = {
                "name": name,
                "city": city,
                "country": country,
                "ownership": ownership,
                "website": website,
                "linkedin": linkedin,
                "description": desc,
                "year_founded": year_founded,
                "primary_business_focus": primary_business_focus,
                "primary_business_focus_id": primary_business_focus_id,
                "sectors": sectors,
                "webpage_monitored": webpage_monitored,
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
            "db_management": db_management,
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


