import os
import json
import re
import traceback
import urllib.parse
from datetime import datetime
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    openrouter_chat,
    detect_ownership_from_description,
    enrich_with_yahoo_finance,
)

import requests
from bs4 import BeautifulSoup


def _parse_openrouter_json(raw: str) -> dict:
    """
    Best-effort parser for OpenRouter responses that should be JSON.
    Accepts raw JSON or JSON wrapped in code fences.
    """
    if not raw:
        return {}
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalize_location_with_ai(person_name: str, company_name: str, position: str, linkedin_url: str, loc: dict) -> dict:
    """
    Mandatory normalization step: expand US state abbreviations (e.g., CA->California),
    standardize to our canonical format, and ensure {city,state,country}.
    Uses OpenRouter for normalization; falls back to original loc if parsing fails.
    """
    if not loc:
        return {"city": "", "state": "", "country": ""}

    raw_location = ", ".join([str(loc.get("city", "")).strip(), str(loc.get("state", "")).strip(), str(loc.get("country", "")).strip()]).strip(", ").strip()

    prompt = f"""
You are a location normalizer.
Return STRICT one-line minified JSON (no code fences) with keys: {{"city":"","state":"","country":""}}.

Our canonical standard is:
- city: city/metro name (e.g., "Chicago")
- state: state/province/region (full name, not abbreviation) (e.g., "Illinois")
- country: full English country name (e.g., "United States")

Rules:
- Expand abbreviations where possible:
  - US states: CA->California, NY->New York, WA->Washington, MA->Massachusetts, IL->Illinois, TX->Texas, FL->Florida, CO->Colorado, GA->Georgia, AZ->Arizona, OR->Oregon, DC->District of Columbia.
  - Countries: US/USA/U.S. -> United States; UK/U.K. -> United Kingdom; UAE -> United Arab Emirates.
- If a location implies a country but it’s missing, infer it.
- If the location is a region/metro (e.g., "Washington DC-Baltimore Area"), choose the best canonical city/state/country (e.g., city="Washington", state="District of Columbia", country="United States") if reasonable.
- Never return abbreviations in state or country. Prefer full names.
- If unknown, use empty strings.

Context:
- Person: {person_name}
- Company: {company_name}
- Position: {position}
- LinkedIn URL: {linkedin_url}
- Raw extracted location: {raw_location}
"""

    try:
        raw = openrouter_chat("openai/gpt-4o-mini", prompt, f"normalize-location-{person_name[:32]}")
        parsed = _parse_openrouter_json(raw)
        out = {
            "city": (parsed.get("city") or "").strip(),
            "state": (parsed.get("state") or "").strip(),
            "country": (parsed.get("country") or "").strip(),
        }
        # Lightweight final normalization / guardrails (global-friendly)
        country_norm = {
            "usa": "United States",
            "us": "United States",
            "u.s.": "United States",
            "u.s.a.": "United States",
            "united states of america": "United States",
            "uk": "United Kingdom",
            "u.k.": "United Kingdom",
            "united kingdom of great britain and northern ireland": "United Kingdom",
            "uae": "United Arab Emirates",
        }
        if out["country"]:
            out["country"] = country_norm.get(out["country"].strip().lower(), out["country"])
        # "DC" -> "District of Columbia" if AI leaves it abbreviated
        if out["state"].strip().upper() == "DC":
            out["state"] = "District of Columbia"
        # If AI returned nothing useful, fall back
        if any(out.values()):
            return out
    except Exception as e:
        print(f"⚠️ Location normalization AI error: {e}")

    return {
        "city": (loc.get("city") or "").strip(),
        "state": (loc.get("state") or "").strip(),
        "country": (loc.get("country") or "").strip(),
    }


def _extract_location_from_serpapi_with_ai(person_name: str, company_name: str, position: str, organic_results: list) -> dict:
    """
    Use AI (OpenRouter) to analyze SerpAPI organic_results and extract the person's location.
    This is more reliable than regex patterns as the LLM can understand context.
    
    Args:
        person_name: The person's name
        company_name: The company name
        position: The person's position/title
        organic_results: List of SerpAPI organic_results (each has title, snippet, link)
        
    Returns:
        Dict with keys: city, state, country (any may be empty string)
    """
    if not organic_results:
        return {"city": "", "state": "", "country": ""}
    
    # Format the results for the AI prompt
    results_text = ""
    for i, r in enumerate(organic_results[:10], 1):
        title = r.get("title", "").strip()
        snippet = r.get("snippet", "").strip()
        link = r.get("link", "").strip()
        source = r.get("source", "").strip()
        results_text += f"\n{i}. Title: {title}\n   Snippet: {snippet}\n   Link: {link}\n   Source: {source}\n"
    
    prompt = f"""You are analyzing Google search results to find WHERE AN INDIVIDUAL PERSON LIVES/IS BASED (city/state/country).

**Person:** {person_name}
**Company they work for:** {company_name}
**Position:** {position}

**Search Results:**
{results_text}

**CRITICAL INSTRUCTIONS:**

1. Find the INDIVIDUAL PERSON'S location - NOT the company's headquarters!
   - A person can work for "London Stock Exchange" but live in Chicago
   - A person can be CEO of a Paris company but be based in New York
   - IGNORE company addresses, office locations, and corporate headquarters

2. Look for PERSONAL location indicators:
   - LinkedIn profile titles: "Name - City, State, Country" (MOST RELIABLE)
   - Phrases like "based in [City]", "lives in [City]", "[Name] of [City]"
   - Personal LinkedIn snippets showing "Location: [City]"
   - Biography mentions of where the person resides

3. What to IGNORE:
   - Company headquarters addresses (e.g., "10 Paternoster Square, London" is an office address)
   - "c/o Company Address" - this is a mailing address, not personal location
   - Generic company location mentions

4. PRIORITIZE LinkedIn results (linkedin.com/in/) - the title format "Name - Location" is the person's actual location

5. If the LinkedIn title shows a different location than the company's headquarters, USE THE LINKEDIN LOCATION - that's where the person actually is.

Return the person's location in this exact JSON format (NO code fences, just raw JSON):
{{"city": "CityName", "state": "StateName", "country": "CountryName"}}

**Rules:**
- Use FULL names, not abbreviations (e.g., "California" not "CA", "United Kingdom" not "UK")
- For "Greater X Area" locations, use the main city (e.g., "Greater Chicago Area" → city="Chicago", state="Illinois", country="United States")
- If you can't determine a field, use empty string ""
- For UK locations, state is the county/region (e.g., "England", "Greater London")

Return ONLY the JSON object, nothing else."""

    try:
        raw = openrouter_chat("openai/gpt-4o-mini", prompt, f"extract-location-{person_name[:32]}")
        parsed = _parse_openrouter_json(raw)
        out = {
            "city": (parsed.get("city") or "").strip(),
            "state": (parsed.get("state") or "").strip(),
            "country": (parsed.get("country") or "").strip(),
        }
        
        # Lightweight normalization
        country_norm = {
            "usa": "United States",
            "us": "United States",
            "u.s.": "United States",
            "united states of america": "United States",
            "uk": "United Kingdom",
            "u.k.": "United Kingdom",
            "uae": "United Arab Emirates",
        }
        if out["country"]:
            out["country"] = country_norm.get(out["country"].strip().lower(), out["country"])
            
        if out["state"].strip().upper() == "DC":
            out["state"] = "District of Columbia"
            
        print(f"🤖 AI extracted location for {person_name}: {out}")
        return out
        
    except Exception as e:
        print(f"⚠️ AI location extraction error: {e}")
        return {"city": "", "state": "", "country": ""}


# ============================================================
# 🔹 Environment
# ============================================================
load_dotenv()

XANO_BASE_URL = "https://xdil-abvj-o7rq.e2.xano.io"
XANO_EMAIL    = os.getenv("XANO_EMAIL", "").strip()
XANO_PASSWORD = os.getenv("XANO_PASSWORD", "").strip()
SCRAPFLY_KEY  = os.getenv("SCRAPFLY_KEY", "").strip()

# ── Xano auth token (fetched once at startup, refreshed on 401) ──────────────
_xano_token: str = ""

def _xano_login() -> str:
    """POST credentials to Xano auth and return the bearer token."""
    if not XANO_EMAIL or not XANO_PASSWORD:
        print("⚠️  XANO_EMAIL / XANO_PASSWORD not set — investor search will be unavailable")
        return ""
    try:
        resp = requests.post(
            f"{XANO_BASE_URL}/api:vnXelut6/auth/login",
            json={"email": XANO_EMAIL, "password": XANO_PASSWORD},
            timeout=10,
        )
        resp.raise_for_status()
        token = resp.json().get("authToken", "")
        if token:
            print("🔑 Xano auth token obtained successfully")
        else:
            print("⚠️  Xano login succeeded but no authToken in response")
        return token
    except Exception as e:
        print(f"⚠️  Xano login failed: {e}")
        return ""

def _get_xano_token(force_refresh: bool = False) -> str:
    """Return cached token, refreshing if missing or forced."""
    global _xano_token
    if not _xano_token or force_refresh:
        _xano_token = _xano_login()
    return _xano_token

# Attempt login at import time so the token is ready before the first request
_xano_token = _xano_login()
# ─────────────────────────────────────────────────────────────────────────────

# ============================================================
# 🔹 FastAPI setup (placed early so routes can use `app`)
# ============================================================
app = FastAPI(title="SearXNG – Events UI (No Streamlit)")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


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
        
        # Handle list response (API returns [{"id":..., "name":..., "url":...}])
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                return data[0]
            return None
        
        # Handle dict response (legacy/fallback)
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


def _normalize_company_linkedin_url(url: str) -> str:
    """Normalize LinkedIn company/school URLs for stable comparisons."""
    candidate = (url or "").strip()
    if not candidate:
        return ""

    try:
        parsed = urllib.parse.urlparse(candidate)
        path = (parsed.path or "").strip("/")
        match = re.match(r"^(company|school)/([A-Za-z0-9_-]+)$", path, re.I)
        if not match:
            return ""
        entity_type, slug = match.groups()
        return f"https://www.linkedin.com/{entity_type.lower()}/{slug.lower()}/"
    except Exception:
        return ""


def _extract_company_linkedin_from_xano_payload(db_company: Optional[dict]) -> str:
    """Read the canonical LinkedIn URL from the Xano company payload if present."""
    if not isinstance(db_company, dict):
        return ""

    company_info = db_company.get("Company", db_company)
    if not isinstance(company_info, dict):
        return ""

    linkedin_block = company_info.get("linkedin_data") or {}
    if not isinstance(linkedin_block, dict):
        return ""

    return _normalize_company_linkedin_url(linkedin_block.get("LinkedIn_URL", ""))


def _is_press_section_url(url: str) -> bool:
    """
    Returns True only if the URL looks like a news/press *section* page
    (e.g. /news, /newsroom, /press-releases) — NOT an individual article.

    Rejects:
    - URLs whose last path segment is too long (likely an article slug)
    - URLs deeper than 4 path levels (likely an article under a dated folder)
    - URLs whose path contains no known press/news keyword at all
    """
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/").lower()
        segments = [s for s in path.split("/") if s]

        # Known section-level keywords
        SECTION_KEYWORDS = {
            "news", "press", "newsroom", "press-releases", "press-room",
            "media", "media-center", "media-room", "announcements",
            "updates", "in-the-news", "coverage", "articles",
            "blog", "insights", "resources", "category",
        }

        # Reject if path has no press/news keyword anywhere
        if not any(kw in path for kw in SECTION_KEYWORDS):
            return False

        # Reject if too many path levels (article under dated folder)
        if len(segments) > 4:
            return False

        # Reject if last segment looks like an article slug (very long or contains year-month)
        last = segments[-1] if segments else ""
        if len(last) > 60:
            return False
        if re.search(r'\d{4}[-/]\d{2}', last):  # e.g. 2024-01 in slug
            return False

        return True
    except Exception:
        return False


def search_press_page(url_or_domain: str, company_name: str = "") -> str:
    """
    Find a press/news *section* page for a company.
    Returns empty string if only article-level pages are found.
    """
    try:
        parsed = urllib.parse.urlparse(url_or_domain)
        domain = parsed.netloc or parsed.path
        domain = domain.replace("www.", "")
        if not domain:
            domain = url_or_domain
        base_domain = domain.lower()

        queries = [
            f"{base_domain} newsroom",
            f"{base_domain} press releases",
            f"{base_domain} news",
        ]
        if company_name:
            queries.append(f'"{company_name}" press releases site:{base_domain}')
            queries.append(f'"{company_name}" newsroom site:{base_domain}')

        _headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        def _fetch_press_query(q: str):
            encoded = urllib.parse.quote_plus(q)
            search_url = f"https://www.startpage.com/sp/search?query={encoded}"
            try:
                resp = requests.get(search_url, headers=_headers, timeout=12)
                if resp.status_code != 200:
                    return None
                links = re.findall(r'href="(https?://[^"]+)"', resp.text, re.I)
                for link in links:
                    if base_domain in link.lower() and _is_press_section_url(link):
                        return link
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=len(queries)) as _press_ex:
            futures = {_press_ex.submit(_fetch_press_query, q): q for q in queries}
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    print(f"[PressPage] Found section page: {result}")
                    return result

        print(f"[PressPage] No valid press section page found for {base_domain}")
        return ""
    except Exception as e:
        print(f"[PressPage] search error: {e}")
        return ""


# ============================================================
# 🔹 Lightweight web corroboration helpers (Startpage)
# ============================================================
def _url_domain(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _startpage_search_urls(query: str, max_urls: int = 8) -> List[str]:
    """
    Very lightweight HTML scraping of Startpage results.
    Returns a de-duplicated list of candidate URLs.
    """
    try:
        encoded = urllib.parse.quote_plus((query or "").strip())
        if not encoded:
            return []
        search_url = f"https://www.startpage.com/sp/search?query={encoded}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        resp = requests.get(search_url, headers=headers, timeout=12)
        if resp.status_code != 200 or not resp.text:
            return []

        # Startpage pages contain many links; filter out obvious non-result domains
        raw_links = re.findall(r'href="(https?://[^"]+)"', resp.text, re.I)
        out: List[str] = []
        seen = set()
        for link in raw_links:
            if not link or not link.startswith("http"):
                continue
            d = _url_domain(link)
            if not d:
                continue
            if any(bad in d for bad in ["startpage.com", "addthis.com", "doubleclick.net", "google.com", "bing.com"]):
                continue
            # Avoid common trackers / redirects
            if "startpage.com" in link.lower():
                continue
            if link in seen:
                continue
            seen.add(link)
            out.append(link)
            if len(out) >= max_urls:
                break
        return out
    except Exception as e:
        print(f"[Startpage] search error: {e}")
        return []


def extract_publish_date_from_html(html: str) -> str:
    """
    Best-effort extraction of an article publish date (YYYY-MM-DD) from HTML metadata/JSON-LD.
    """
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")

        # JSON-LD datePublished is most common
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            candidates = []
            if isinstance(data, dict):
                candidates.append(data)
            elif isinstance(data, list):
                candidates.extend([x for x in data if isinstance(x, dict)])
            for obj in candidates:
                for key in ["datePublished", "dateCreated", "dateModified", "date"]:
                    val = obj.get(key)
                    if isinstance(val, str):
                        dt = parse_date_flexible(val)
                        if dt:
                            return dt.strftime("%Y-%m-%d")

        # Meta tags
        meta = (
            soup.find("meta", property="article:published_time")
            or soup.find("meta", attrs={"name": "date"})
            or soup.find("meta", attrs={"name": "pubdate"})
            or soup.find("meta", attrs={"name": "publishdate"})
            or soup.find("meta", property="og:updated_time")
        )
        if meta and meta.get("content"):
            dt = parse_date_flexible(meta["content"])
            if dt:
                return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""
    return ""


def fetch_text_brief(url: str, max_chars: int = 3500) -> Dict[str, str]:
    """
    Fetch a URL and return a compact, prompt-friendly dict:
    { url, domain, publish_date, title, text }
    """
    try:
        html = fetch_html(url, force_scrapfly=True)
        if not html:
            return {"url": url, "domain": _url_domain(url), "publish_date": "", "title": "", "text": ""}
        soup = BeautifulSoup(html, "html.parser")
        title = ""
        if soup.title and soup.title.get_text(strip=True):
            title = soup.title.get_text(" ", strip=True)[:200]
        publish_date = extract_publish_date_from_html(html)
        text = soup.get_text(" ", strip=True)
        text = (text or "")[:max_chars]
        return {
            "url": url,
            "domain": _url_domain(url),
            "publish_date": publish_date,
            "title": title,
            "text": text,
        }
    except Exception as e:
        print(f"[fetch_text_brief] error for {url}: {e}")
        return {"url": url, "domain": _url_domain(url), "publish_date": "", "title": "", "text": ""}


def get_corroborating_sources(
    *,
    title: str,
    company: str,
    source_url: str,
    max_sources: int = 3,
) -> List[Dict[str, str]]:
    """
    Search for corroborating sources and return brief extracted texts.
    """
    base_domain = _url_domain(source_url)
    q = " ".join([p for p in [company, title, "deal", "closed"] if p]).strip()
    urls = _startpage_search_urls(q, max_urls=12)

    # Filter & de-dup
    out: List[Dict[str, str]] = []
    seen_domains = set([base_domain]) if base_domain else set()
    seen_urls = set([source_url]) if source_url else set()
    for u in urls:
        if u in seen_urls:
            continue
        seen_urls.add(u)
        d = _url_domain(u)
        if not d:
            continue
        # Avoid pulling the same domain repeatedly (paywalls/tracking); still allow one
        if d in seen_domains:
            continue
        # Skip obvious low-signal domains
        if any(x in d for x in ["facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "reddit.com", "tiktok.com"]):
            continue
        seen_domains.add(d)
        brief = fetch_text_brief(u)
        if brief.get("text"):
            out.append(brief)
        if len(out) >= max_sources:
            break
    return out

# ============================================================
# 🔹 Text helpers
# ============================================================
def strip_marketing_phrases(text: str) -> str:
    """
    Remove boilerplate call-to-action phrases that models sometimes append,
    like 'for more information visit company website'.
    """
    if not text:
        return text

    patterns = [
        r"\s*for more information[^.\n]*[.\n]?",
        r"\s*for further information[^.\n]*[.\n]?",
        r"\s*visit (the )?company website[^.\n]*[.\n]?",
        r"\s*more information can be found on (their )?official website[^.\n]*[.\n]?",
    ]

    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)

    return cleaned.strip()


# ============================================================
# 🔹 Utilities for lightweight page parsing (IMPROVED)
# ============================================================
DATE_PATTERNS = [
    "%Y-%m-%d",           # 2024-06-20
    "%B %d, %Y",         # June 20, 2024
    "%b %d, %Y",         # Jun 20, 2024
    "%d %B %Y",          # 20 June 2024
    "%d %b %Y",          # 20 Jun 2024
    "%m/%d/%Y",          # 06/20/2024
    "%d/%m/%Y",          # 20/06/2024
    "%Y/%m/%d",          # 2024/06/20
    "%d.%m.%Y",          # 20.06.2024
    "%B %Y",             # June 2024
    "%b %Y",             # Jun 2024
    "%Y",                # 2024
]

# Date context keywords
ANNOUNCEMENT_KEYWORDS = [
    "announced", "announcement", "announcing", "announces",
    "press release", "disclosed", "disclosure", "revealed",
    "unveiled", "introduced", "launched", "published",
    "dated", "dated on", "as of",
    # Avoid bare "on" — it matches almost any sentence and picks founding/historical dates.
]

# Snippet context that usually refers to company history, not deal announcement
DATE_HISTORICAL_NOISE = [
    "founded in", "founded on", "established in", "established on",
    "since 19", "since 20", "originally founded", "originally established",
]
DEAL_CONTEXT_HINTS = (
    "acquisition", "acquire", "acquired", "merger", "investment",
    "funding", "series ", "round", "deal", "transaction", "announced",
    "press release", "closing", "completed acquisition", "takeover",
)

CLOSED_KEYWORDS = [
    "closed", "completed", "finalized", "finalised",
    "consummated", "signed", "executed", "completed on",
    "closed on", "finalized on", "signed on"
]


def parse_date_flexible(date_str: str) -> Optional[datetime]:
    """Parse date string using multiple formats."""
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    
    # Remove common prefixes/suffixes
    date_str = re.sub(r'^(on|as of|dated|dated on)\s+', '', date_str, flags=re.IGNORECASE)
    date_str = date_str.split('T')[0]  # Remove time if present
    
    for fmt in DATE_PATTERNS:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            continue
    
    return None


def extract_first_date(text: str) -> str:
    """
    Legacy function for backward compatibility.
    Returns first date found (announcement_date if available).
    """
    dates = extract_dates_with_context(text)
    return dates.get("announcement_date", "")


def extract_dates_with_context(text: str) -> Dict[str, str]:
    """
    Extract dates with context awareness - looks for announcement_date and closed_date
    based on surrounding keywords.
    
    Returns: {"announcement_date": "YYYY-MM-DD", "closed_date": "YYYY-MM-DD"}
    """
    result = {"announcement_date": "", "closed_date": ""}
    
    if not text:
        return result
    
    # Normalize text for better matching
    text_lower = text.lower()
    
    # Find all potential dates with their positions
    date_candidates = []
    
    # Pattern 1: ISO dates (YYYY-MM-DD)
    for match in re.finditer(r'\b(\d{4}-\d{2}-\d{2})\b', text):
        date_str = match.group(1)
        dt = parse_date_flexible(date_str)
        if dt:
            date_candidates.append({
                "date": dt,
                "pos": match.start(),
                "text": date_str,
                "context": text[max(0, match.start()-100):match.end()+100].lower()
            })
    
    # Pattern 2: Month DD, YYYY
    for match in re.finditer(r'\b([A-Z][a-z]+ \d{1,2}, \d{4})\b', text):
        date_str = match.group(1)
        dt = parse_date_flexible(date_str)
        if dt:
            date_candidates.append({
                "date": dt,
                "pos": match.start(),
                "text": date_str,
                "context": text[max(0, match.start()-100):match.end()+100].lower()
            })
    
    # Pattern 3: DD/MM/YYYY or DD.MM.YYYY
    for match in re.finditer(r'\b(\d{1,2}[./]\d{1,2}[./]\d{2,4})\b', text):
        date_str = match.group(1)
        dt = parse_date_flexible(date_str)
        if dt:
            date_candidates.append({
                "date": dt,
                "pos": match.start(),
                "text": date_str,
                "context": text[max(0, match.start()-100):match.end()+100].lower()
            })
    
    # Pattern 4: DD Month YYYY
    for match in re.finditer(r'\b(\d{1,2} [A-Z][a-z]+ \d{4})\b', text):
        date_str = match.group(1)
        dt = parse_date_flexible(date_str)
        if dt:
            date_candidates.append({
                "date": dt,
                "pos": match.start(),
                "text": date_str,
                "context": text[max(0, match.start()-100):match.end()+100].lower()
            })
    
    # Sort by position (earlier in text often = headline; historical filler can precede)
    date_candidates.sort(key=lambda x: x["pos"])

    def _context_is_historical_noise(ctx: str) -> bool:
        cl = (ctx or "").lower()
        if not any(noise in cl for noise in DATE_HISTORICAL_NOISE):
            return False
        return not any(h in cl for h in DEAL_CONTEXT_HINTS)

    # Classify dates based on context
    for candidate in date_candidates:
        if _context_is_historical_noise(candidate["context"]):
            continue
        context = candidate["context"]
        date_str = candidate["date"].strftime("%Y-%m-%d")
        
        # Check for announcement keywords
        if not result["announcement_date"]:
            for keyword in ANNOUNCEMENT_KEYWORDS:
                if keyword in context:
                    result["announcement_date"] = date_str
                    break
        
        # Check for closed/completed keywords
        if not result["closed_date"]:
            for keyword in CLOSED_KEYWORDS:
                if keyword in context:
                    result["closed_date"] = date_str
                    break
    
    # If we found dates but didn't classify them, prefer first non-historical-noise candidate
    if not result["announcement_date"] and date_candidates:
        usable = [c for c in date_candidates if not _context_is_historical_noise(c["context"])]
        pool = usable if usable else date_candidates
        result["announcement_date"] = pool[0]["date"].strftime("%Y-%m-%d")
    
    # If we found a second date and no closed_date, use it as closed_date
    if not result["closed_date"] and len(date_candidates) > 1:
        # Prefer second usable candidate when first was announcement
        usable = [c for c in date_candidates if not _context_is_historical_noise(c["context"])]
        pool = usable if usable else date_candidates
        if len(pool) > 1:
            result["closed_date"] = pool[1]["date"].strftime("%Y-%m-%d")
    
    return result


def extract_investment_fields(text: str) -> Dict[str, Any]:
    """
    Improved heuristics to extract investment amount (millions), currency, and funding stage from text.
    """
    result = {}
    
    if not text:
        return result
    
    text_lower = text.lower()
    
    # Funding stages (improved patterns)
    stage_patterns = [
        (r"\bseed\s+round\b", "Seed"),
        (r"\bpre-?seed\b", "Pre-Seed"),
        (r"\bseries\s*a\b", "Series A"),
        (r"\bseries\s*b\b", "Series B"),
        (r"\bseries\s*c\b", "Series C"),
        (r"\bseries\s*d\b", "Series D"),
        (r"\bseries\s*e\b", "Series E"),
        (r"\bgrowth\s+round\b", "Growth"),
        (r"\blate\s*stage\b", "Late Stage"),
        (r"\bangel\s+round\b", "Angel"),
        (r"\bventure\s+round\b", "Venture"),
    ]
    
    for pat, label in stage_patterns:
        if re.search(pat, text_lower):
            result["funding_stage"] = label
            break
    
    # Amount and currency - improved regex patterns
    amount_patterns = [
        # $200 million, $200M, $200m
        r"(?P<currency>\$|USD)\s*(?P<amt>\d+(?:[.,]\d+)?)\s*(?P<scale>million|m|mm|billion|bn|b)",
        # €50 million, €50M
        r"(?P<currency>€|EUR|EUR|euro)\s*(?P<amt>\d+(?:[.,]\d+)?)\s*(?P<scale>million|m|mm|billion|bn|b)",
        # £25 million, GBP 25 million
        r"(?P<currency>£|GBP|pound)\s*(?P<amt>\d+(?:[.,]\d+)?)\s*(?P<scale>million|m|mm|billion|bn|b)",
        # 200 million USD, 200M USD
        r"(?P<amt>\d+(?:[.,]\d+)?)\s*(?P<scale>million|m|mm|billion|bn|b)\s*(?P<currency>USD|EUR|GBP|CHF|CAD|AUD)",
        # Original pattern for backward compatibility
        r"(?P<currency>USD|EUR|GBP|CHF|CAD|AUD|SEK|NOK|DKK|INR|JPY|CNY|HKD|\$|€|£)\s?(?P<amt>\d+(?:[.,]\d+)?)(?:\s?(?P<scale>billion|bn|million|m|mm))",
    ]
    
    for pattern in amount_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                currency = match.group("currency").upper()
                amt_str = match.group("amt").replace(",", "")  # Keep decimal point
                scale = (match.group("scale") or "").lower()
                
                # Normalize currency symbols
                currency_map = {"$": "USD", "€": "EUR", "£": "GBP", "EUR": "EUR", "EURO": "EUR"}
                currency = currency_map.get(currency, currency)
                
                amt_val = float(amt_str)
                
                # Convert to millions
                if scale in ("billion", "bn", "b"):
                    amt_val *= 1000.0
                
                result["investment_amount_m"] = amt_val
                result["investment_currency"] = currency
                break
            except Exception:
                continue
        
        if "investment_amount_m" in result:
            break
    
    return result


def extract_structured_data(html: str, url: str) -> Dict[str, Any]:
    """
    Extract structured data from HTML (JSON-LD, meta tags) for dates and other metadata.
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    
    # Try JSON-LD
    json_ld = soup.find_all("script", type="application/ld+json")
    for script in json_ld:
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                # Look for datePublished, dateCreated, etc.
                for key in ["datePublished", "dateCreated", "dateModified", "date"]:
                    if key in data:
                        date_str = data[key]
                        dt = parse_date_flexible(date_str)
                        if dt and not result.get("announcement_date"):
                            result["announcement_date"] = dt.strftime("%Y-%m-%d")
        except:
            pass
    
    # Try meta tags
    meta_date = soup.find("meta", property="article:published_time") or \
                soup.find("meta", attrs={"name": "date"}) or \
                soup.find("meta", attrs={"name": "pubdate"}) or \
                soup.find("meta", attrs={"name": "publishdate"})
    
    if meta_date:
        date_str = meta_date.get("content", "")
        dt = parse_date_flexible(date_str)
        if dt:
            result["announcement_date"] = dt.strftime("%Y-%m-%d")
    
    return result


def fetch_html(url: str, force_scrapfly: bool = False) -> Optional[str]:
    """
    Fetch HTML via Scrapfly (if key available) or direct GET with proper headers.
    
    Args:
        url: URL to fetch
        force_scrapfly: If True, use Scrapfly even if not normally used
    
    Returns:
        HTML content as string, or None if fetch failed
    """
    if not url:
        print(f"[fetch_html] No URL provided")
        return None
    
    # Try Scrapfly first if key is available
    if SCRAPFLY_KEY and force_scrapfly:
        try:
            print(f"[fetch_html] Using Scrapfly: {url[:100]}...")
            api_url = "https://api.scrapfly.io/scrape"
            
            params = {
                "key": SCRAPFLY_KEY,
                "url": url,
                "render": "html",  # Render JavaScript
                "country": "US",
            }
            
            body = {
                "url": url,
                "format": "raw",
            }
            
            response = requests.post(
                api_url,
                params=params,
                json=body,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                result = data.get("result", {})
                html = result.get("content", "")
                if html:
                    print(f"[fetch_html] Scrapfly: Got {len(html)} chars")
                    return html
            else:
                print(f"[fetch_html] Scrapfly error: {response.status_code}, falling back to direct GET")
        except Exception as e:
            print(f"[fetch_html] Scrapfly error: {e}, falling back to direct GET")
    
    # Fallback: Direct GET with proper headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        print(f"[fetch_html] Fetching directly: {url[:100]}...")
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        print(f"[fetch_html] Response: {resp.status_code} ({len(resp.text)} chars)")
        
        if resp.ok:
            return resp.text
        else:
            print(f"[fetch_html] HTTP error {resp.status_code} for {url}")
            # Try without some headers as fallback
            try:
                resp2 = requests.get(url, timeout=10)
                if resp2.ok:
                    return resp2.text
            except:
                pass
    except requests.exceptions.Timeout:
        print(f"[fetch_html] Timeout for {url}")
    except requests.exceptions.ConnectionError as e:
        print(f"[fetch_html] Connection error for {url}: {e}")
    except Exception as e:
        print(f"[fetch_html] Error for {url}: {e}")
    
    return None


def ai_extract_event_from_text(text: str, url: str) -> Dict[str, Any]:
    """
    Send page text to LLM to extract structured corporate event fields.
    """
    if not text:
        return {}

    prompt = f"""
You are an information extractor. Extract ONE corporate event from the given announcement page text.
Return ONLY minified JSON (no code fences, no trailing text) exactly like:
{{"title":"","announcement_date":"","closed_date":"","deal_type":"","deal_status":"","long_description":"","investment_amount_m":null,"investment_currency":"","funding_stage":"","investment_amount_source":"","deal_terms":"","source_url":"","counterparties":[{{"name":"","role":"","website":"","linkedin":""}}]}}
Rules:
- Dates: use YYYY-MM-DD when you can; otherwise "". ONLY use dates tied to this specific transaction — never founding year, prior milestones, or unrelated history.
- Amount: millions of base currency (e.g., $200 million => 200).
- Currency: ISO code (USD, EUR, GBP, CHF, etc.). Map $->USD, €->EUR, £->GBP.
- deal_terms: brief M&A deal structure only (e.g. "all-cash acquisition", "all-stock merger"). Use "" for funding events.
- If unknown, use "" for strings and null for numbers. Keep keys present.
- long_description: write a concise, neutral, professional paragraph (no bullet points) describing what happened using only verifiable facts from the provided text. Include absolute dates when available. If terms are undisclosed, state that neutrally.

Announcement URL: {url}
Page text (truncated):
\"\"\"{text[:8000]}\"\"\"
"""
    raw = openrouter_chat("openai/gpt-4o-mini", prompt, "ai-extract-event")
    if not raw:
        return {}
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        print(f"[AI extract] JSON parse error: {e} / raw: {raw[:200]}")
    return {}


def enrich_ai_events_with_llm(events: List[dict], max_enrich: int = 5) -> List[dict]:
    """
    For each AI event that has a source URL, fetch the page (Scrapfly/direct) and
    ask the LLM to extract structured fields. Merge into the event.
    """
    if not events:
        return events

    enriched = []
    count = 0
    for ev in events:
        merged = dict(ev)
        src = (
            ev.get("Source URL")
            or ev.get("source_url")
            or ev.get("press_release_url")
            or ev.get("announcement_url")
            or ""
        ).strip()

        if src and count < max_enrich:
            count += 1
            try:
                html = fetch_html(src, force_scrapfly=True)
                if html:
                    soup = BeautifulSoup(html, "html.parser")
                    text = soup.get_text(" ", strip=True)
                    ai = ai_extract_event_from_text(text, src)
                    if ai:
                        # Merge only if fields exist; prefer existing values
                        merged.setdefault("source_url", src)
                        merged.setdefault("Source URL", src)
                        for k in [
                            "title",
                            "announcement_date",
                            "closed_date",
                            "deal_type",
                            "deal_status",
                            "long_description",
                            "investment_amount_m",
                            "investment_currency",
                            "funding_stage",
                            "investment_amount_source",
                            "deal_terms",
                            "source_url",
                        ]:
                            v = ai.get(k)
                            if v not in [None, ""]:
                                # map title -> Event (short)
                                if k == "title":
                                    merged["Event (short)"] = merged.get("Event (short)", v) or v
                                    merged["event_short"] = merged.get("event_short", v) or v
                                else:
                                    merged[k] = merged.get(k) or v
                        # Counterparties could be merged later into UI; keep as payload
                        if ai.get("counterparties"):
                            merged["ai_counterparties"] = ai["counterparties"]
            except Exception as e:
                print(f"[AI enrich] failed for {src}: {e}")

        enriched.append(merged)

    return enriched


def ai_enrich_single_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich a single event with evidence extraction.
    """
    source_url = (
        event.get("source_url")
        or event.get("Source URL")
        or event.get("press_release_url")
        or ""
    ).strip()
    title = event.get("title") or event.get("Event (short)") or event.get("event_short") or ""
    company = event.get("company") or event.get("target_company") or ""
    counterparties = event.get("counterparties") or []
    if isinstance(counterparties, list):
        cp_names = counterparties
    else:
        cp_names = []

    if not source_url:
        return {"error": "Missing source_url"}

    html = fetch_html(source_url, force_scrapfly=True)
    if not html:
        return {"error": "Fetch failed"}
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    
    # Extract structured data and dates with context (before LLM)
    structured = extract_structured_data(html, source_url)
    dates = extract_dates_with_context(text)
    
    # Prepare initial result with improved date extraction
    initial_result = {}
    if structured.get("announcement_date"):
        initial_result["announcement_date"] = structured["announcement_date"]
    elif dates.get("announcement_date"):
        initial_result["announcement_date"] = dates["announcement_date"]
    if dates.get("closed_date"):
        initial_result["closed_date"] = dates["closed_date"]

    # Multi-source corroboration (best-effort). We pass short excerpts to the model.
    corroborating = get_corroborating_sources(title=title, company=company, source_url=source_url, max_sources=3)

    prompt = f"""
You are a financial analyst who writes concise, professional, neutral descriptions of recently closed corporate finance events for clients.
Your purpose is to help clients quickly understand what happened and why, using only verifiable facts.
Clarity and completeness are the highest priorities.

When sources conflict, briefly identify the discrepancy and use the most supportable figure (or mark as undisclosed if not supportable).
Always include absolute dates where relevant. Do not use bullet points.

Use ONLY the provided source materials below (do not invent facts). Prefer authoritative sources (company filings/press releases, major business media).

Return STRICT one-line JSON (no code fences) with keys:
{{
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
 "parties": [{{"name":"","role":""}}],
 "evidence_links": [],
 "evidence_summary": "",
 "enrichment_version": 1
}}
Rules:
- Dates: YYYY-MM-DD when possible else "".
- amount: millions (base currency). If undisclosed/not mentioned, set null and set amount_status accordingly.
- amount_status: one of ["disclosed","undisclosed","not_mentioned"].
- amount_confidence: 0..1; higher when explicitly stated.
- amount_source_type: e.g., "company_pr","news","regulatory","unknown".
- stage_status: "disclosed","undisclosed","not_mentioned".
- parties: include target/investor/partner roles if evident.
- evidence_links: up to 3 URLs found in page (absolute URLs).
- Use empty strings for unknown strings; null for unknown numbers.
- long_description: MUST be a single paragraph (no bullet points). 3–6 sentences max. Neutral tone. Include announcement and/or close date if known. Mention strategic rationale only if explicitly stated in sources (e.g., expansion, product, market).

Context:
- Event title hint: {title}
- Company: {company}
- Counterparties: {", ".join(cp_names)}
- Source URL: {source_url}
- Deal date hints: announcement_date={initial_result.get("announcement_date","")}, closed_date={initial_result.get("closed_date","")}

Primary source material (source_url; publish_date={extract_publish_date_from_html(html)}):
\"\"\"{text[:9000]}\"\"\"

Corroborating sources (may be empty):
{json.dumps(corroborating, ensure_ascii=False)[:14000]}
"""

    raw = openrouter_chat("openai/gpt-4o-mini", prompt, "ai-enrich-event")
    if not raw:
        # Return initial result even if LLM fails
        if initial_result:
            return initial_result
        return {"error": "LLM returned empty"}

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            parsed.setdefault("enrichment_version", 1)
            
            # Merge with improved date extraction (prefer our dates over LLM dates)
            if initial_result.get("announcement_date"):
                parsed["announcement_date"] = initial_result["announcement_date"]
            if initial_result.get("closed_date"):
                parsed["closed_date"] = initial_result["closed_date"]
            
            return parsed
    except Exception as e:
        print(f"[AI enrich] JSON parse error: {e} / raw: {raw[:200]}")
        # Return initial result even if LLM fails
        if initial_result:
            return initial_result
        return {"error": "LLM parse error", "raw": raw[:200]}

    # Return initial result if LLM format is unexpected
    if initial_result:
        return initial_result
    return {"error": "Unexpected LLM format"}


def validate_enriched_dates(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates and fixes date logic issues in enriched event data:
    1. Announcement date cannot be in the future
    2. Closed date must be >= announcement date
    3. If dates are swapped, fix them
    """
    from datetime import datetime
    
    def parse_date_flex(date_str):
        if not date_str or not isinstance(date_str, str):
            return None
        date_str = date_str.strip()
        formats = [
            "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
            "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%B %Y", "%b %Y", "%Y"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.split("T")[0], fmt)
            except:
                continue
        return None
    
    today = datetime.now()
    
    # Check both possible field names for dates
    ann_key = "announcement_date" if "announcement_date" in result else "Announcement Date"
    closed_key = "closed_date" if "closed_date" in result else "Closed Date"
    
    ann_str = result.get(ann_key, "") or ""
    closed_str = result.get(closed_key, "") or ""
    
    ann_date = parse_date_flex(ann_str)
    closed_date = parse_date_flex(closed_str)
    
    # Rule 1: Announcement date cannot be in the future
    if ann_date and ann_date > today:
        print(f"   ⚠️ Future announcement date: {ann_str} - clearing")
        result[ann_key] = ""
        ann_date = None
    
    # Rule 2: Closed date cannot be in the future
    if closed_date and closed_date > today:
        print(f"   ⚠️ Future closed date: {closed_str} - clearing")
        result[closed_key] = ""
        closed_date = None
    
    # Rule 3: If both dates exist, closed must be >= announcement
    if ann_date and closed_date and closed_date < ann_date:
        print(f"   🔄 Swapped dates: Ann={ann_str}, Closed={closed_str} - fixing")
        result[ann_key] = closed_str
        result[closed_key] = ann_str
    
    return result


@app.post("/extract_event_meta", response_class=JSONResponse)
async def extract_event_meta(payload: Dict[str, Any]) -> JSONResponse:
    """
    Fetch a page and try to extract a reasonable title, first date found, and long text.
    Body: { "url": "https://example.com/press-release" }
    """
    url = (payload or {}).get("url", "").strip()
    if not url:
        print(f"[extract_event_meta] Missing URL in payload")
        return JSONResponse({"error": "Missing url"}, status_code=400)

    print(f"[extract_event_meta] Processing: {url}")
    try:
        html = fetch_html(url)
        if not html:
            print(f"[extract_event_meta] Fetch returned no HTML for: {url}")
            return JSONResponse({"error": f"Could not fetch page: {url[:50]}..."}, status_code=400)
        soup = BeautifulSoup(html, "html.parser")

        # Title preference: h1 > title tag
        title = ""
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(" ", strip=True)
        elif soup.title and soup.title.get_text(strip=True):
            title = soup.title.get_text(" ", strip=True)
        else:
            og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "title"})
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
            else:
                h2 = soup.find("h2")
                if h2 and h2.get_text(strip=True):
                    title = h2.get_text(" ", strip=True)

        # Extract structured data first (JSON-LD, meta tags)
        structured = extract_structured_data(html, url)
        
        # Collect text for date search with improved context-aware extraction
        body_text = soup.get_text(" ", strip=True)
        dates = extract_dates_with_context(body_text)
        
        # Use structured data date if available, otherwise use context-extracted date
        date_iso = structured.get("announcement_date") or dates.get("announcement_date", "")
        
        inv_fields = extract_investment_fields(body_text)

        # Long description: take first meaningful paragraphs
        long_desc = ""
        paras = soup.find_all("p")
        collected = []
        for p in paras:
            txt = p.get_text(" ", strip=True)
            if txt and len(txt) > 40:
                collected.append(txt)
            if len(" ".join(collected)) > 600:
                break
        if collected:
            long_desc = "\n\n".join(collected)

        return JSONResponse(
            {
                "title": title,
                "announcement_date": date_iso,
                "long_description": long_desc,
                **inv_fields,
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/ai_extract_event_from_url", response_class=JSONResponse)
async def ai_extract_event_from_url(payload: Dict[str, Any]) -> JSONResponse:
    """
    Fetch page (direct GET) and ask LLM to extract corporate event fields.
    Body: { "url": "..." }
    """
    url = (payload or {}).get("url", "").strip()
    if not url:
        return JSONResponse({"error": "Missing url"}, status_code=400)

    try:
        html = fetch_html(url)
        if not html:
            return JSONResponse({"error": "Fetch failed"}, status_code=400)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        ai_data = ai_extract_event_from_text(text, url)
        return JSONResponse(ai_data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/enrich_event", response_class=JSONResponse)
async def enrich_event(payload: Dict[str, Any]) -> JSONResponse:
    """
    Enrich a single corporate event with evidence extraction (Scrapfly if available + LLM).
    Body: { "event": { ... } }
    """
    ev = (payload or {}).get("event") or {}
    if not ev:
        return JSONResponse({"error": "Missing event"}, status_code=400)
    try:
        enriched = ai_enrich_single_event(ev)
        if "error" in enriched:
            return JSONResponse(enriched, status_code=400)
        return JSONResponse({"enriched_event": enriched})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/smart_enrich_event", response_class=JSONResponse)
async def smart_enrich_event(payload: Dict[str, Any]) -> JSONResponse:
    """
    Smart enrichment: First parse source URL, then search web if data is incomplete.
    Body: { "url": "...", "event": { title, company, counterparties, ... } }
    
    Steps:
    1. Fetch and AI-parse the source URL for detailed extraction
    2. Check if key fields are complete (title, date, amount, description)
    3. If incomplete, search the web for additional evidence
    """
    url = (payload or {}).get("url", "").strip()
    ev = (payload or {}).get("event") or {}
    
    if not url:
        return JSONResponse({"error": "Missing url"}, status_code=400)
    
    print(f"\n🧠 Smart Enrich: {url}")
    result = {}
    
    try:
        # =====================================================
        # Step 1: Fetch and parse the source URL (improved parsing)
        # =====================================================
        print(f"   📄 Step 1: Parsing source URL...")
        html = fetch_html(url, force_scrapfly=True)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            body_text = soup.get_text(" ", strip=True)[:15000]  # Limit text
            
            # Extract structured data (JSON-LD, meta tags) for dates
            structured = extract_structured_data(html, url)
            if structured.get("announcement_date"):
                result["announcement_date"] = structured["announcement_date"]
                print(f"   ✅ Found date in structured data: {result['announcement_date']}")
            
            # Extract dates with context awareness
            dates = extract_dates_with_context(body_text)
            if dates.get("announcement_date") and not result.get("announcement_date"):
                result["announcement_date"] = dates["announcement_date"]
                print(f"   ✅ Found announcement date via context: {result['announcement_date']}")
            if dates.get("closed_date"):
                result["closed_date"] = dates["closed_date"]
                print(f"   ✅ Found closed date via context: {result['closed_date']}")
            
            # Extract investment fields
            inv_fields = extract_investment_fields(body_text)
            if inv_fields:
                result.update(inv_fields)
                print(f"   ✅ Extracted investment fields: {inv_fields}")
            
            # AI extraction from source (for other fields)
            ai_data = ai_extract_event_from_text(body_text, url)
            if ai_data and not ai_data.get("error"):
                # Merge AI data, but prefer our improved date extraction
                for key in ["title", "deal_type", "deal_status", "long_description", 
                           "investment_amount_m", "investment_currency", "funding_stage",
                           "counterparties"]:
                    if ai_data.get(key) and not result.get(key):
                        result[key] = ai_data[key]
                # Only use AI date if we didn't find one via improved parsing
                if ai_data.get("announcement_date") and not result.get("announcement_date"):
                    result["announcement_date"] = ai_data["announcement_date"]
                if ai_data.get("closed_date") and not result.get("closed_date"):
                    result["closed_date"] = ai_data["closed_date"]
                
                print(f"   ✅ AI extracted from source: title={bool(result.get('title'))}, date={bool(result.get('announcement_date'))}, amount={bool(result.get('investment_amount_m'))}")
        else:
            print(f"   ⚠️ Could not fetch source URL")
        
        # =====================================================
        # Step 2: Check completeness of key fields
        # =====================================================
        has_title = bool(result.get("title"))
        has_date = bool(result.get("announcement_date"))
        has_amount = result.get("investment_amount_m") not in [None, "", "N/A", "nan", "NaN"]
        has_description = bool(result.get("long_description")) and len(result.get("long_description", "")) > 100
        
        completeness = sum([has_title, has_date, has_amount, has_description])
        print(f"   📊 Completeness: {completeness}/4 (title={has_title}, date={has_date}, amount={has_amount}, desc={has_description})")
        
        # =====================================================
        # Step 3: If incomplete, search web for more evidence
        # =====================================================
        if completeness < 3:
            print(f"   🔍 Step 2: Searching web for additional evidence...")
            
            # Build search context
            company = ev.get("company", "") or result.get("company", "")
            title = ev.get("title", "") or result.get("title", "")
            counterparties = ev.get("counterparties", [])
            
            # Use the full enrichment pipeline
            enrichment_event = {
                "title": title,
                "company": company,
                "counterparties": counterparties,
                "source_url": url,
                "announcement_date": ev.get("announcement_date") or result.get("announcement_date"),
                "deal_type": ev.get("deal_type") or result.get("deal_type", "")
            }
            
            enriched = ai_enrich_single_event(enrichment_event)
            if enriched and not enriched.get("error"):
                # Merge: prefer enriched data for missing fields
                for key in ["title", "announcement_date", "deal_type", "deal_status", 
                           "amount", "currency", "stage", "amount_source_url", "long_description"]:
                    if enriched.get(key) and not result.get(key):
                        result[key] = enriched[key]
                    # Also override if result has placeholder values
                    elif enriched.get(key) and result.get(key) in [None, "", "N/A", "nan", "NaN"]:
                        result[key] = enriched[key]
                
                # Map amount field names
                if enriched.get("amount") and not result.get("investment_amount_m"):
                    result["investment_amount_m"] = enriched["amount"]
                if enriched.get("currency") and not result.get("investment_currency"):
                    result["investment_currency"] = enriched["currency"]
                if enriched.get("stage") and not result.get("funding_stage"):
                    result["funding_stage"] = enriched["stage"]
                    
                print(f"   ✅ Web enrichment complete")
        else:
            print(f"   ✅ Source parsing sufficient, skipping web search")
        
        # =====================================================
        # Step 4: Validate and fix date logic
        # =====================================================
        result = validate_enriched_dates(result)
        
        return JSONResponse({"enriched_event": result, "source": "smart_enrich"})
        
    except Exception as e:
        print(f"   ❌ Smart enrich error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ============================================================
# 🔹 Routes
# ============================================================

@app.get("/investors_search", response_class=JSONResponse)
async def investors_search(q: str = "", page: int = 1, per_page: int = 25):
    """
    Proxy for the Xano investors list endpoint.
    Handles Xano auth transparently — credentials stay on the server.
    """
    xano_url = f"{XANO_BASE_URL}/api:y4OAXSVm/investors_with_d_a_list"
    params = {"page": page, "per_page": per_page, "Search_Query": q.lower()}

    def _call(token: str):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.get(xano_url, params=params, headers=headers, timeout=10)

    token = _get_xano_token()
    try:
        resp = _call(token)
        if resp.status_code == 401:
            # Token may have expired — refresh once and retry
            token = _get_xano_token(force_refresh=True)
            resp = _call(token)
        resp.raise_for_status()
        return JSONResponse(resp.json())
    except Exception as e:
        print(f"[investors_search] error: {e}")
        return JSONResponse({"items": [], "error": str(e)}, status_code=502)


@app.post("/investors_create", response_class=JSONResponse)
async def investors_create(request: Request):
    """
    Proxy for creating a new investor record in Xano.
    Accepts JSON: { company_name, website_url, investor_type, city, country, linkedin_url, primary_business_focus_id }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    name    = (body.get("company_name") or "").strip()
    website = (body.get("website_url") or "").strip()
    if not name or not website:
        return JSONResponse({"error": "company_name and website_url are required"}, status_code=422)

    pbf_raw = body.get("primary_business_focus_id", 74)
    try:
        primary_business_focus_id = int(pbf_raw)
    except (TypeError, ValueError):
        primary_business_focus_id = 74
    if primary_business_focus_id <= 0:
        primary_business_focus_id = 74

    xano_url = f"{XANO_BASE_URL}/api:y4OAXSVm/investors_new_company"
    payload = {
        "company_name": name,
        "website_url":  website,
        "investor_type": body.get("investor_type", ""),
        "city":          body.get("city", ""),
        "country":       body.get("country", ""),
        "linkedin_url":  body.get("linkedin_url", ""),
        "primary_business_focus_id": primary_business_focus_id,
    }

    def _call(token: str):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.post(xano_url, json=payload, headers=headers, timeout=10)

    try:
        token = _get_xano_token()
        resp = _call(token)
        if resp.status_code == 401:
            token = _get_xano_token(force_refresh=True)
            resp = _call(token)
        resp.raise_for_status()
        return JSONResponse(resp.json())
    except Exception as e:
        print(f"[investors_create] error: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


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


@app.post("/api/search_company_headquarters", response_class=JSONResponse)
async def search_company_headquarters_api(payload: Dict[str, Any]) -> JSONResponse:
    """
    Search for a company's headquarters location, or look up state if city/country provided.
    Body: { "company": "Acme Corp", "website": "https://acme.com", "city": "San Francisco", "country": "USA" }
    Returns: { "city": "San Francisco", "state": "California", "country": "USA" }
    """
    company = (payload or {}).get("company", "").strip()
    website = (payload or {}).get("website", "").strip()
    existing_city = (payload or {}).get("city", "").strip()
    existing_country = (payload or {}).get("country", "").strip()
    
    # Major US cities to state mapping
    us_city_to_state = {
        'san francisco': 'California', 'los angeles': 'California', 'san diego': 'California',
        'san jose': 'California', 'oakland': 'California', 'palo alto': 'California',
        'mountain view': 'California', 'menlo park': 'California', 'cupertino': 'California',
        'sunnyvale': 'California', 'santa clara': 'California', 'redwood city': 'California',
        'irvine': 'California', 'santa monica': 'California', 'pasadena': 'California',
        'new york': 'New York', 'new york city': 'New York', 'nyc': 'New York', 'manhattan': 'New York',
        'brooklyn': 'New York', 'buffalo': 'New York',
        'seattle': 'Washington', 'bellevue': 'Washington', 'redmond': 'Washington',
        'boston': 'Massachusetts', 'cambridge': 'Massachusetts',
        'chicago': 'Illinois',
        'austin': 'Texas', 'dallas': 'Texas', 'houston': 'Texas', 'san antonio': 'Texas', 'plano': 'Texas',
        'denver': 'Colorado', 'boulder': 'Colorado',
        'atlanta': 'Georgia',
        'miami': 'Florida', 'tampa': 'Florida', 'orlando': 'Florida', 'jacksonville': 'Florida',
        'phoenix': 'Arizona', 'scottsdale': 'Arizona', 'tempe': 'Arizona',
        'portland': 'Oregon',
        'las vegas': 'Nevada', 'reno': 'Nevada',
        'salt lake city': 'Utah',
        'raleigh': 'North Carolina', 'charlotte': 'North Carolina', 'durham': 'North Carolina',
        'nashville': 'Tennessee',
        'detroit': 'Michigan', 'ann arbor': 'Michigan',
        'minneapolis': 'Minnesota', 'st paul': 'Minnesota',
        'philadelphia': 'Pennsylvania', 'pittsburgh': 'Pennsylvania',
        'washington': 'District of Columbia', 'washington dc': 'District of Columbia', 
        'washington d.c.': 'District of Columbia', 'dc': 'District of Columbia',
        'arlington': 'Virginia', 'mclean': 'Virginia', 'reston': 'Virginia', 'alexandria': 'Virginia',
        'baltimore': 'Maryland', 'bethesda': 'Maryland',
        'indianapolis': 'Indiana',
        'columbus': 'Ohio', 'cleveland': 'Ohio', 'cincinnati': 'Ohio',
        'kansas city': 'Missouri', 'st louis': 'Missouri', 'st. louis': 'Missouri',
        'omaha': 'Nebraska',
        'new orleans': 'Louisiana',
        'milwaukee': 'Wisconsin', 'madison': 'Wisconsin',
        'hartford': 'Connecticut', 'stamford': 'Connecticut', 'greenwich': 'Connecticut',
        'providence': 'Rhode Island',
        'jersey city': 'New Jersey', 'newark': 'New Jersey', 'hoboken': 'New Jersey', 'princeton': 'New Jersey',
    }
    
    # If we already have city + USA/US, just look up the state
    if existing_city and existing_country:
        country_upper = existing_country.upper().strip()
        if country_upper in ['USA', 'US', 'UNITED STATES', 'UNITED STATES OF AMERICA', 'AMERICA']:
            city_lower = existing_city.lower().strip()
            state = us_city_to_state.get(city_lower, "")
            if state:
                result = {"city": existing_city, "state": state, "country": "USA"}
                print(f"📍 State lookup for {existing_city}, {existing_country}: {state}")
                return JSONResponse(result)
            else:
                print(f"⚠️ Unknown US city: {existing_city}, trying web search...")
        else:
            # Non-US country - just return what we have
            result = {"city": existing_city, "state": "", "country": existing_country}
            print(f"📍 Non-US location: {existing_city}, {existing_country}")
            return JSONResponse(result)
    
    if not company and not website:
        return JSONResponse({"error": "Missing 'company' or 'website' field", "city": "", "state": "", "country": ""}, status_code=400)
    
    try:
        from searxng_analyzer import search_company_headquarters
        result = search_company_headquarters(company, website)
        
        # If search found a city + USA but no state, try lookup
        if result.get("city") and result.get("country", "").upper() in ['USA', 'US'] and not result.get("state"):
            city_lower = result["city"].lower().strip()
            state = us_city_to_state.get(city_lower, "")
            if state:
                result["state"] = state
                print(f"📍 Added state from lookup: {state}")
        
        print(f"📍 HQ search result for {company}: {result}")
        return JSONResponse(result)
            
    except ImportError as e:
        print(f"❌ search_company_headquarters not available: {e}")
        return JSONResponse({"error": "Function not available", "city": "", "state": "", "country": ""}, status_code=500)
    
    except Exception as e:
        print(f"❌ Error searching company HQ: {e}")
        return JSONResponse({"error": str(e), "city": "", "state": "", "country": ""}, status_code=500)


@app.post("/api/search_company_linkedin", response_class=JSONResponse)
async def search_company_linkedin_api(payload: Dict[str, Any]) -> JSONResponse:
    """
    Recover the current LinkedIn company URL using stable company identifiers.
    Body: { "company": "Acme Corp", "website": "https://acme.com", "old_linkedin_url": "https://..." }
    Returns: { "linkedin_url": "https://...", "source": "xano|serpapi|startpage|not_found", ... }
    """
    company = (payload or {}).get("company", "").strip()
    website = (payload or {}).get("website", "").strip()
    old_linkedin_url = (payload or {}).get("old_linkedin_url", "").strip()
    normalized_old_linkedin = _normalize_company_linkedin_url(old_linkedin_url)

    if not company and not website:
        return JSONResponse(
            {
                "error": "Missing 'company' or 'website' field",
                "linkedin_url": None,
                "source": "not_found",
                "matched_by": "",
                "query_used": "",
                "queries_used": [],
                "changed": False,
                "old_linkedin_url": old_linkedin_url or None,
            },
            status_code=400,
        )

    try:
        if website:
            existing_company = check_company_by_url(website)
            if existing_company and existing_company.get("id"):
                db_company = get_company_by_id(existing_company["id"])
                xano_linkedin = _extract_company_linkedin_from_xano_payload(db_company)
                if xano_linkedin:
                    return JSONResponse(
                        {
                            "linkedin_url": xano_linkedin,
                            "source": "xano",
                            "matched_by": "website_domain",
                            "query_used": website,
                            "queries_used": [website],
                            "changed": bool(normalized_old_linkedin and normalized_old_linkedin != xano_linkedin),
                            "old_linkedin_url": old_linkedin_url or None,
                            "company": company,
                            "website": website,
                        }
                    )

        from searxng_analyzer import search_company_linkedin_detailed

        result = search_company_linkedin_detailed(company, website)
        linkedin_url = _normalize_company_linkedin_url(result.get("linkedin_url", ""))
        source = result.get("source", "not_found") if linkedin_url else "not_found"
        query_used = result.get("query_used", "")
        queries_used = result.get("queries_used", []) or []
        matched_by = result.get("matched_by", "")

        return JSONResponse(
            {
                "linkedin_url": linkedin_url or None,
                "source": source,
                "matched_by": matched_by if linkedin_url else "",
                "query_used": query_used if linkedin_url else "",
                "queries_used": queries_used,
                "changed": bool(linkedin_url and normalized_old_linkedin and normalized_old_linkedin != linkedin_url),
                "old_linkedin_url": old_linkedin_url or None,
                "company": company,
                "website": website,
            }
        )

    except ImportError as e:
        print(f"❌ search_company_linkedin_detailed not available: {e}")
        return JSONResponse(
            {
                "error": "Function not available",
                "linkedin_url": None,
                "source": "not_found",
                "matched_by": "",
                "query_used": "",
                "queries_used": [],
                "changed": False,
                "old_linkedin_url": old_linkedin_url or None,
            },
            status_code=500,
        )
    except Exception as e:
        print(f"❌ Error searching company LinkedIn: {e}")
        return JSONResponse(
            {
                "error": str(e),
                "linkedin_url": None,
                "source": "not_found",
                "matched_by": "",
                "query_used": "",
                "queries_used": [],
                "changed": False,
                "old_linkedin_url": old_linkedin_url or None,
            },
            status_code=500,
        )


@app.post("/api/search_individual_linkedin", response_class=JSONResponse)
async def search_individual_linkedin(payload: Dict[str, Any]) -> JSONResponse:
    """
    Search for an individual's LinkedIn profile AND extract location from SEO snippets.
    Body: { "name": "John Smith", "company": "Acme Corp", "position": "CFO" }
    Returns: { "linkedin_url": "https://linkedin.com/in/johnsmith", "location": {"city": "", "state": "", "country": ""} }
    """
    name = (payload or {}).get("name", "").strip()
    company = (payload or {}).get("company", "").strip()
    position = (payload or {}).get("position", "").strip()
    
    if not name:
        return JSONResponse({"error": "Missing 'name' field", "linkedin_url": None, "location": {}}, status_code=400)
    
    try:
        search_query = " ".join([p for p in [name, position, company, "linkedin"] if p]).strip()
        print(f"🔍 Searching individual LinkedIn+Location: {search_query}")
        
        # Use the combined search function that extracts location from SEO snippets
        from searxng_analyzer import search_person_linkedin_with_location
        result = search_person_linkedin_with_location(name, company, position)
        
        linkedin_url = result.get("linkedin_url", "")
        location = result.get("location", {"city": "", "state": "", "country": ""})
        
        if linkedin_url:
            # Normalize location via AI if we got something
            if location and (location.get("city") or location.get("state") or location.get("country")):
                location = _normalize_location_with_ai(name, company, position, linkedin_url, location)
            print(f"✅ Found LinkedIn for {name}: {linkedin_url}, location: {location}")
            return JSONResponse({"linkedin_url": linkedin_url, "location": location, "query": search_query})
        else:
            print(f"⚠️ No LinkedIn found for: {name}")
            return JSONResponse({"linkedin_url": None, "location": {}, "query": search_query})
            
    except ImportError as ie:
        print(f"⚠️ search_person_linkedin_with_location not available: {ie}, trying fallback")
        # Fallback: try using the old function
        try:
            from searxng_analyzer import search_person_linkedin
            linkedin_url = search_person_linkedin(name, company, position)
            
            if linkedin_url:
                print(f"✅ Found LinkedIn (fallback): {linkedin_url}")
                return JSONResponse({"linkedin_url": linkedin_url, "location": {}, "query": search_query})
            
            print(f"⚠️ No LinkedIn found (fallback) for: {name}")
            return JSONResponse({"linkedin_url": None, "location": {}, "query": search_query})
            
        except Exception as e:
            print(f"❌ Fallback search failed: {e}")
            return JSONResponse({"error": str(e), "linkedin_url": None, "location": {}}, status_code=500)
    
    except Exception as e:
        print(f"❌ Error searching individual LinkedIn: {e}")
        return JSONResponse({"error": str(e), "linkedin_url": None, "location": {}}, status_code=500)


@app.post("/api/search_individual_location", response_class=JSONResponse)
async def search_individual_location(payload: Dict[str, Any]) -> JSONResponse:
    """
    Search for an individual's likely location (city/state/country) using AI analysis of search results.
    Body: { "name": "Mary Meeker", "company": "Kleiner Perkins", "position": "Partner", "linkedin_url": "https://..." }
    Returns: { "city": "...", "state": "...", "country": "..." }
    """
    name = (payload or {}).get("name", "").strip()
    company = (payload or {}).get("company", "").strip()
    position = (payload or {}).get("position", "").strip()
    linkedin_url = (payload or {}).get("linkedin_url", "").strip()

    if not name:
        return JSONResponse({"error": "Missing 'name' field", "city": "", "state": "", "country": ""}, status_code=400)

    try:
        # NEW: Use AI to analyze raw SerpAPI results instead of regex patterns
        from searxng_analyzer import get_raw_serpapi_results_for_person_location
        
        # Get raw search results
        search_data = get_raw_serpapi_results_for_person_location(name, company, position)
        organic_results = search_data.get("organic_results", [])
        query_used = search_data.get("query", "")
        
        if organic_results:
            # Use AI to analyze the search results and extract location
            print(f"🔍 Searching location for {name} (query: {query_used})")
            result = _extract_location_from_serpapi_with_ai(name, company, position, organic_results)
        else:
            # Fallback to old regex-based method if no SerpAPI results
            print(f"⚠️ No SerpAPI results, falling back to regex method for {name}")
            from searxng_analyzer import search_person_location
            result = search_person_location(name, company, linkedin_url, position=position) or {"city": "", "state": "", "country": ""}
            # Normalize via AI
            if result and (result.get("city") or result.get("state") or result.get("country")):
                result = _normalize_location_with_ai(name, company, position, linkedin_url, result)
        
        print(f"📍 Individual location result for {name}: {result}")
        return JSONResponse(result)
    except Exception as e:
        print(f"❌ Error searching individual location: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e), "city": "", "state": "", "country": ""}, status_code=500)


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
        with ThreadPoolExecutor(max_workers=2) as _xano_ex:
            _f_company = _xano_ex.submit(get_company_by_id, cid)
            _f_events  = _xano_ex.submit(get_corporate_events_by_company_id, cid)
            db_company = _f_company.result()
            db_events  = _f_events.result()

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

            def parse_maybe_json(value):
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except Exception:
                        return value
                return value

            former_name = (
                company_info.get("former_name")
                or company_info.get("former_names")
                or company_info.get("Former_Name")
                or ""
            )
            investors = company_info.get("investors") or company_info.get("investor_names") or ""
            investors_new_company = company_info.get("investors_new_company") or []
            investment = parse_maybe_json(company_info.get("investment")) or {}
            revenues = parse_maybe_json(company_info.get("revenues")) or {}
            ev_data = parse_maybe_json(company_info.get("ev_data")) or {}
            EBITDA = parse_maybe_json(company_info.get("EBITDA") or company_info.get("ebitda")) or {}

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
                "former_name": former_name,
                "investors": investors,
                "investors_new_company": investors_new_company,
                "investment": investment,
                "revenues": revenues,
                "ev_data": ev_data,
                "EBITDA": EBITDA,
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
            # Check both root level (correct) and Company level (fallback) for management roles
            mgmt_current = (
                db_company.get("Managmant_Roles_current") or 
                db_company.get("Management_Roles_current") or
                db_company.get("Company", {}).get("Managmant_Roles_current") or
                db_company.get("Company", {}).get("Management_Roles_current") or
                []
            )
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
    payload = payload or {}
    query = payload.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "Missing 'query' field"}, status_code=400)

    raw_options = payload.get("options") or {}
    if not isinstance(raw_options, dict):
        raw_options = {}
    include_overview = bool(raw_options.get("include_overview", True))
    include_events = bool(raw_options.get("include_events", True))
    include_individuals = bool(raw_options.get("include_individuals", True))
    include_counterparties = include_events and bool(raw_options.get("include_counterparties", True))

    def strip_counterparties_from_events(events: List[dict]) -> List[dict]:
        """Remove counterparty/advisor payloads when the user only wants event facts."""
        cleaned = []
        for ev in events or []:
            if not isinstance(ev, dict):
                cleaned.append(ev)
                continue
            item = dict(ev)
            for key in (
                "counterparties",
                "ai_counterparties",
                "other_counterparties",
                "target_company",
                "advisors",
                "investors",
            ):
                item.pop(key, None)
            cleaned.append(item)
        return cleaned

    # 1) Pre-check in Xano
    existing_company = check_company_by_url(query)
    db_company = None
    db_events: List[dict] = []

    if existing_company and existing_company.get("id"):
        cid = existing_company["id"]
        db_company = get_company_by_id(cid)
        if include_events:
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
    
    # ========================================
    # 🚀 PARALLEL AI TASKS - Run in parallel to save time
    # ========================================
    print(f"[AI] Starting parallel AI tasks for {query}...")
    
    # Helper functions for parallel execution
    def task_overview():
        """Generate company overview (summary + description)"""
        try:
            wiki_text = get_wikipedia_summary(query)

            # Pre-fetch Yahoo Finance data so generate_summary can inject it into
            # the LLM prompt context without making a second lookup.
            # query may be a full URL; extract the bare company name so that
            # lookup_ticker gets sensible SerpAPI queries (e.g. "equifax", not
            # "https://www.equifax.com/").
            _yf_name = query
            if query.startswith(("http://", "https://")):
                import urllib.parse as _urlparse
                _domain = _urlparse.urlparse(query).netloc.replace("www.", "")
                _yf_name = _domain.split(".")[0]  # "equifax" from "equifax.com"
            yahoo_data = enrich_with_yahoo_finance(_yf_name, input_website or "")
            if yahoo_data:
                print(
                    f"[Yahoo] EV=${yahoo_data.get('enterprise_value_m')}M, "
                    f"Rev=${yahoo_data.get('revenue_m')}M, "
                    f"EBITDA=${yahoo_data.get('ebitda_m')}M"
                )
            else:
                print("[Yahoo] No data returned (private company or ticker not found)")

            summary_md = generate_summary(query, text=wiki_text, yahoo_data=yahoo_data)
            description_raw = generate_description(query, text=wiki_text, company_details=summary_md)
            description = strip_marketing_phrases(description_raw)
            return {"summary_md": summary_md, "description": description, "wiki_text": wiki_text}
        except Exception as e:
            print(f"[AI] Overview task error: {e}")
            traceback.print_exc()
            return {"summary_md": "", "description": "", "wiki_text": ""}

    def task_events():
        """Generate corporate events"""
        try:
            return generate_corporate_events(query, max_events=20) or []
        except Exception as e:
            print(f"[AI] Events task error: {e}")
            return []

    def task_management():
        """Get top management"""
        try:
            mgmt_list, mgmt_text = get_top_management(query)
            if mgmt_list and isinstance(mgmt_list, list):
                return mgmt_list
            return []
        except Exception as e:
            print(f"[AI] Management task error: {e}")
            return []

    # Run all AI tasks in parallel (overview first so ownership task can use description)
    overview_result = {"summary_md": "", "description": "", "wiki_text": ""}
    ai_events = []
    top_management = []
    ownership_result = {"ownership": "", "confidence": "Low", "reasoning": ""}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        if include_overview:
            futures[executor.submit(task_overview)] = "overview"
        if include_events:
            futures[executor.submit(task_events)] = "events"
        if include_individuals:
            futures[executor.submit(task_management)] = "management"

        # Collect overview first so we can launch ownership detection on the description
        future_ownership = None
        for future in as_completed(list(futures.keys())):
            task_name = futures.get(future)
            try:
                if task_name == "overview":
                    overview_result = future.result()
                    print(f"[AI] ✅ Overview completed")
                    # Launch ownership detection now that we have the description
                    desc_for_ownership = overview_result.get("description", "")
                    future_ownership = executor.submit(detect_ownership_from_description, desc_for_ownership)
                elif task_name == "events":
                    ai_events = future.result()
                    print(f"[AI] ✅ Events completed ({len(ai_events)} found)")
                elif task_name == "management":
                    top_management = future.result()
                    print(f"[AI] ✅ Management completed ({len(top_management)} found)")
            except Exception as e:
                print(f"[AI] Parallel task error: {e}")

        # Collect ownership result (submitted after overview finished)
        if future_ownership:
            try:
                ownership_result = future_ownership.result(timeout=30)
                print(f"[AI] ✅ Ownership detected: {ownership_result.get('ownership')} (confidence: {ownership_result.get('confidence')})")
            except Exception as e:
                print(f"[AI] Ownership detection error: {e}")

    print(f"[AI] All parallel tasks completed")
    
    # Extract results from parallel execution
    summary_md = overview_result.get("summary_md", "")
    description = overview_result.get("description", "")
    
    try:
        if not include_overview:
            raise RuntimeError("overview skipped by profile options")
        
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
        former_name = ""
        investors = ""
        investors_new_company: List[int] = []
        investment = {
            "last_investment_amount": "",
            "last_investment_currency": "",
            "last_investment_date": "",
            "last_investment_source": "",
        }
        revenues = {
            "revenues_m": "",
            "rev_source": "",
            "revenues_currency": "",
            "years_id": "",
        }
        ev_data = {
            "ev_value": "",
            "ev_currency": "",
            "ev_year": "",
            "ev_source": "",
        }
        EBITDA = {
            "EBITDA_m": "",
            "EBITDA_source": "",
            "EBITDA_currency": "",
            "EBITDA_year": "",
        }
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
        
        def strip_citations(val: str) -> str:
            """Remove Perplexity/Sonar citation markers like [1], [2][3] from a value."""
            if not isinstance(val, str):
                return val
            cleaned = re.sub(r'\[\d+\]', '', val)
            return cleaned.strip()

        def is_valid_value(val: str) -> bool:
            """Check if value is valid (not empty or placeholder)."""
            if not val:
                return False
            low = strip_citations(val).lower().strip()
            invalid = {"not found", "unknown", "n/a", "none", "<value>", "", "-"}
            return low not in invalid and not low.startswith("<")

        def extract_url(text: str) -> str:
            """Extract URL from text, stripping citations and handling markdown links."""
            text = strip_citations(text)
            # Handle markdown links like [text](url)
            md_match = re.search(r'\[.*?\]\((https?://[^\)]+)\)', text)
            if md_match:
                return md_match.group(1)
            # Handle plain URLs (stop before any citation bracket)
            url_match = re.search(r'(https?://[^\s\)\[]+)', text)
            if url_match:
                return url_match.group(1).rstrip('.,;')
            return text.strip()

        def extract_year(text: str) -> str:
            if not text:
                return ""
            match = re.search(r'\b(19|20)\d{2}\b', text)
            return match.group(0) if match else text.strip()

        def parse_integer_list(text: str) -> List[int]:
            if not text:
                return []
            return [int(x) for x in re.findall(r'\d+', text)]
        
        for line in (summary_md or "").splitlines():
            # Strip Perplexity citation markers [1][2][3] so they never
            # end up in parsed values or field-matching strings.
            line = strip_citations(line)
            low = line.lower().replace("–", "-").replace("—", "-")

            if "website:" in low:
                val = line.split(":", 1)[-1].strip()
                url = extract_url(val)
                if is_valid_value(url) and ("http://" in url or "https://" in url):
                    website = url
            elif "former name:" in low or "former names:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    former_name = val
            elif "linkedin:" in low:
                val = line.split(":", 1)[-1].strip()
                url = extract_url(val)
                if is_valid_value(url) and "linkedin.com" in url.lower():
                    linkedin = url
            elif "press page:" in low or "press-page:" in low or "presspage:" in low:
                val = line.split(":", 1)[-1].strip()
                url = extract_url(val)
                if is_valid_value(url) and ("http://" in url or "https://" in url):
                    if _is_press_section_url(url):
                        press_page = url
                    else:
                        print(f"[PressPage] Rejected AI-suggested URL (looks like article, not section): {url}")
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
                    year_match = re.search(r'(\d{4})', val)
                    if year_match:
                        year_founded = year_match.group(1)
            elif "- ceo:" in low or line.strip().lower().startswith("ceo:"):
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    ceo = val
            elif "investors:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    investors = val
            elif "company name:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    company_name = val
            elif "investors new company:" in low or "investor ids:" in low:
                investors_new_company = parse_integer_list(line.split(":", 1)[-1].strip())
            elif "last investment amount:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    investment["last_investment_amount"] = val
            elif "last investment currency:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    investment["last_investment_currency"] = val.upper()
            elif "last investment date:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    investment["last_investment_date"] = val
            elif "last investment source:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    investment["last_investment_source"] = extract_url(val)
            elif "revenues currency:" in low or "revenue currency:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    revenues["revenues_currency"] = val.upper()
            elif "revenues year:" in low or "revenue year:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    revenues["years_id"] = extract_year(val)
            elif "revenues source:" in low or "revenue source:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    revenues["rev_source"] = extract_url(val)
            elif "revenues:" in low or "revenue:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    revenues["revenues_m"] = val
            elif "enterprise value currency:" in low or "ev currency:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    ev_data["ev_currency"] = val.upper()
            elif "enterprise value year:" in low or "ev year:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    ev_data["ev_year"] = extract_year(val)
            elif "enterprise value source:" in low or "ev source:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    ev_data["ev_source"] = extract_url(val)
            elif "enterprise value:" in low or "ev value:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    ev_data["ev_value"] = val
            elif "ebitda currency:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    EBITDA["EBITDA_currency"] = val.upper()
            elif "ebitda year:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    EBITDA["EBITDA_year"] = extract_year(val)
            elif "ebitda source:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    EBITDA["EBITDA_source"] = extract_url(val)
            elif "ebitda:" in low:
                val = line.split(":", 1)[-1].strip()
                if is_valid_value(val):
                    EBITDA["EBITDA_m"] = val

        # Always try web search to correct press page: prefer domain-based result
        search_input = website or company_name
        if search_input:
            found_press = search_press_page(search_input, company_name)
            if found_press:
                press_page = found_press

        # Auto-lookup state for US cities
        state_province = ""
        if city and country:
            country_upper = country.upper().strip()
            if country_upper in ['USA', 'US', 'UNITED STATES', 'UNITED STATES OF AMERICA', 'AMERICA']:
                us_city_to_state = {
                    'san francisco': 'California', 'los angeles': 'California', 'san diego': 'California',
                    'san jose': 'California', 'oakland': 'California', 'palo alto': 'California',
                    'mountain view': 'California', 'menlo park': 'California', 'cupertino': 'California',
                    'sunnyvale': 'California', 'santa clara': 'California', 'redwood city': 'California',
                    'irvine': 'California', 'santa monica': 'California', 'pasadena': 'California',
                    'new york': 'New York', 'new york city': 'New York', 'nyc': 'New York', 'manhattan': 'New York',
                    'brooklyn': 'New York', 'buffalo': 'New York',
                    'seattle': 'Washington', 'bellevue': 'Washington', 'redmond': 'Washington',
                    'boston': 'Massachusetts', 'cambridge': 'Massachusetts',
                    'chicago': 'Illinois',
                    'austin': 'Texas', 'dallas': 'Texas', 'houston': 'Texas', 'san antonio': 'Texas', 'plano': 'Texas',
                    'denver': 'Colorado', 'boulder': 'Colorado',
                    'atlanta': 'Georgia',
                    'miami': 'Florida', 'tampa': 'Florida', 'orlando': 'Florida', 'jacksonville': 'Florida',
                    'phoenix': 'Arizona', 'scottsdale': 'Arizona', 'tempe': 'Arizona',
                    'portland': 'Oregon',
                    'las vegas': 'Nevada', 'reno': 'Nevada',
                    'salt lake city': 'Utah',
                    'raleigh': 'North Carolina', 'charlotte': 'North Carolina', 'durham': 'North Carolina',
                    'nashville': 'Tennessee',
                    'detroit': 'Michigan', 'ann arbor': 'Michigan',
                    'minneapolis': 'Minnesota', 'st paul': 'Minnesota',
                    'philadelphia': 'Pennsylvania', 'pittsburgh': 'Pennsylvania',
                    'washington': 'District of Columbia', 'washington dc': 'District of Columbia',
                    'washington d.c.': 'District of Columbia', 'dc': 'District of Columbia',
                    'arlington': 'Virginia', 'mclean': 'Virginia', 'reston': 'Virginia', 'alexandria': 'Virginia',
                    'baltimore': 'Maryland', 'bethesda': 'Maryland',
                    'indianapolis': 'Indiana',
                    'columbus': 'Ohio', 'cleveland': 'Ohio', 'cincinnati': 'Ohio',
                    'kansas city': 'Missouri', 'st louis': 'Missouri', 'st. louis': 'Missouri',
                    'omaha': 'Nebraska',
                    'new orleans': 'Louisiana',
                    'milwaukee': 'Wisconsin', 'madison': 'Wisconsin',
                    'hartford': 'Connecticut', 'stamford': 'Connecticut', 'greenwich': 'Connecticut',
                    'providence': 'Rhode Island',
                    'jersey city': 'New Jersey', 'newark': 'New Jersey', 'hoboken': 'New Jersey', 'princeton': 'New Jersey',
                }
                city_lower = city.lower().strip()
                state_province = us_city_to_state.get(city_lower, "")
                if state_province:
                    print(f"[AI] 📍 Auto-detected state for {city}: {state_province}")

        # Ownership: use the dedicated LLM-based detector result (runs in parallel above)
        # It overrides whatever was parsed from summary_md — more accurate and consistent.
        detected_ownership = ownership_result.get("ownership", "")
        detected_confidence = ownership_result.get("confidence", "Low")
        if detected_ownership:
            ownership = detected_ownership
            print(f"[AI] 🏷️ Ownership set by detector: {ownership} (confidence: {detected_confidence})")

        ai_overview = {
            "name": company_name,
            "city": city,
            "state_province": state_province,
            "country": country,
            "ownership": ownership,
            "website": website,
            "linkedin": linkedin,
            "webpage_monitored": press_page,  # Press/news page URL
            "description": description or "",
            "year_founded": year_founded,
            "ceo": ceo,
            "former_name": former_name,
            "investors": investors,
            "investors_new_company": investors_new_company,
            "investment": investment,
            "revenues": revenues,
            "ev_data": ev_data,
            "EBITDA": EBITDA,
            "sectors": ai_sectors,
            "primary_business_focus": primary_business_focus,
            "primary_business_focus_id": primary_business_focus_id,
        }
        print(f"[AI] Parsed overview: {ai_overview}")
    except Exception as e:
        if include_overview:
            print(f"[AI] overview generation error: {e}")
            traceback.print_exc()
        else:
            ai_overview = None

    # 3) AI events - already fetched in parallel above

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

    if include_events and ai_events and db_events:
        for ev in ai_events:
            matched = any(events_match(ev, db_ev) for db_ev in db_events)
            if matched:
                matched_events.append(ev)
            else:
                missing_events.append(ev)
    elif include_events:
        missing_events = ai_events

    if include_events and not include_counterparties:
        ai_events = strip_counterparties_from_events(ai_events)
        db_events = strip_counterparties_from_events(db_events)
        matched_events = strip_counterparties_from_events(matched_events)
        missing_events = strip_counterparties_from_events(missing_events)

    # 4.5) Top Management - already fetched in parallel above

    # 5) DB management roles (key people from Xano)
    # Note: Management roles are at ROOT level of db_company, not inside "Company"
    db_management = []
    if include_individuals and db_company:
        try:
            # Check both root level (correct) and Company level (fallback)
            mgmt_current = (
                db_company.get("Managmant_Roles_current") or 
                db_company.get("Management_Roles_current") or
                db_company.get("Company", {}).get("Managmant_Roles_current") or
                db_company.get("Company", {}).get("Management_Roles_current") or
                []
            )
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
            # Past management roles - check root level first (correct), then Company level (fallback)
            mgmt_past = (
                db_company.get("Managmant_Roles_past") or 
                db_company.get("Management_Roles_past") or
                db_company.get("Company", {}).get("Managmant_Roles_past") or
                db_company.get("Company", {}).get("Management_Roles_past") or
                []
            )
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
    if include_overview and db_company:
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

            def parse_maybe_json(value):
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except Exception:
                        return value
                return value

            former_name = (
                company_info.get("former_name")
                or company_info.get("former_names")
                or company_info.get("Former_Name")
                or ""
            )
            investors_new_company = company_info.get("investors_new_company") or []
            investment = parse_maybe_json(company_info.get("investment")) or {}
            revenues = parse_maybe_json(company_info.get("revenues")) or {}
            ev_data = parse_maybe_json(company_info.get("ev_data")) or {}
            EBITDA = parse_maybe_json(company_info.get("EBITDA") or company_info.get("ebitda")) or {}
            
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
                "former_name": former_name,
                "investors_new_company": investors_new_company,
                "investment": investment,
                "revenues": revenues,
                "ev_data": ev_data,
                "EBITDA": EBITDA,
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
            "options": {
                "include_overview": include_overview,
                "include_events": include_events,
                "include_individuals": include_individuals,
                "include_counterparties": include_counterparties,
            },
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


