# searxng_analyzer.py
# This module provides functions to fetch and analyze company data, including summaries, descriptions,
# corporate events, top management, and subsidiaries, using APIs like SerpAPI, Wikipedia, and OpenRouter.

import os
import requests
from urllib.parse import quote
from dotenv import load_dotenv
import re
from datetime import datetime
import time
import json
from serpapi import GoogleSearch
from searxng_crawler import scrape_website
from searxng_db import store_subsidiaries
from bs4 import BeautifulSoup
import base64
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from searxng_ce_helpers import (
    corporate_events_shard_size,
    format_ce_journal_from_hits,
    merge_dedupe_ce_rows,
    normalize_llm_ce_events_to_rows,
    openrouter_extract_ce_events_json,
    title_is_strict_csuite,
)

_CE_LLM_SEM_STATE: Dict[str, Any] = {"sem": None, "n": None}


def _ce_llm_concurrency_semaphore() -> threading.Semaphore:
    """Caps concurrent OpenRouter CE shard calls. Recreates semaphore when CORPORATE_EVENTS_LLM_CONCURRENCY changes."""
    try:
        n = int(os.getenv("CORPORATE_EVENTS_LLM_CONCURRENCY", "4"))
    except ValueError:
        n = 4
    n = max(1, min(n, 12))
    if _CE_LLM_SEM_STATE["sem"] is None or _CE_LLM_SEM_STATE["n"] != n:
        _CE_LLM_SEM_STATE["sem"] = threading.Semaphore(n)
        _CE_LLM_SEM_STATE["n"] = n
    return _CE_LLM_SEM_STATE["sem"]

try:
    import yfinance as yf
except Exception:
    yf = None

def fetch_logo_free(company_name: str):
    """
    Fetches a company's logo using 100% free and stable sources.
    Fallback order:
        1️⃣ Wikipedia (Commons image)
        2️⃣ DuckDuckGo Images (scraped)
        3️⃣ Favicon generator
    Returns:
        str - Base64 data URI or working image URL.
    """
    headers = {"User-Agent": "Mozilla/5.0"}

    # ---------------------------------------------
    # 1️⃣ Try Wikipedia / Wikimedia Commons
    # ---------------------------------------------
    try:
        wiki_url = f"https://en.wikipedia.org/wiki/{company_name.replace(' ', '_')}"
        r = requests.get(wiki_url, headers=headers, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            infobox = soup.select_one("table.infobox img")
            if infobox and infobox.get("src"):
                img_url = infobox["src"]
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                img_data = requests.get(img_url, headers=headers, timeout=10).content
                b64 = base64.b64encode(img_data).decode("utf-8")
                mime = "image/png" if ".png" in img_url.lower() else "image/jpeg"
                print(f"✅ Wikipedia logo found for {company_name}")
                return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"⚠️ Wikipedia logo fetch failed for {company_name}: {e}")

    # ---------------------------------------------
    # 2️⃣ Try DuckDuckGo Image Search
    # ---------------------------------------------
    try:
        search_url = f"https://duckduckgo.com/html/?q={company_name.replace(' ', '+')}+logo"
        r = requests.get(search_url, headers=headers, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            img_tags = soup.find_all("img")
            for img in img_tags:
                src = img.get("src") or ""
                if re.search(r"\.(png|jpg|jpeg|svg)", src, re.I):
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://duckduckgo.com" + src
                    img_data = requests.get(src, headers=headers, timeout=10).content
                    b64 = base64.b64encode(img_data).decode("utf-8")
                    mime = "image/png" if ".png" in src.lower() else "image/jpeg"
                    print(f"✅ DuckDuckGo logo found for {company_name}")
                    return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"⚠️ DuckDuckGo logo fetch failed for {company_name}: {e}")

    # ---------------------------------------------
    # 3️⃣ Fallback favicon (guaranteed to work)
    # ---------------------------------------------
    try:
        domain = company_name.lower().replace(" ", "") + ".com"
        favicon_url = f"https://www.google.com/s2/favicons?sz=128&domain_url={domain}"
        r = requests.get(favicon_url, headers=headers, timeout=10)
        if r.status_code == 200:
            img_data = r.content
            b64 = base64.b64encode(img_data).decode("utf-8")
            mime = r.headers.get("Content-Type", "image/png")
            print(f"✅ Favicon used for {company_name}")
            return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"⚠️ Favicon fetch failed for {company_name}: {e}")

    # ---------------------------------------------
    # If everything fails — use Google fallback
    # ---------------------------------------------
    print(f"⚠️ No logo found, returning generic fallback for {company_name}")
    return "https://www.google.com/s2/favicons?sz=128&domain_url=google.com"


def fetch_logo_from_google(company_name: str):
    """
    Searches Google Images (via SerpAPI) for a company logo.
    Returns a base64-encoded data URI (so the logo always loads in UI).
    """
    try:
        print(f"🖼️ Searching Google for logo: {company_name}")
        params = {
            "q": f"{company_name} official company logo filetype:png OR filetype:svg",
            "tbm": "isch",
            "num": 5,
            "api_key": SERPAPI_KEY,
        }
        search = GoogleSearch(params)
        results = search.get_dict().get("images_results", [])

        for img in results:
            url = img.get("original") or img.get("thumbnail") or img.get("link")
            if not url or not url.startswith("http"):
                continue

            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                    mime = r.headers.get("Content-Type", "image/png")
                    b64 = base64.b64encode(r.content).decode("utf-8")
                    print(f"✅ Logo found for {company_name}")
                    return f"data:{mime};base64,{b64}"
            except Exception as e:
                print(f"⚠️ Failed logo URL for {company_name}: {e}")
                continue

        # Fallback to favicon
        domain = company_name.lower().replace(" ", "") + ".com"
        print(f"⚠️ All Google logo attempts failed for {company_name}, using fallback.")
        return f"https://www.google.com/s2/favicons?sz=64&domain_url={domain}"

    except Exception as e:
        print(f"❌ Logo fetch error for {company_name}: {e}")
        return "https://www.google.com/s2/favicons?sz=64&domain_url=google.com"


def fetch_and_encode_logo(url):
    """Downloads a logo and returns a base64-encoded data URI for Streamlit display."""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "image/png")
        b64 = base64.b64encode(r.content).decode("utf-8")
        return f"data:{content_type};base64,{b64}"
    except Exception as e:
        print(f"⚠️ Logo fetch failed: {e}")
        return "https://www.google.com/s2/favicons?sz=64&domain_url=google.com"
    

def get_google_logo(company_name: str):
    """
    Searches Google Images (via SerpAPI) for an official company logo.
    Returns a direct image URL if found, else a safe fallback favicon.
    """
    try:
        search = GoogleSearch({
            "q": f"{company_name} company logo site:pngtree.com OR site:seeklogo.com OR site:wikipedia.org OR site:commons.wikimedia.org",
            "tbm": "isch",
            "num": 5,
            "api_key": SERPAPI_KEY,
        })
        results = search.get_dict().get("images_results", [])
        for img in results:
            url = img.get("original") or img.get("thumbnail") or img.get("link")
            if url and url.startswith("http"):
                return url
        # fallback favicon
        domain = company_name.lower().replace(" ", "") + ".com"
        return f"https://www.google.com/s2/favicons?sz=64&domain_url={domain}"
    except Exception as e:
        print(f"⚠️ Logo search failed for {company_name}: {e}")
        return "https://www.google.com/s2/favicons?sz=64&domain_url=google.com"


# ============================================================
# 🔹 Environment Setup
# ============================================================
# Load environment variables from .env file for secure API key management
load_dotenv()

# Retrieve API keys and URLs from environment variables
OPENROUTER_API_KEY = (
    os.getenv("OPENROUTER_API_KEY")
    or os.getenv("OPEN_ROUTER_KEY")
)

if not OPENROUTER_API_KEY:
    raise ValueError("Missing OPENROUTER_API_KEY or OPEN_ROUTER_KEY in environment variables.")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
print("🔑 Loaded OpenRouter Key:", bool(OPENROUTER_API_KEY))

# ============================================================
# 🔹 OpenRouter Chat Completion Helper
# ============================================================
def openrouter_chat(model, prompt, title):
    """
    Sends a chat completion request to the OpenRouter API.

    Args:
        model (str): The AI model to use (e.g., 'openai/gpt-4o-mini').
        prompt (str): The prompt to send to the model.
        title (str): A title for the API request, used in headers for identification.

    Returns:
        str: The response content from the model, stripped of whitespace, or empty string on error.
    """
    # Set up headers with API key and request metadata
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": title
    }
    # Prepare request payload with model and prompt
    data = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    try:
        # Send POST request to OpenRouter API with a 20-second timeout
        response = requests.post(OPENROUTER_URL, headers=headers, json=data, timeout=20)
        response.raise_for_status()
        # Return the stripped content of the first choice
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        # Log error and return empty string if the request fails
        print(f"⚠️ OpenRouter API error ({title}): {e}")
        return ""

# ============================================================
# 🔹 SerpAPI Search Helper
# ============================================================
def serpapi_search(query, num_results=5):
    """
    Performs a search using SerpAPI and returns formatted results.

    Args:
        query (str): The search query to execute.
        num_results (int): Number of results to return (default: 5).

    Returns:
        str: A string of search results with titles and snippets, or empty string on error.
    """
    # Check if SerpAPI key is available
    if not SERPAPI_KEY:
        return ""
    try:
        # Set up search parameters for SerpAPI
        params = {"q": query, "hl": "en", "gl": "us", "num": num_results, "api_key": SERPAPI_KEY}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        # Format results as title: snippet pairs
        return "\n".join([f"{r.get('title', '')}: {r.get('snippet', '')}" for r in results[:num_results]])
    except Exception as e:
        # Log error and return empty string if the search fails
        print(f"⚠️ SerpAPI error: {e}")
        return ""

# ============================================================
# 🔹 Parallel SerpAPI Search (OPTIMIZED)
# ============================================================
import asyncio
import aiohttp

async def _serpapi_search_async(session: aiohttp.ClientSession, query: str, api_key: str, num_results: int = 10):
    """
    Single async SerpAPI search using aiohttp.
    """
    try:
        params = {
            "q": query,
            "hl": "en",
            "gl": "us",
            "num": num_results,
            "api_key": api_key,
            "output": "json"
        }
        url = "https://serpapi.com/search"
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("organic_results", [])
    except Exception as e:
        print(f"⚠️ Async SerpAPI error for query '{query[:50]}...': {e}")
    return []


async def _run_parallel_searches(queries: list, api_key: str, num_results: int = 10, batch_size: int = 10):
    """
    Run multiple SerpAPI queries in parallel batches.
    
    Args:
        queries: List of search query strings
        api_key: SerpAPI key
        num_results: Results per query
        batch_size: How many queries to run simultaneously (default 10)
    
    Returns:
        List of all results (deduplicated by URL)
    """
    all_results = []
    seen_urls = set()
    
    connector = aiohttp.TCPConnector(limit=batch_size, limit_per_host=batch_size)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Process in batches
        for i in range(0, len(queries), batch_size):
            batch = queries[i:i + batch_size]
            tasks = [_serpapi_search_async(session, q, api_key, num_results) for q in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for results in batch_results:
                if isinstance(results, Exception):
                    continue
                for r in results:
                    url = r.get("link", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append({
                            "title": r.get("title", ""),
                            "snippet": r.get("snippet", ""),
                            "link": url,
                            # Google organic "date" when present (headline date / freshness hint)
                            "published_date": (r.get("date") or "").strip(),
                        })
            
            # Small delay between batches to avoid rate limits
            if i + batch_size < len(queries):
                await asyncio.sleep(0.3)
    
    return all_results


def serpapi_parallel_search(queries: list, api_key: str, num_results: int = 10) -> list:
    """
    Synchronous wrapper for parallel SerpAPI searches.
    Runs all queries in parallel batches for ~5-10x speedup.
    
    Args:
        queries: List of search query strings
        api_key: SerpAPI key
        num_results: Results per query
        
    Returns:
        List of deduplicated results with title, snippet, link
    """
    if not queries or not api_key:
        return []
    
    try:
        # Try to get existing event loop (for environments like Jupyter)
        try:
            loop = asyncio.get_running_loop()
            # If we're already in an async context, use nest_asyncio or run in thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    _run_parallel_searches(queries, api_key, num_results)
                )
                return future.result(timeout=120)
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            return asyncio.run(_run_parallel_searches(queries, api_key, num_results))
    except Exception as e:
        print(f"⚠️ Parallel search error: {e}")
        return []


async def _run_parallel_searches_grouped(queries: list, api_key: str, num_results: int = 10, batch_size: int = 10):
    """
    Same scheduling as _run_parallel_searches, but returns **per-query** hit lists (no cross-query URL dedupe).
    Order matches `queries`.
    """
    grouped = []
    connector = aiohttp.TCPConnector(limit=batch_size, limit_per_host=batch_size)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(0, len(queries), batch_size):
            batch = queries[i : i + batch_size]
            tasks = [_serpapi_search_async(session, q, api_key, num_results) for q in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for q, results in zip(batch, batch_results):
                hits = []
                if isinstance(results, Exception):
                    print(f"⚠️ Async SerpAPI batch error for '{q[:48]}...': {results}")
                elif results:
                    for r in results:
                        url = r.get("link", "")
                        if not url:
                            continue
                        hits.append(
                            {
                                "title": r.get("title", ""),
                                "snippet": r.get("snippet", ""),
                                "link": url,
                                "published_date": (r.get("date") or "").strip(),
                            }
                        )
                grouped.append((q, hits))
            if i + batch_size < len(queries):
                await asyncio.sleep(0.3)
    return grouped


def serpapi_parallel_search_grouped(queries: list, api_key: str, num_results: int = 10) -> list:
    """
    Per-query SerpAPI results: list of (query_string, [ {title, snippet, link, published_date}, ... ]).
    """
    if not queries or not api_key:
        return []
    try:
        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    _run_parallel_searches_grouped(queries, api_key, num_results),
                )
                return future.result(timeout=180)
        except RuntimeError:
            return asyncio.run(_run_parallel_searches_grouped(queries, api_key, num_results))
    except Exception as e:
        print(f"⚠️ Parallel grouped search error: {e}")
        return []


# Serp snippets often contain "NYSE FUTURES" / similar — not a valid equity symbol for yfinance.
_INVALID_EQUITY_TICKERS = frozenset(
    {"FUTURES", "OPTIONS", "ETF", "INDEX", "FUND", "TRUST"}
)


def _sanitized_equity_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not t or t in _INVALID_EQUITY_TICKERS:
        return ""
    return t


def lookup_ticker(company_name: str, website_url: str = "") -> str:
    """
    Find a stock ticker symbol for a company name using SerpAPI.
    Returns ticker string like "EFX" or "" if not found/not public.
    """
    if not SERPAPI_KEY:
        return ""

    clean_name = (company_name or "").strip()
    if not clean_name:
        return ""

    # If a URL was passed as company name, extract the bare domain label
    if clean_name.startswith(("http://", "https://")):
        from urllib.parse import urlparse as _up
        _domain = _up(clean_name).netloc.replace("www.", "")
        clean_name = _domain.split(".")[0]  # "equifax" from "equifax.com"

    queries = [
        f'"{clean_name}" stock ticker symbol NASDAQ NYSE',
        f'"{clean_name}" ticker site:finance.yahoo.com',
    ]
    if website_url and not website_url.startswith(("http://", "https://")):
        queries.append(f'"{website_url}" stock ticker')

    results = serpapi_parallel_search(queries, SERPAPI_KEY, num_results=5)

    for r in results:
        snippet = (r.get("snippet", "") + " " + r.get("title", "")).upper()
        match = re.search(
            r'\b(?:NASDAQ|NYSE|LSE|TSX|ASX|FTSE|EURONEXT)[:\s]+([A-Z.\-]{1,8})\b',
            snippet,
        )
        if match:
            ticker = _sanitized_equity_ticker(match.group(1).strip())
            if not ticker:
                continue
            print(f"   📈 Found ticker for {company_name}: {ticker}")
            return ticker

        link = r.get("link", "")
        if "finance.yahoo.com/quote/" in link:
            m = re.search(r'/quote/([A-Z.\-]{1,8})(?:/|$)', link)
            if m:
                ticker = _sanitized_equity_ticker(m.group(1))
                if not ticker:
                    continue
                print(f"   📈 Found ticker from Yahoo URL: {ticker}")
                return ticker

    return ""


def get_yahoo_finance_data(ticker: str) -> dict:
    """
    Fetch key financial metrics from Yahoo Finance for a given ticker.
    Returns dict with EV, revenue, EBITDA, multiples, and holder data.
    """
    if not ticker or yf is None:
        if ticker and yf is None:
            print("   ⚠️ yfinance is not installed; skipping Yahoo Finance enrichment")
        return {}

    ticker = ticker.strip().upper()
    if not _sanitized_equity_ticker(ticker):
        print(f"   ⚠️ Skipping Yahoo Finance for invalid/non-equity ticker token: {ticker}")
        return {}

    print(f"   📊 Fetching Yahoo Finance data for ticker: {ticker}")

    result = {}

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        if not info or (info.get("trailingPegRatio") is None and not info.get("shortName")):
            print(f"   ⚠️ No Yahoo Finance data found for ticker: {ticker}")
            return {}

        result["ticker"] = ticker
        result["exchange"] = info.get("exchange", "")
        result["company_name"] = info.get("shortName") or info.get("longName", "")
        result["currency"] = info.get("financialCurrency") or info.get("currency", "USD")

        market_cap = info.get("marketCap")
        ev = info.get("enterpriseValue")
        result["market_cap_m"] = round(market_cap / 1_000_000, 2) if market_cap else None
        result["enterprise_value_m"] = round(ev / 1_000_000, 2) if ev else None

        revenue = info.get("totalRevenue")
        ebitda = info.get("ebitda")
        gross_profit = info.get("grossProfits")
        result["revenue_m"] = round(revenue / 1_000_000, 2) if revenue else None
        result["ebitda_m"] = round(ebitda / 1_000_000, 2) if ebitda else None
        result["gross_profit_m"] = round(gross_profit / 1_000_000, 2) if gross_profit else None

        result["profit_margin"] = info.get("profitMargins")
        result["ebitda_margin"] = (
            round(ebitda / revenue, 4)
            if ebitda and revenue and revenue > 0
            else None
        )
        result["gross_margin"] = info.get("grossMargins")
        result["operating_margin"] = info.get("operatingMargins")

        result["revenue_growth"] = info.get("revenueGrowth")
        result["earnings_growth"] = info.get("earningsGrowth")

        result["ev_revenue"] = info.get("enterpriseToRevenue")
        result["ev_ebitda"] = info.get("enterpriseToEbitda")
        result["pe_trailing"] = info.get("trailingPE")
        result["pe_forward"] = info.get("forwardPE")

        result["employees"] = info.get("fullTimeEmployees")
        result["institutional_ownership_pct"] = info.get("institutionPercentHeld")
        result["insider_ownership_pct"] = info.get("insiderPercentHeld")

        try:
            inst_holders = t.institutional_holders
            if inst_holders is not None and not inst_holders.empty:
                holders_list = []
                for _, row in inst_holders.head(10).iterrows():
                    holders_list.append({
                        "name": str(row.get("Holder", "")),
                        "pct_held": round(float(row.get("% Out", 0)) * 100, 2),
                        "shares": int(row.get("Shares", 0)),
                        "value": int(row.get("Value", 0)),
                    })
                result["institutional_holders"] = holders_list
        except Exception as e:
            print(f"   ⚠️ Institutional holders fetch failed: {e}")

        try:
            mf_holders = t.mutualfund_holders
            if mf_holders is not None and not mf_holders.empty:
                mf_list = []
                for _, row in mf_holders.head(5).iterrows():
                    mf_list.append({
                        "name": str(row.get("Holder", "")),
                        "pct_held": round(float(row.get("% Out", 0)) * 100, 2),
                    })
                result["mutualfund_holders"] = mf_list
        except Exception as e:
            print(f"   ⚠️ Mutual fund holders fetch failed: {e}")

        result["fiscal_year_end"] = info.get("lastFiscalYearEnd")
        result["source_url"] = f"https://finance.yahoo.com/quote/{ticker}/key-statistics/"

        print(
            f"   ✅ Yahoo Finance: EV=${result.get('enterprise_value_m')}M, "
            f"Rev=${result.get('revenue_m')}M, "
            f"EBITDA=${result.get('ebitda_m')}M"
        )

        return result

    except Exception as e:
        print(f"   ❌ Yahoo Finance fetch error for {ticker}: {e}")
        return {}


def enrich_with_yahoo_finance(
    company_name: str,
    website_url: str = "",
    known_ticker: str = "",
) -> dict:
    """
    Find a public ticker then fetch Yahoo Finance data. Empty dict means
    not public, ticker not found, yfinance missing, or data unavailable.
    """
    ticker = known_ticker.strip().upper() if known_ticker else lookup_ticker(company_name, website_url)
    if not ticker:
        print(f"   ℹ️ No ticker found for {company_name} — skipping Yahoo Finance")
        return {}
    return get_yahoo_finance_data(ticker)


# ============================================================
# 🔹 Date Parsing & Validation
# ============================================================
def parse_date_flexible(date_str: str):
    """
    Parses a date string into a datetime object using multiple formats.
    Returns None if parsing fails.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    
    # Common date formats to try
    formats = [
        "%Y-%m-%d",           # 2024-03-12
        "%B %d, %Y",          # March 12, 2024
        "%b %d, %Y",          # Mar 12, 2024
        "%d %B %Y",           # 12 March 2024
        "%d %b %Y",           # 12 Mar 2024
        "%m/%d/%Y",           # 03/12/2024
        "%d/%m/%Y",           # 12/03/2024
        "%Y/%m/%d",           # 2024/03/12
        "%B %Y",              # March 2024
        "%b %Y",              # Mar 2024
        "%Y",                 # 2024
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str.split("T")[0], fmt)
        except:
            continue
    return None


def validate_and_fix_event_dates(events: list) -> list:
    """
    Validates and fixes date logic issues in events:
    1. Announcement date cannot be in the future
    2. Closed date must be >= announcement date (if both exist)
    3. If dates are swapped, fix them
    """
    from datetime import datetime
    
    today = datetime.now()
    
    for event in events:
        ann_str = event.get("Announcement Date", "") or ""
        closed_str = event.get("Closed Date", "") or ""
        
        ann_date = parse_date_flexible(ann_str)
        closed_date = parse_date_flexible(closed_str)
        
        fixed = False
        
        # Rule 1: Announcement date cannot be in the future
        if ann_date and ann_date > today:
            print(f"   ⚠️ Future announcement date detected: {ann_str} - clearing")
            event["Announcement Date"] = ""
            ann_date = None
            fixed = True
        
        # Rule 2: Closed date cannot be in the future
        if closed_date and closed_date > today:
            print(f"   ⚠️ Future closed date detected: {closed_str} - clearing")
            event["Closed Date"] = ""
            closed_date = None
            fixed = True
        
        # Rule 3: If both dates exist, closed must be >= announcement
        if ann_date and closed_date:
            if closed_date < ann_date:
                # Dates are swapped - fix them
                print(f"   🔄 Swapped dates detected: Ann={ann_str}, Closed={closed_str} - fixing")
                event["Announcement Date"] = closed_str
                event["Closed Date"] = ann_str
                fixed = True
        
        # Rule 4: If only closed date and no announcement, that's suspicious for "announced" events
        # (Often the AI confuses article date with closed date)
        if closed_date and not ann_date and event.get("Deal Status") != "Completed":
            # Move closed_date to announcement_date if status isn't "Completed"
            print(f"   🔄 Only closed date for non-completed deal - moving to announcement: {closed_str}")
            event["Announcement Date"] = closed_str
            event["Closed Date"] = ""
            fixed = True
        
        if fixed:
            event_short = event.get("Event (short)", "")[:50]
            print(f"      → Fixed dates for: {event_short}...")
    
    return events


def parse_date(date_str):
    """
    Parses a date string into a datetime object.

    Args:
        date_str (str): The date string to parse (e.g., '2023-10-15' or 'October 15, 2023').

    Returns:
        datetime: Parsed datetime object, or 1900-01-01 if parsing fails.
    """
    # Handle empty or invalid date strings
    if not date_str:
        return datetime(1900, 1, 1)
    # Try multiple date formats
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%Y"):
        try:
            return datetime.strptime(date_str.split("T")[0], fmt)
        except:
            continue
    # Return default date if all formats fail
    return datetime(1900, 1, 1)

def has_recent_events(text, years=[2021, 2022, 2023, 2024, 2025]):
    """
    Checks if the text contains years within the specified range.

    Args:
        text (str): Text to search for years.
        years (list): List of years to check for (default: 2021–2025).

    Returns:
        bool: True if any specified year is found in the text, False otherwise.
    """
    # Extract all four-digit years from the text
    found_years = re.findall(r"\b(20\d{2})\b", text)
    # Check if any extracted year is in the provided list
    return any(int(y) in years for y in found_years)

# ============================================================
# 🔹 Wikipedia Summary Fetcher
# ============================================================
def get_wikipedia_summary(company_name):
    """
    Fetches a summary for the company from Wikipedia's REST API.

    Args:
        company_name (str): The name of the company to search for.

    Returns:
        str: The Wikipedia summary extract, or empty string if not found or on error.
    """
    # Set user-agent to avoid being blocked by Wikipedia
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        # Encode company name for URL safety
        encoded_name = quote(company_name.replace('&', '%26'))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_name}"
        # Send GET request to Wikipedia API
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            # Return extract if available and not a disambiguation page
            if "extract" in data and data.get("type") != "disambiguation":
                return data["extract"]
    except Exception as e:
        # Log error and return empty string if the request fails
        print(f"⚠️ Wikipedia fetch error: {e}")
    return ""

# ============================================================
# 🔹 Top Management Fetcher
# ============================================================
def _format_management_list(man_list):
    """
    Converts a list of management dictionaries into a formatted string.

    Args:
        man_list (list): List of dictionaries with 'name' and 'role' keys.

    Returns:
        str: A semicolon-separated string of the format 'Name — Role; Name2 — Role2; ...'.
    """
    if not man_list:
        return ""
    formatted_entries = []
    for item in man_list:
        name = item.get("name", "").strip()
        role = item.get("role", "").strip()
        if name and role:
            formatted_entries.append(f"{name} — {role}")
        elif name:
            formatted_entries.append(f"{name}")
    return "; ".join(formatted_entries)

def search_linkedin_startpage(query: str) -> str:
    """
    Real-time search using Startpage (privacy-focused, uses Google results).
    Query format: "company name" + "person name" + linkedin
    Returns the first LinkedIn profile URL found.
    """
    try:
        import urllib.parse
        
        # Startpage search URL
        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://www.startpage.com/sp/search?query={encoded_query}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        if response.status_code != 200:
            return ""
        
        # Find LinkedIn profile URLs in the HTML
        matches = re.findall(
            r'linkedin\.com/in/([a-zA-Z0-9_-]+)',
            response.text
        )
        
        # Return first unique LinkedIn profile found
        seen = set()
        for username in matches:
            # Skip if too short or already seen
            if len(username) < 3 or username in seen:
                continue
            # Skip common non-profile patterns
            if username in ['in', 'pub', 'company', 'jobs', 'pulse']:
                continue
            seen.add(username)
            return f"https://www.linkedin.com/in/{username}"
        
        return ""
        
    except Exception as e:
        print(f"⚠️ Startpage search error: {e}")
        return ""


def search_company_linkedin_startpage(query: str, clean_name: str = "", domain_hint: str = "") -> str:
    """
    Fallback: find a COMPANY LinkedIn page using Startpage (Google results proxy).
    Looks for linkedin.com/company/ slugs and returns a normalized URL.
    """
    try:
        import urllib.parse

        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://www.startpage.com/sp/search?query={encoded_query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        response = requests.get(search_url, headers=headers, timeout=15)
        if response.status_code != 200:
            return ""

        matches = re.findall(
            r"linkedin\.com/(?:company|school)/([a-zA-Z0-9_-]+)",
            response.text,
            re.I,
        )

        if not matches:
            return ""

        name_words = set(clean_name.lower().split()) if clean_name else set()
        domain_hint_clean = domain_hint.replace(".", "").replace("-", "").lower()
        best_url = ""

        seen = set()
        for slug in matches:
            slug_lower = slug.lower()
            if slug_lower in seen:
                continue
            seen.add(slug_lower)

            # Skip obvious non-company slugs
            if slug_lower in {"company", "jobs", "pulse", "learning", "about"}:
                continue

            slug_words = set(slug_lower.replace("-", " ").replace("_", " ").split())
            score = 0
            if domain_hint_clean and domain_hint_clean in slug_lower.replace("-", ""):
                score += 2
            if name_words and len(name_words & slug_words) >= 1:
                score += 1

            normalized = f"https://www.linkedin.com/company/{slug_lower.strip('/')}/"
            # Prefer a scored match; otherwise keep the first plausible URL
            if score > 0:
                return normalized
            if not best_url:
                best_url = normalized

        return best_url
    except Exception as e:
        print(f"⚠️ Startpage company search error: {e}")
        return ""


def search_linkedin_profile(name: str, company_name: str, position: str = "") -> str:
    """
    Searches for a person's LinkedIn profile URL.
    Delegates to search_person_linkedin() which uses the optimal formula:
    "Name" Company Role linkedin site:linkedin.com/in
    and trusts Google's first result.
    """
    if not name:
        return ""
    
    # Clean the name
    clean_name = re.sub(r'\b(Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Jr\.?|Sr\.?|III|II|IV)\b', '', name, flags=re.I).strip()
    
    # Use the main search_person_linkedin function which has the correct "trust first result" logic
    return search_person_linkedin(clean_name, company_name, position)


def search_linkedin_profile_serpapi(name: str, company_name: str, position: str = "") -> str:
    """
    Backup: Searches for LinkedIn using SerpAPI (if available and not rate limited).
    """
    if not name or not SERPAPI_KEY:
        return ""
    
    clean_name = re.sub(r'\b(Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Jr\.?|Sr\.?|III|II|IV)\b', '', name, flags=re.I).strip()
    name_parts = clean_name.lower().split()
    
    if len(name_parts) < 2:
        return ""
    
    first_name = name_parts[0]
    last_name = name_parts[-1]
    
    search_queries = [
        f'"{clean_name}" "{company_name}" site:linkedin.com/in/',
        f'{first_name} {last_name} {company_name} linkedin',
    ]
    
    for query in search_queries:
        try:
            params = {"q": query, "num": 5, "api_key": SERPAPI_KEY}
            search = GoogleSearch(params)
            result = search.get_dict()
            
            # Check for rate limit error
            if "error" in result:
                print(f"⚠️ SerpAPI error: {result['error']}")
                return ""
            
            results = result.get("organic_results", [])
            for r in results:
                link = r.get("link", "")
                if "linkedin.com/in/" in link and "/company/" not in link:
                    return link
                    
        except Exception as e:
            print(f"⚠️ SerpAPI error: {e}")
            return ""
    
    return ""


def search_company_website(company_name: str) -> str:
    """
    Search for a company's official website URL using SerpAPI.
    Uses multiple query strategies for accuracy.
    
    Args:
        company_name: The company name to search for
        
    Returns:
        Company website URL if found, empty string otherwise
    """
    if not company_name or not SERPAPI_KEY:
        return ""
    
    # Clean company name
    clean_name = company_name.strip()
    for suffix in [', Inc.', ', Inc', ' Inc.', ' Inc', ', LLC', ' LLC', ', Ltd.', ', Ltd', ' Ltd.', ' Ltd',
                   ', Corp.', ', Corp', ' Corp.', ' Corp', ', Limited', ' Limited', ', Co.', ' Co.']:
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)].strip()
    
    # Search queries ordered by specificity — run all in parallel
    search_queries = [
        f'"{clean_name}" official website',
        f'{clean_name} company website',
        f'"{company_name}"',
    ]

    skip_domains = ['linkedin.com', 'facebook.com', 'twitter.com', 'x.com',
                    'crunchbase.com', 'bloomberg.com', 'reuters.com', 'wikipedia.org',
                    'zoominfo.com', 'dnb.com', 'glassdoor.com', 'indeed.com',
                    'yelp.com', 'yellowpages.com', 'bbb.org', 'manta.com']
    name_lower = clean_name.lower()
    name_words = set(name_lower.split())

    def _run_website_query(query):
        try:
            params = {"q": query, "num": 10, "api_key": SERPAPI_KEY}
            result = GoogleSearch(params).get_dict()
            if "error" in result:
                return None
            for r in result.get("organic_results", []):
                link = r.get("link", "")
                title = r.get("title", "").lower()
                if any(d in link.lower() for d in skip_domains):
                    continue
                try:
                    from urllib.parse import urlparse as _up
                    _parsed = _up(link)
                    domain = _parsed.netloc.lower().replace('www.', '')
                    domain_name = domain.split('.')[0] if '.' in domain else domain
                except Exception:
                    domain_name = ""
                name_in_title = name_lower in title or any(w in title for w in name_words if len(w) > 3)
                name_in_domain = any(w in domain_name for w in name_words if len(w) > 3)
                if name_in_title or name_in_domain:
                    return link if link.startswith('http') else f"https://{link}"
        except Exception as e:
            print(f"   ⚠️ SerpAPI website search error: {e}")
        return None

    with ThreadPoolExecutor(max_workers=len(search_queries)) as _ws_ex:
        futures = [_ws_ex.submit(_run_website_query, q) for q in search_queries]
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                print(f"   🌐 Found website for {company_name}: {result}")
                return result

    return ""


def _clean_company_name_for_lookup(company_name: str) -> str:
    clean_name = (company_name or "").strip()
    for suffix in [', Inc.', ', Inc', ' Inc.', ' Inc', ', LLC', ' LLC', ', Ltd.', ', Ltd', ' Ltd.', ' Ltd',
                   ', Corp.', ', Corp', ' Corp.', ' Corp', ', Limited', ' Limited', ', Co.', ' Co.']:
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)].strip()
    return clean_name


def _extract_domain_hint(website_url: str = "", fallback_value: str = "") -> str:
    url_for_domain = (website_url or "").strip()
    if not url_for_domain and (fallback_value or "").strip().startswith("http"):
        url_for_domain = (fallback_value or "").strip()
    if not url_for_domain:
        return ""

    try:
        from urllib.parse import urlparse
        parsed = urlparse(url_for_domain)
        domain = (parsed.netloc or parsed.path or "").replace("www.", "")
        domain = domain.split('.')[0] if '.' in domain else domain
        return domain if domain and len(domain) > 2 else ""
    except Exception:
        return ""


def search_company_linkedin_detailed(company_name: str, website_url: str = "") -> dict:
    """
    Search for a company's LinkedIn page and return provenance metadata.
    """
    if not company_name and not website_url:
        return {
            "linkedin_url": "",
            "source": "not_found",
            "matched_by": "",
            "query_used": "",
            "queries_used": [],
        }

    serpapi_available = bool(SERPAPI_KEY)
    clean_name = _clean_company_name_for_lookup(company_name)
    domain_hint = _extract_domain_hint(website_url, clean_name)

    serpapi_queries = [
        f'"{clean_name}" site:linkedin.com/company/' if clean_name else None,
        f'"{clean_name}" {domain_hint} site:linkedin.com/company/' if clean_name and domain_hint else None,
        f'{clean_name} linkedin company page' if clean_name else None,
        f'"{company_name}" linkedin' if company_name else None,
    ]
    serpapi_queries = [q for q in serpapi_queries if q and serpapi_available]

    fallback_queries = []
    if domain_hint:
        fallback_queries.extend([
            f"{domain_hint} linkedin",
            f"{domain_hint} linkedin company",
            f"{domain_hint}.com linkedin",
        ])
    if clean_name:
        fallback_queries.append(f"{clean_name} linkedin")
    if company_name:
        fallback_queries.append(f"{company_name} linkedin")

    queries_used = list(serpapi_queries) + list(fallback_queries)
    found_urls = []
    clean_name_lower = clean_name.lower()
    name_words = set(clean_name_lower.split())

    def _run_serpapi_li_query(query):
        """Run one SerpAPI query, return matching LinkedIn URL or None."""
        try:
            params = {"q": query, "num": 10, "api_key": SERPAPI_KEY}
            result = GoogleSearch(params).get_dict()
            if "error" in result:
                return None, []
            candidates = []
            for r in result.get("organic_results", []):
                link = r.get("link", "")
                title = r.get("title", "").lower()
                if "linkedin.com/company/" not in link:
                    continue
                m = re.search(r'linkedin\.com/company/([a-zA-Z0-9_-]+)', link)
                if not m:
                    continue
                slug = m.group(1).lower()
                if slug in ['company', 'jobs', 'pulse', 'learning', 'about']:
                    continue
                slug_words = set(slug.replace('-', ' ').replace('_', ' ').split())
                name_in_title = clean_name_lower in title if clean_name_lower else False
                name_in_slug = any(w in slug for w in name_words if len(w) > 2)
                slug_matches = len(name_words & slug_words) >= 1 if name_words else False
                normalized = f"https://www.linkedin.com/company/{slug}/"
                candidates.append(normalized)
                if name_in_title or name_in_slug or slug_matches:
                    return normalized, candidates
            return None, candidates
        except Exception as e:
            print(f"   ⚠️ SerpAPI company search error: {e}")
            return None, []

    def _run_startpage_li_query(query):
        """Run one Startpage fallback query, return URL or None."""
        try:
            url = search_company_linkedin_startpage(query, clean_name=clean_name, domain_hint=domain_hint)
            return url or None
        except Exception:
            return None

    # Run all SerpAPI queries in parallel
    with ThreadPoolExecutor(max_workers=max(len(serpapi_queries), 1)) as _li_ex:
        sp_futures = {_li_ex.submit(_run_serpapi_li_query, q): q for q in serpapi_queries}
        for fut in as_completed(sp_futures):
            matched_url, candidates = fut.result()
            for c in candidates:
                if c not in found_urls:
                    found_urls.append(c)
            if matched_url:
                print(f"   🔗 Found LinkedIn for {company_name}: {matched_url}")
                return {
                    "linkedin_url": matched_url,
                    "source": "serpapi",
                    "matched_by": "website_domain" if domain_hint else "company_name",
                    "query_used": sp_futures[fut],
                    "queries_used": queries_used,
                }

    # Run all Startpage fallback queries in parallel
    with ThreadPoolExecutor(max_workers=max(len(fallback_queries), 1)) as _fb_ex:
        fb_futures = {_fb_ex.submit(_run_startpage_li_query, q): q for q in fallback_queries}
        for fut in as_completed(fb_futures):
            url = fut.result()
            if url:
                print(f"   🔗 Startpage LinkedIn for {company_name}: {url}")
                return {
                    "linkedin_url": url,
                    "source": "startpage",
                    "matched_by": "website_domain" if domain_hint else "fallback",
                    "query_used": fb_futures[fut],
                    "queries_used": queries_used,
                }

    if found_urls:
        return {
            "linkedin_url": found_urls[0],
            "source": "serpapi",
            "matched_by": "website_domain" if domain_hint else "company_name",
            "query_used": queries_used[0] if queries_used else "",
            "queries_used": queries_used,
        }

    return {
        "linkedin_url": "",
        "source": "not_found",
        "matched_by": "",
        "query_used": "",
        "queries_used": queries_used,
    }


def search_company_linkedin(company_name: str, website_url: str = "") -> str:
    """
    Backward-compatible wrapper returning only the LinkedIn URL.
    """
    result = search_company_linkedin_detailed(company_name, website_url)
    return result.get("linkedin_url", "")


def search_person_linkedin(person_name: str, company_name: str = "", position: str = "") -> str:
    """
    Search for a PERSON's LinkedIn profile using SerpAPI or Startpage.
    Uses query variants like: "{person name} {position} {company name} linkedin"
    
    Args:
        person_name: The person's name to search for
        company_name: Optional company name to help narrow results
        position: Optional role/title to improve disambiguation (e.g., CEO, CFO, Partner)
        
    Returns:
        LinkedIn profile URL if found, empty string otherwise
    """
    if not person_name:
        return ""
    
    serpapi_available = bool(SERPAPI_KEY)
    
    # Clean names for better matching
    clean_person = person_name.strip()
    clean_company = (company_name or "").strip()
    
    # Remove common company suffixes
    for suffix in [', Inc.', ', Inc', ' Inc.', ' Inc', ', LLC', ' LLC', ', Ltd.', ', Ltd', ' Ltd.', ' Ltd',
                   ', Corp.', ', Corp', ' Corp.', ' Corp', ', Limited', ' Limited', ', Co.', ' Co.']:
        if clean_company.endswith(suffix):
            clean_company = clean_company[:-len(suffix)].strip()
    
    # Clean position for better matching
    clean_position = (position or "").strip()

    def _role_acronyms(pos: str) -> list:
        """
        Extract common C-level acronyms from a verbose title.
        Example: "Co-Founder and Chief Executive Officer" -> ["CEO"]
        """
        pos = (pos or "").strip()
        if not pos:
            return []
        acr = []
        for m in re.finditer(r"\bChief\s+([A-Za-z][A-Za-z\s]{0,40}?)\s+Officer\b", pos, re.I):
            mid = (m.group(1) or "").strip()
            parts = [p for p in re.split(r"[^A-Za-z]+", mid) if p]
            if parts:
                candidate = "C" + "".join(p[0].upper() for p in parts) + "O"
                if candidate in {"CEO", "CFO", "CTO", "COO", "CPO", "CMO", "CIO", "CRO", "CISO"}:
                    acr.append(candidate)
        for token in ["CEO", "CFO", "CTO", "COO", "CPO", "CMO", "CIO", "CRO", "CISO", "SVP", "EVP", "VP"]:
            if re.search(rf"\b{re.escape(token)}\b", pos, re.I):
                acr.append(token)
        out = []
        for a in acr:
            if a not in out:
                out.append(a)
        return out

    role_terms = []
    if clean_position:
        role_terms.append(clean_position)
        role_terms.extend(_role_acronyms(clean_position))

    # Build search queries (most specific first)
    # IMPORTANT: put the exact "name + company + role + linkedin" formula first.
    queries = []
    if clean_company and role_terms:
        for role in role_terms[:2]:
            queries.append(f'"{clean_person}" {clean_company} {role} linkedin site:linkedin.com/in')
            queries.append(f'"{clean_person}" "{clean_company}" "{role}" linkedin site:linkedin.com/in')
    if clean_company:
        queries.append(f'"{clean_person}" {clean_company} linkedin site:linkedin.com/in')
        queries.append(f'"{clean_person}" "{clean_company}" site:linkedin.com/in/')
        queries.append(f'{clean_person} {clean_company} linkedin')
    if role_terms:
        for role in role_terms[:1]:
            queries.append(f'"{clean_person}" {role} linkedin site:linkedin.com/in')
    queries.append(f'"{clean_person}" linkedin site:linkedin.com/in')
    queries.append(f'"{clean_person}" site:linkedin.com/in/')
    queries.append(f'{clean_person} linkedin profile')

    # Try SerpAPI first
    if serpapi_available:
        # -----------------------------------------------------------------
        # STRATEGY: Trust Google's ranking for high-quality queries.
        # For the FIRST query (name + company + role + linkedin), just grab
        # the first linkedin.com/in/ result. Only use scoring for fallbacks.
        # -----------------------------------------------------------------
        for query_idx, query in enumerate(queries[:6]):
            try:
                params = {"q": query, "num": 10, "api_key": SERPAPI_KEY}
                search = GoogleSearch(params)
                result = search.get_dict()
                
                if "error" in result:
                    print(f"   ⚠️ SerpAPI person LinkedIn search error: {result['error']}")
                    continue
                
                results = result.get("organic_results", [])
                
                for r in results:
                    link = r.get("link", "")
                    title_raw = r.get("title", "") or ""
                    title = title_raw.lower()
                    
                    # Must be a LinkedIn profile (not company page, not posts)
                    if "linkedin.com/in/" not in link:
                        continue
                    if any(bad in link.lower() for bad in ["/posts/", "/pulse/", "/jobs/", "/company/"]):
                        continue
                    
                    # Extract profile slug
                    match = re.search(r'linkedin\.com/in/([a-zA-Z0-9_-]+)', link)
                    if not match:
                        continue
                    
                    profile_slug = match.group(1).lower()
                    
                    # Skip generic slugs
                    if profile_slug in ['in', 'pub', 'profile', 'company', 'jobs']:
                        continue
                    
                    normalized_url = f"https://www.linkedin.com/in/{profile_slug}/"

                    # ---------------------------------------------------------
                    # SLUG NAME CHECK (all queries): reject URLs where neither
                    # first nor last name appears in the slug.
                    # e.g. "Sachin Kalwani" must not accept "rahul-jha-03978a37"
                    # ---------------------------------------------------------
                    person_lower = clean_person.lower()
                    name_parts = [p for p in person_lower.split() if len(p) > 1]
                    first_name = name_parts[0] if name_parts else ""
                    last_name = name_parts[-1] if len(name_parts) >= 2 else ""

                    slug_has_first = first_name and first_name in profile_slug
                    slug_has_last = last_name and last_name in profile_slug

                    # For names with 2+ parts, require at least first OR last name in slug
                    if len(name_parts) >= 2 and not slug_has_first and not slug_has_last:
                        print(f"   ⛔ Slug mismatch — skipping {normalized_url} for {person_name}")
                        continue  # try next search result

                    # ---------------------------------------------------------
                    # PRIMARY QUERIES (index 0-1): slug passed, trust this result
                    # ---------------------------------------------------------
                    if query_idx <= 1:
                        print(f"   🔗 Found LinkedIn for {person_name}: {normalized_url} (trusted first result)")
                        return normalized_url

                    # ---------------------------------------------------------
                    # FALLBACK QUERIES (index 2+): also require name in title/snippet
                    # ---------------------------------------------------------
                    # Check if last name appears in title (most important)
                    if last_name and last_name in title:
                        print(f"   🔗 Found LinkedIn for {person_name}: {normalized_url}")
                        return normalized_url
                            
            except Exception as e:
                print(f"   ⚠️ SerpAPI person search error: {e}")
                continue
    
    # Fallback: Startpage search with stricter matching
    try:
        import urllib.parse
        
        # More specific query with quotes for exact name match
        fallback_queries = []
        if clean_company and clean_position:
            fallback_queries.append(f'"{clean_person}" "{clean_company}" "{clean_position}" linkedin')
        if clean_company:
            fallback_queries.append(f'"{clean_person}" "{clean_company}" linkedin')
        if clean_position:
            fallback_queries.append(f'"{clean_person}" "{clean_position}" linkedin')
        fallback_queries.append(f'"{clean_person}" linkedin')
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        
        for fq in fallback_queries[:3]:
            encoded_query = urllib.parse.quote_plus(fq.strip())
            search_url = f"https://www.startpage.com/sp/search?query={encoded_query}"

            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code != 200:
                continue

            # Look for linkedin.com/in/ URLs
            linkedin_matches = re.findall(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)', response.text)

            for profile_slug in linkedin_matches:
                profile_slug = profile_slug.lower()
                if profile_slug in ['in', 'pub', 'profile', 'company', 'jobs']:
                    continue

                # Stricter matching - require BOTH first AND last name in slug
                person_lower = clean_person.lower()
                name_parts = [p for p in person_lower.split() if len(p) > 1]

                if len(name_parts) >= 2:
                    first_name = name_parts[0]
                    last_name = name_parts[-1]
                    # Both first and last name should appear in slug
                    if first_name in profile_slug and last_name in profile_slug:
                        normalized_url = f"https://www.linkedin.com/in/{profile_slug}/"
                        print(f"   🔗 Startpage LinkedIn for {person_name}: {normalized_url}")
                        return normalized_url
                elif name_parts:
                    # Single name - just check that one
                    if name_parts[0] in profile_slug:
                        normalized_url = f"https://www.linkedin.com/in/{profile_slug}/"
                        print(f"   🔗 Startpage LinkedIn for {person_name}: {normalized_url}")
                        return normalized_url
                    
    except Exception as e:
        print(f"   ⚠️ Startpage person search error: {e}")
    
    print(f"   ⚠️ No LinkedIn found for: {person_name}")
    return ""


def search_person_linkedin_with_location(person_name: str, company_name: str = "", position: str = "") -> dict:
    """
    Search for a person's LinkedIn profile AND extract location from SEO snippets in one call.
    This is more efficient than calling search_person_linkedin + search_person_location separately.
    
    Returns: {"linkedin_url": "...", "location": {"city": "", "state": "", "country": ""}}
    """
    result = {"linkedin_url": "", "location": {"city": "", "state": "", "country": ""}}
    
    if not person_name:
        return result
    
    serpapi_available = bool(SERPAPI_KEY)
    
    # Clean names for better matching
    clean_person = person_name.strip()
    clean_company = (company_name or "").strip()
    clean_company_lower = clean_company.lower()
    
    # Remove common company suffixes
    for suffix in [', Inc.', ', Inc', ' Inc.', ' Inc', ', LLC', ' LLC', ', Ltd.', ', Ltd', ' Ltd.', ' Ltd',
                   ', Corp.', ', Corp', ' Corp.', ' Corp', ', Limited', ' Limited', ', Co.', ' Co.']:
        if clean_company.endswith(suffix):
            clean_company = clean_company[:-len(suffix)].strip()
            clean_company_lower = clean_company.lower()
    
    # Extract key words from company name for matching (e.g., "S&P Global" -> ["s&p", "global"])
    company_keywords = [w.lower() for w in re.split(r'\s+', clean_company) if len(w) > 2]
    
    clean_position = (position or "").strip()

    def _role_acronyms(pos: str) -> list:
        pos = (pos or "").strip()
        if not pos:
            return []
        acr = []
        for m in re.finditer(r"\bChief\s+([A-Za-z][A-Za-z\s]{0,40}?)\s+Officer\b", pos, re.I):
            mid = (m.group(1) or "").strip()
            parts = [p for p in re.split(r"[^A-Za-z]+", mid) if p]
            if parts:
                candidate = "C" + "".join(p[0].upper() for p in parts) + "O"
                if candidate in {"CEO", "CFO", "CTO", "COO", "CPO", "CMO", "CIO", "CRO", "CISO"}:
                    acr.append(candidate)
        for token in ["CEO", "CFO", "CTO", "COO", "CPO", "CMO", "CIO", "CRO", "CISO", "SVP", "EVP", "VP"]:
            if re.search(rf"\b{re.escape(token)}\b", pos, re.I):
                acr.append(token)
        out = []
        for a in acr:
            if a not in out:
                out.append(a)
        return out

    role_terms = []
    if clean_position:
        role_terms.append(clean_position)
        role_terms.extend(_role_acronyms(clean_position))

    # Build search queries (most specific first)
    queries = []
    if clean_company and role_terms:
        for role in role_terms[:2]:
            queries.append(f'"{clean_person}" "{clean_company}" {role} linkedin site:linkedin.com/in')
    if clean_company:
        queries.append(f'"{clean_person}" "{clean_company}" linkedin site:linkedin.com/in')
        queries.append(f'"{clean_person}" {clean_company} linkedin site:linkedin.com/in')
    if role_terms:
        for role in role_terms[:1]:
            queries.append(f'"{clean_person}" {role} linkedin site:linkedin.com/in')
    queries.append(f'"{clean_person}" linkedin site:linkedin.com/in')

    # Country normalization
    country_map = {
        "uk": "United Kingdom", "u.k.": "United Kingdom", "u.k": "United Kingdom",
        "usa": "USA", "us": "USA", "u.s.": "USA", "u.s.a.": "USA",
        "united states": "USA", "united states of america": "USA",
    }
    
    def normalize_country(c: str) -> str:
        c = (c or "").strip()
        if not c:
            return ""
        return country_map.get(c.lower(), c)

    def extract_location_from_snippet(text: str, title: str = "") -> dict:
        """
        Extract location from LinkedIn SEO result.
        
        LinkedIn title formats (MOST RELIABLE):
        - "Erica Bourne - London, England, United Kingdom"
        - "John Smith - San Francisco, California, United States"
        - "Jane Doe - New York, New York, United States"
        
        LinkedIn snippet formats:
        - "Location: London · 500+ connections..."
        - "Location: Santiago de Surco, Peru"
        """
        if not text and not title:
            return {}
        
        # PATTERN 0 (MOST RELIABLE): LinkedIn title format "Name - City, State, Country"
        # This is the most reliable source as LinkedIn always formats titles this way
        if title:
            # Match "Name - Location" where location has comma-separated parts
            # Pattern must have at least one comma to be a valid location
            title_match = re.search(r'\s+-\s+([A-Z][a-zA-Z\s]+(?:,\s*[A-Z][a-zA-Z\s]+)+)\s*$', title)
            if title_match:
                loc_part = title_match.group(1).strip()
                # Sanity check: location should be reasonably short (< 80 chars)
                if len(loc_part) < 80:
                    parts = [p.strip() for p in loc_part.split(",") if p and p.strip()]
                    if len(parts) >= 3:
                        return {"city": parts[0], "state": parts[1], "country": normalize_country(parts[2])}
                    if len(parts) == 2:
                        second = parts[1]
                        if len(second.strip()) == 2 and second.strip().isalpha():
                            return {"city": parts[0], "state": second.strip().upper(), "country": "USA"}
                        return {"city": parts[0], "state": "", "country": normalize_country(second)}
        
        # PATTERN 1: LinkedIn snippet "Location: X" - but extract BEFORE the delimiter
        # Note: snippet often has "Location: London · 500+ connections" - we only get "London"
        # So we need to combine with title data above
        m = re.search(r"\blocation\s*[:\-]\s*([^\n\r|•·]+)", text, re.IGNORECASE)
        if m:
            raw = (m.group(1) or "").strip()
            # Stop at common delimiters
            raw = re.split(r"\s*(?:\|\s*|•\s*|·\s*|Education|Experience|Connections|\d+\s*connections)", raw, flags=re.I)[0].strip()
            raw = re.sub(r"\s+\d+\+.*$", "", raw).strip()
            if raw:
                parts = [p.strip() for p in raw.split(",") if p and p.strip()]
                if len(parts) >= 3:
                    return {"city": parts[0], "state": parts[1], "country": normalize_country(parts[2])}
                if len(parts) == 2:
                    second = parts[1]
                    if len(second.strip()) == 2 and second.strip().isalpha():
                        return {"city": parts[0], "state": second.strip().upper(), "country": "USA"}
                    return {"city": parts[0], "state": "", "country": normalize_country(second)}
                if len(parts) == 1:
                    loc = parts[0]
                    area_match = re.match(r"(?:Greater\s+)?(.+?)(?:\s+(?:Area|Metropolitan|Metro))?\s*$", loc, re.I)
                    if area_match:
                        loc = area_match.group(1).strip()
                    if normalize_country(loc) != loc or loc.lower() in country_map:
                        return {"city": "", "state": "", "country": normalize_country(loc)}
                    return {"city": loc, "state": "", "country": ""}
        
        # PATTERN 2: Look for "City, State, Country" anywhere in combined text
        loc_pattern = re.search(r"([A-Z][a-z]+(?:\s+[A-Za-z]+)*),\s*([A-Z][a-z]+(?:\s+[A-Za-z]+)*),\s*([A-Z][a-z]+(?:\s+[A-Za-z]+)*)", text)
        if loc_pattern:
            city, state, country = loc_pattern.groups()
            return {"city": city.strip(), "state": state.strip(), "country": normalize_country(country.strip())}
        
        # PATTERN 3: "City, Country" pattern for international locations
        loc_pattern2 = re.search(r"([A-Z][a-z]+(?:\s+de\s+[A-Z][a-z]+)?(?:\s+[A-Za-z]+)*),\s+(Peru|Chile|Argentina|Brazil|Mexico|Colombia|Spain|France|Germany|Italy|United Kingdom|UK|USA|United States|Canada|Australia|India|China|Japan|Singapore|Hong Kong|Belgium|Netherlands|Switzerland|Sweden|Norway|Denmark|Ireland|Austria|Poland|Portugal)", text, re.I)
        if loc_pattern2:
            city, country = loc_pattern2.groups()
            return {"city": city.strip(), "state": "", "country": normalize_country(country.strip())}
        
        return {}
    
    def verify_company_match(text: str) -> bool:
        """Check if the snippet/title mentions the company name"""
        if not clean_company:
            return True  # No company to verify
        text_lower = text.lower()
        # Check if company name or its keywords appear in text
        if clean_company_lower in text_lower:
            return True
        # Check if majority of company keywords appear
        if company_keywords:
            matches = sum(1 for kw in company_keywords if kw in text_lower)
            return matches >= len(company_keywords) * 0.5
        return False

    # Try SerpAPI first
    if serpapi_available:
        for query_idx, query in enumerate(queries[:6]):
            try:
                params = {"q": query, "num": 10, "api_key": SERPAPI_KEY}
                search = GoogleSearch(params)
                api_result = search.get_dict()
                
                if "error" in api_result:
                    print(f"   ⚠️ SerpAPI error: {api_result['error']}")
                    continue
                
                results = api_result.get("organic_results", [])
                
                for r in results:
                    link = r.get("link", "")
                    title_raw = r.get("title", "") or ""
                    snippet = r.get("snippet", "") or ""
                    title = title_raw.lower()
                    combined = f"{title_raw} {snippet}"
                    
                    # Must be a LinkedIn profile
                    if "linkedin.com/in/" not in link:
                        continue
                    if any(bad in link.lower() for bad in ["/posts/", "/pulse/", "/jobs/", "/company/"]):
                        continue
                    
                    # Extract profile slug
                    match = re.search(r'linkedin\.com/in/([a-zA-Z0-9_-]+)', link)
                    if not match:
                        continue
                    
                    profile_slug = match.group(1).lower()
                    
                    if profile_slug in ['in', 'pub', 'profile', 'company', 'jobs']:
                        continue
                    
                    normalized_url = f"https://www.linkedin.com/in/{profile_slug}/"
                    
                    # IMPORTANT: Verify company appears in snippet to avoid wrong person
                    # For primary queries with company, require company verification
                    if query_idx <= 1 and clean_company:
                        if not verify_company_match(combined):
                            print(f"   ⚠️ Skipping {normalized_url} - company '{clean_company}' not in snippet")
                            continue
                    
                    # Slug name check — reject if neither first nor last name in slug
                    person_lower = clean_person.lower()
                    name_parts = [p for p in person_lower.split() if len(p) > 1]
                    last_name = name_parts[-1] if len(name_parts) >= 2 else ""
                    first_name = name_parts[0] if name_parts else ""

                    slug_has_first = first_name and first_name in profile_slug
                    slug_has_last = last_name and last_name in profile_slug

                    if len(name_parts) >= 2 and not slug_has_first and not slug_has_last:
                        print(f"   ⛔ Slug mismatch — skipping {normalized_url} for {person_name}")
                        continue

                    # For primary queries, trust first result (after company + slug verification)
                    if query_idx <= 1:
                        result["linkedin_url"] = normalized_url
                        # Extract location - pass title separately for "Name - City, State, Country" parsing
                        loc = extract_location_from_snippet(snippet, title_raw)
                        if loc:
                            result["location"] = {"city": loc.get("city", ""), "state": loc.get("state", ""), "country": loc.get("country", "")}
                        print(f"   🔗 Found LinkedIn+Location for {person_name}: {normalized_url}, loc={result['location']}")
                        return result

                    # Fallback queries - require name in title AND company match
                    if last_name and last_name in title:
                        if clean_company and not verify_company_match(combined):
                            continue
                        result["linkedin_url"] = normalized_url
                        loc = extract_location_from_snippet(snippet, title_raw)
                        if loc:
                            result["location"] = {"city": loc.get("city", ""), "state": loc.get("state", ""), "country": loc.get("country", "")}
                        print(f"   🔗 Found LinkedIn+Location for {person_name}: {normalized_url}, loc={result['location']}")
                        return result
                            
            except Exception as e:
                print(f"   ⚠️ SerpAPI error: {e}")
                continue
    
    # Startpage fallback
    try:
        import urllib.parse
        
        fallback_queries = []
        if clean_company and clean_position:
            fallback_queries.append(f'"{clean_person}" "{clean_company}" "{clean_position}" linkedin')
        if clean_company:
            fallback_queries.append(f'"{clean_person}" "{clean_company}" linkedin')
        fallback_queries.append(f'"{clean_person}" linkedin')
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        
        for fq in fallback_queries[:3]:
            encoded_query = urllib.parse.quote_plus(fq.strip())
            search_url = f"https://www.startpage.com/sp/search?query={encoded_query}"

            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code != 200:
                continue

            # Look for LinkedIn URLs
            linkedin_matches = re.findall(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)', response.text)

            for profile_slug in linkedin_matches:
                profile_slug = profile_slug.lower()
                if profile_slug in ['in', 'pub', 'profile', 'company', 'jobs']:
                    continue

                person_lower = clean_person.lower()
                name_parts = [p for p in person_lower.split() if len(p) > 1]

                if len(name_parts) >= 2:
                    first_name = name_parts[0]
                    last_name = name_parts[-1]
                    if first_name in profile_slug and last_name in profile_slug:
                        normalized_url = f"https://www.linkedin.com/in/{profile_slug}/"
                        result["linkedin_url"] = normalized_url
                        # Try to extract location from full page text
                        loc = extract_location_from_snippet(response.text)
                        if loc:
                            result["location"] = {"city": loc.get("city", ""), "state": loc.get("state", ""), "country": loc.get("country", "")}
                        print(f"   🔗 Startpage LinkedIn+Location for {person_name}: {normalized_url}, loc={result['location']}")
                        return result
                elif name_parts:
                    if name_parts[0] in profile_slug:
                        normalized_url = f"https://www.linkedin.com/in/{profile_slug}/"
                        result["linkedin_url"] = normalized_url
                        loc = extract_location_from_snippet(response.text)
                        if loc:
                            result["location"] = {"city": loc.get("city", ""), "state": loc.get("state", ""), "country": loc.get("country", "")}
                        print(f"   🔗 Startpage LinkedIn+Location for {person_name}: {normalized_url}, loc={result['location']}")
                        return result
                    
    except Exception as e:
        print(f"   ⚠️ Startpage error: {e}")
    
    print(f"   ⚠️ No LinkedIn found for: {person_name}")
    return result


def search_person_location(person_name: str, company_name: str = "", linkedin_url: str = "", position: str = "") -> dict:
    """
    Find a person's likely location (city/state/country) using SerpAPI or Startpage snippets.
    Returns: {"city": "", "state": "", "country": ""} (any may be empty)
    """
    result = {"city": "", "state": "", "country": ""}
    person_name = (person_name or "").strip()
    company_name = (company_name or "").strip()
    linkedin_url = (linkedin_url or "").strip()
    position = (position or "").strip()

    if not person_name:
        return result

    # Lightweight normalizers
    country_map = {
        "uk": "United Kingdom",
        "u.k.": "United Kingdom",
        "u.k": "United Kingdom",
        "usa": "USA",
        "us": "USA",
        "u.s.": "USA",
        "u.s.a.": "USA",
        "united states": "USA",
        "united states of america": "USA",
    }

    # Major US city → state heuristic (keeps it small + high-signal)
    us_city_to_state = {
        "san francisco": "California",
        "los angeles": "California",
        "san jose": "California",
        "new york": "New York",
        "new york city": "New York",
        "nyc": "New York",
        "seattle": "Washington",
        "boston": "Massachusetts",
        "cambridge": "Massachusetts",
        "chicago": "Illinois",
        "austin": "Texas",
        "dallas": "Texas",
        "houston": "Texas",
        "miami": "Florida",
        "denver": "Colorado",
        "atlanta": "Georgia",
        "phoenix": "Arizona",
        "portland": "Oregon",
    }

    def normalize_country(c: str) -> str:
        c = (c or "").strip()
        if not c:
            return ""
        return country_map.get(c.lower(), c)

    def finalize_location(loc: dict) -> dict:
        if not loc:
            return loc
        city = (loc.get("city") or "").strip()
        state = (loc.get("state") or "").strip()
        country = normalize_country(loc.get("country") or "")
        loc["country"] = country
        city_low = city.lower().strip()

        # Infer USA when a high-signal US city/region is detected but country is missing
        if city and not country:
            if "bay area" in city_low or "san francisco" in city_low:
                loc["country"] = "USA"
                country = "USA"
            elif city_low in us_city_to_state:
                loc["country"] = "USA"
                country = "USA"

        # Expand common US state abbreviations (SERP snippets often use "San Francisco, CA")
        us_state_abbrev = {
            "CA": "California",
            "NY": "New York",
            "WA": "Washington",
            "MA": "Massachusetts",
            "IL": "Illinois",
            "TX": "Texas",
            "FL": "Florida",
            "CO": "Colorado",
            "GA": "Georgia",
            "AZ": "Arizona",
            "OR": "Oregon",
        }
        if country in ["USA", "US", "United States"] and state and len(state) == 2 and state.upper() in us_state_abbrev:
            loc["state"] = us_state_abbrev[state.upper()]
            state = loc["state"]

        # Infer state from US city when country indicates USA
        if city and not state and country in ["USA", "US", "United States"]:
            looked_up = us_city_to_state.get(city_low, "")
            if looked_up:
                loc["state"] = looked_up
        return loc

    # Location patterns (person-focused + generic)
    location_patterns = [
        r"based\s+in\s+([A-Z][a-zA-Z\s\.\-]+),\s*([A-Z][a-zA-Z\s\.\-]+),\s*([A-Z][a-zA-Z\s\.\-]+)",
        r"based\s+in\s+([A-Z][a-zA-Z\s\.\-]+),\s*([A-Z][a-zA-Z\s\.\-]+)",
        r"located\s+in\s+([A-Z][a-zA-Z\s\.\-]+),\s*([A-Z][a-zA-Z\s\.\-]+),\s*([A-Z][a-zA-Z\s\.\-]+)",
        r"located\s+in\s+([A-Z][a-zA-Z\s\.\-]+),\s*([A-Z][a-zA-Z\s\.\-]+)",
        r"([A-Z][a-zA-Z\s]+),\s*([A-Z]{2})\s*-based",
        r"([A-Z][a-zA-Z\s]+)\s*-based",
        r"([A-Z][a-zA-Z\s]+),\s*(USA|UK|United States|United Kingdom|California|Texas|New York|Florida|Illinois|Washington|Massachusetts)",
    ]

    def extract_location_from_text(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None

        # PATTERN 0: LinkedIn title format "Name - City, State, Country" (MOST RELIABLE)
        # Example: "Erica Bourne - London, England, United Kingdom"
        # Must have comma-separated location parts and reasonable length (< 80 chars)
        title_match = re.search(r'\s+-\s+([A-Z][a-zA-Z\s]+(?:,\s*[A-Z][a-zA-Z\s]+)+)\s*(?:$|\||Location)', text)
        if title_match:
            loc_part = title_match.group(1).strip()
            # Sanity check: location should be reasonably short
            if len(loc_part) < 80:
                parts = [p.strip() for p in loc_part.split(",") if p and p.strip()]
                if len(parts) >= 3:
                    return {"city": parts[0], "state": parts[1], "country": normalize_country(parts[2])}
                if len(parts) == 2:
                    second = parts[1]
                    if len(second.strip()) == 2 and second.strip().isalpha():
                        return {"city": parts[0], "state": second.strip().upper(), "country": "USA"}
                    return {"city": parts[0], "state": "", "country": normalize_country(second)}

        # PATTERN 1: LinkedIn SERP snippets "Location: X"
        # Example: "... Education: Duke University · Location: San Francisco · ..."
        m = re.search(r"\blocation\s*[:\-]\s*([^\n\r|•·]+)", text, re.IGNORECASE)
        if m:
            raw = (m.group(1) or "").strip()
            raw = re.split(r"\s*(?:\|\s*|•\s*|·\s*|-{1,2}\s*)", raw)[0].strip()
            raw = re.sub(r"\s+\d+\+.*$", "", raw).strip()
            if raw:
                parts = [p.strip() for p in raw.split(",") if p and p.strip()]
                if len(parts) >= 3:
                    return {"city": parts[0], "state": parts[1], "country": normalize_country(parts[2])}
                if len(parts) == 2:
                    second = parts[1]
                    if len(second.strip()) == 2 and second.strip().isalpha():
                        return {"city": parts[0], "state": second.strip().upper(), "country": "USA"}
                    return {"city": parts[0], "state": "", "country": normalize_country(second)}
                if len(parts) == 1:
                    return {"city": parts[0], "state": "", "country": ""}

        for pattern in location_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                parts = []
                if isinstance(match, tuple):
                    parts = [p.strip() for p in match if p and p.strip()]
                else:
                    parts = [match.strip()]

                if not parts:
                    continue

                city = ""
                state = ""
                country = ""

                if len(parts) >= 3:
                    city = parts[0]
                    state = parts[1]
                    country = normalize_country(parts[2])
                elif len(parts) == 2:
                    city = parts[0]
                    second = parts[1]
                    # US state abbreviation implies USA
                    if len(second.strip()) == 2 and second.strip().isalpha():
                        state = second.strip().upper()
                        country = "USA"
                    else:
                        country = normalize_country(second)
                elif len(parts) == 1:
                    city = parts[0]

                if city and len(city) > 2:
                    return {"city": city, "state": state, "country": country}

        return None

    serpapi_available = bool(SERPAPI_KEY)

    # Queries
    def _role_acronyms(pos: str) -> list:
        """
        Extract common C-level acronyms from a verbose title.
        Example: "Co-Founder and Chief Executive Officer" -> ["CEO"]
        """
        pos = (pos or "").strip()
        if not pos:
            return []
        acr = []
        # Pull each "Chief X Officer" segment and turn into C?O acronyms.
        for m in re.finditer(r"\bChief\s+([A-Za-z][A-Za-z\s]{0,40}?)\s+Officer\b", pos, re.I):
            mid = (m.group(1) or "").strip()
            parts = [p for p in re.split(r"[^A-Za-z]+", mid) if p]
            if parts:
                candidate = "C" + "".join(p[0].upper() for p in parts) + "O"
                # Normalize common ones (avoid weird long acronyms)
                if candidate in {"CEO", "CFO", "CTO", "COO", "CPO", "CMO", "CIO", "CRO", "CISO"}:
                    acr.append(candidate)
        # Also detect "VP"/"SVP"/"EVP" etc.
        for token in ["CEO", "CFO", "CTO", "COO", "CPO", "CMO", "CIO", "CRO", "CISO", "SVP", "EVP", "VP"]:
            if re.search(rf"\b{re.escape(token)}\b", pos, re.I):
                acr.append(token)
        # De-dupe
        out = []
        for a in acr:
            if a not in out:
                out.append(a)
        return out

    role_terms = []
    if position:
        role_terms.append(position)
        role_terms.extend(_role_acronyms(position))

    # ============================================================
    # STRATEGY: Use the exact formula that works in Google:
    #   "{Name}" {Company} {Role} location
    # NO site: constraint - let Google naturally return LinkedIn first.
    # Then extract "Location: ..." from the SEO snippet.
    # ============================================================
    queries = []
    
    # PRIMARY QUERIES: exact formula from user's example
    # "Jennifer Taylor" Plaid President location -> returns LinkedIn with "Location: San Francisco"
    if company_name and role_terms:
        for role in role_terms[:2]:
            queries.append(f'"{person_name}" {company_name} {role} location')
    
    if company_name:
        queries.append(f'"{person_name}" {company_name} location')
    
    if role_terms:
        for role in role_terms[:2]:
            queries.append(f'"{person_name}" {role} location')
    
    # FALLBACK QUERIES
    queries.append(f'"{person_name}" location')
    if linkedin_url:
        queries.append(f'{linkedin_url} location')

    # 1) SerpAPI (preferred, PARALLEL) - TRUST GOOGLE'S FIRST RESULT
    # The query "{Name}" {Company} {Role} location returns LinkedIn profile first
    # with "Location: San Francisco" in the SEO snippet. Just grab it.
    if serpapi_available:
        loc_queries = queries[:4]  # Use up to 4 queries
        loc_results = serpapi_parallel_search(loc_queries, SERPAPI_KEY, num_results=5)
        
        for r in loc_results:
            title = r.get("title", "") or ""
            snippet = r.get("snippet", "") or ""
            combined = f"{title} {snippet}"
            
            # Look for "Location:" pattern in the snippet (LinkedIn SEO format)
            loc = extract_location_from_text(combined)
            if loc and loc.get("city"):
                loc = finalize_location(loc)
                print(f"   📍 Person location for {person_name}: {loc}")
                return loc

    # 2) Startpage fallback (quick + rough, like HQ fallback)
    try:
        import urllib.parse
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        for q in queries[:3]:
            encoded_query = urllib.parse.quote_plus(q)
            search_url = f"https://www.startpage.com/sp/search?query={encoded_query}"
            resp = requests.get(search_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                loc = extract_location_from_text(resp.text)
                if loc and loc.get("city"):
                    loc = finalize_location(loc)
                    print(f"   📍 Startpage person location for {person_name}: {loc}")
                    return loc
    except Exception as e:
        print(f"⚠️ Person location Startpage error: {e}")

    return result


def get_raw_serpapi_results_for_person_location(person_name: str, company_name: str = "", position: str = "") -> dict:
    """
    Get raw SerpAPI organic_results for AI analysis of person's location.
    Returns the full organic_results array for the LLM to analyze.
    
    Args:
        person_name: The person's name
        company_name: Optional company name
        position: Optional position/title
        
    Returns:
        Dict with keys: query (str), organic_results (list of result dicts)
    """
    person_name = (person_name or "").strip()
    company_name = (company_name or "").strip()
    position = (position or "").strip()
    
    if not person_name or not SERPAPI_KEY:
        return {"query": "", "organic_results": []}
    
    # Build the search query - similar to how we'd search manually
    query_parts = [f'"{person_name}"']
    if company_name:
        query_parts.append(company_name)
    if position:
        # Clean position - extract key role if it's too long
        if len(position) > 30:
            # Try to extract C-level acronyms
            for role in ["CEO", "CFO", "CTO", "COO", "CPO", "CMO", "CIO", "CRO"]:
                if role.lower() in position.lower() or f"Chief {role[1:-1]}" in position:
                    query_parts.append(role)
                    break
            else:
                # Just take first few words
                query_parts.append(" ".join(position.split()[:3]))
        else:
            query_parts.append(position)
    query_parts.append("location")
    
    query = " ".join(query_parts)
    print(f"🔍 Raw SerpAPI search for location: {query}")
    
    try:
        params = {"q": query, "num": 10, "api_key": SERPAPI_KEY, "hl": "en", "gl": "us"}
        search = GoogleSearch(params)
        result = search.get_dict()
        
        if "error" in result:
            print(f"   ⚠️ SerpAPI error: {result['error']}")
            return {"query": query, "organic_results": []}
        
        organic_results = result.get("organic_results", [])
        
        # Simplify the results to just what AI needs: title, snippet, link
        simplified = []
        for r in organic_results[:10]:
            simplified.append({
                "position": r.get("position", 0),
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
                "link": r.get("link", ""),
                "source": r.get("source", "")
            })
        
        print(f"   ✅ Got {len(simplified)} organic results for AI analysis")
        return {"query": query, "organic_results": simplified}
        
    except Exception as e:
        print(f"   ❌ SerpAPI raw search error: {e}")
        return {"query": query, "organic_results": []}


def search_company_headquarters(company_name: str, website_url: str = "") -> dict:
    """
    Search for a company's headquarters location using SerpAPI or web search.
    Uses queries like: "{company name} headquarters location city country"
    
    Args:
        company_name: The company name to search for
        website_url: Optional company website to help narrow results
        
    Returns:
        Dict with keys: city, state, country (any may be empty string)
    """
    result = {"city": "", "state": "", "country": ""}
    
    if not company_name and not website_url:
        return result
    
    serpapi_available = bool(SERPAPI_KEY)
    
    # Clean company name
    clean_name = (company_name or "").strip()
    for suffix in [', Inc.', ', Inc', ' Inc.', ' Inc', ', LLC', ' LLC', ', Ltd.', ', Ltd', ' Ltd.', ' Ltd',
                   ', Corp.', ', Corp', ' Corp.', ' Corp', ', Limited', ' Limited', ', Co.', ' Co.']:
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)].strip()
    
    # Extract domain from website
    domain_hint = ""
    if website_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(website_url)
            domain = parsed.netloc.replace("www.", "")
            domain_hint = domain.split('.')[0] if '.' in domain else domain
        except:
            pass
    
    # Major US cities to state mapping (for automatic state lookup)
    us_city_to_state = {
        'san francisco': 'California', 'los angeles': 'California', 'san diego': 'California',
        'san jose': 'California', 'oakland': 'California', 'palo alto': 'California',
        'mountain view': 'California', 'menlo park': 'California', 'cupertino': 'California',
        'sunnyvale': 'California', 'santa clara': 'California', 'redwood city': 'California',
        'new york': 'New York', 'new york city': 'New York', 'nyc': 'New York', 'manhattan': 'New York',
        'brooklyn': 'New York', 'buffalo': 'New York',
        'seattle': 'Washington', 'bellevue': 'Washington', 'redmond': 'Washington',
        'boston': 'Massachusetts', 'cambridge': 'Massachusetts',
        'chicago': 'Illinois',
        'austin': 'Texas', 'dallas': 'Texas', 'houston': 'Texas', 'san antonio': 'Texas',
        'denver': 'Colorado', 'boulder': 'Colorado',
        'atlanta': 'Georgia',
        'miami': 'Florida', 'tampa': 'Florida', 'orlando': 'Florida',
        'phoenix': 'Arizona', 'scottsdale': 'Arizona',
        'portland': 'Oregon',
        'las vegas': 'Nevada',
        'salt lake city': 'Utah',
        'raleigh': 'North Carolina', 'charlotte': 'North Carolina', 'durham': 'North Carolina',
        'nashville': 'Tennessee',
        'detroit': 'Michigan', 'ann arbor': 'Michigan',
        'minneapolis': 'Minnesota',
        'philadelphia': 'Pennsylvania', 'pittsburgh': 'Pennsylvania',
        'washington': 'District of Columbia', 'washington dc': 'District of Columbia', 'washington d.c.': 'District of Columbia',
        'arlington': 'Virginia', 'mclean': 'Virginia', 'reston': 'Virginia',
        'baltimore': 'Maryland', 'bethesda': 'Maryland',
        'indianapolis': 'Indiana',
        'columbus': 'Ohio', 'cleveland': 'Ohio', 'cincinnati': 'Ohio',
        'kansas city': 'Missouri', 'st louis': 'Missouri', 'st. louis': 'Missouri',
        'omaha': 'Nebraska',
        'new orleans': 'Louisiana',
        'milwaukee': 'Wisconsin', 'madison': 'Wisconsin',
        'hartford': 'Connecticut', 'stamford': 'Connecticut',
        'providence': 'Rhode Island',
        'jersey city': 'New Jersey', 'newark': 'New Jersey', 'hoboken': 'New Jersey',
    }
    
    # Country name normalization
    country_map = {
        'united states': 'USA', 'united states of america': 'USA', 'us': 'USA', 'u.s.': 'USA', 'america': 'USA',
        'united kingdom': 'UK', 'great britain': 'UK', 'britain': 'UK', 'england': 'UK', 'scotland': 'UK', 'wales': 'UK',
        'united arab emirates': 'UAE', 'uae': 'UAE',
    }
    
    # US state abbreviations
    us_states = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY',
        'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND',
        'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
    }
    
    # US state full names to abbreviations
    us_state_names = {
        'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
        'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
        'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA',
        'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
        'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO',
        'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
        'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH',
        'oklahoma': 'OK', 'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
        'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
        'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY',
        'district of columbia': 'DC', 'washington dc': 'DC', 'washington d.c.': 'DC'
    }
    
    def normalize_country(c):
        c_lower = c.lower().strip()
        return country_map.get(c_lower, c.strip())
    
    def is_us_state(s):
        """Check if string is a US state (full name or abbreviation)."""
        s_upper = s.upper().strip()
        s_lower = s.lower().strip()
        return s_upper in us_states or s_lower in us_state_names
    
    def get_state_abbrev(s):
        """Convert state name to abbreviation."""
        s_upper = s.upper().strip()
        s_lower = s.lower().strip()
        if s_upper in us_states:
            return s_upper
        return us_state_names.get(s_lower, s.strip())
    
    def lookup_state_for_city(city, country):
        """Look up state for a US city if country is USA."""
        if country.upper() not in ['USA', 'US', 'UNITED STATES', 'AMERICA']:
            return ""
        city_lower = city.lower().strip()
        if city_lower in us_city_to_state:
            return us_city_to_state[city_lower]
        return ""
    
    def finalize_location(loc):
        """Ensure state is populated if we have city + USA."""
        if not loc:
            return loc
        city = loc.get("city", "")
        state = loc.get("state", "")
        country = loc.get("country", "")
        
        # If we have city + USA but no state, look it up
        if city and not state and country in ['USA', 'US', 'United States']:
            looked_up = lookup_state_for_city(city, country)
            if looked_up:
                loc["state"] = looked_up
                print(f"   📍 Looked up state for {city}: {looked_up}")
        
        return loc
    
    # Common location patterns to extract
    location_patterns = [
        # "headquartered in City, State, Country"
        r'headquartered\s+in\s+([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+)',
        # "headquartered in City, Country" or "headquartered in City, State"
        r'headquartered\s+in\s+([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+)',
        # "based in City, State, Country"
        r'based\s+in\s+([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+)',
        # "based in City, Country"
        r'based\s+in\s+([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+)',
        # "headquarters in City, State"
        r'headquarters?\s+in\s+([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+)',
        # "City, State-based" or "City-based"
        r'([A-Z][a-zA-Z\s\.]+),\s*([A-Z]{2})\s*-based',
        # "located in City, Country"
        r'located\s+in\s+([A-Z][a-zA-Z\s\.]+),\s*([A-Z][a-zA-Z\s\.]+)',
        # Simple "City, USA" or "City, California" patterns
        r'([A-Z][a-zA-Z\s]+),\s*(USA|UK|United States|California|Texas|New York|Florida|Illinois|Washington|Massachusetts)',
    ]
    
    def extract_location_from_text(text):
        """Try to extract city, state, country from text snippets."""
        for pattern in location_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    parts = [p.strip() for p in match if p and p.strip()]
                else:
                    parts = [match.strip()]
                
                if not parts:
                    continue
                
                city = ""
                state = ""
                country = ""
                
                if len(parts) >= 3:
                    city = parts[0]
                    state = parts[1]
                    country = normalize_country(parts[2])
                elif len(parts) == 2:
                    city = parts[0]
                    # Check if second part is a US state (full name or abbreviation)
                    if is_us_state(parts[1]):
                        state = get_state_abbrev(parts[1])
                        country = "USA"
                    else:
                        country = normalize_country(parts[1])
                elif len(parts) == 1:
                    city = parts[0]
                
                if city and len(city) > 2:
                    return {"city": city, "state": state, "country": country}
        
        return None
    
    # Try SerpAPI first (PARALLEL)
    if serpapi_available:
        hq_queries = [
            f'"{clean_name}" headquarters location',
            f'"{clean_name}" head office city',
            f'{clean_name} company headquarters',
        ]
        print(f"   → Running {len(hq_queries)} HQ queries in PARALLEL...")
        hq_results = serpapi_parallel_search(hq_queries, SERPAPI_KEY, num_results=10)
        
        for r in hq_results:
            snippet = r.get("snippet", "")
            title = r.get("title", "")
            combined = f"{title} {snippet}"
            
            location = extract_location_from_text(combined)
            if location and location.get("city"):
                location = finalize_location(location)
                print(f"   📍 Found HQ for {company_name}: {location}")
                return location
    
    # Fallback: Startpage search with multiple queries
    fallback_queries = [
        f"{clean_name} headquarters",
        f"{clean_name} head office location",
        f'"{clean_name}" company location city',
    ]
    if domain_hint:
        fallback_queries.insert(0, f"{domain_hint} company headquarters location")
    
    for fallback_query in fallback_queries[:2]:
        try:
            import urllib.parse
            
            encoded_query = urllib.parse.quote_plus(fallback_query)
            search_url = f"https://www.startpage.com/sp/search?query={encoded_query}"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                location = extract_location_from_text(response.text)
                if location and location.get("city"):
                    location = finalize_location(location)
                    print(f"   📍 Startpage HQ for {company_name}: {location}")
                    return location
                        
        except Exception as e:
            print(f"   ⚠️ Startpage HQ search error: {e}")
            continue
    
    print(f"   ⚠️ No headquarters found for: {company_name}")
    return result


def get_top_management(company_name, text=""):
    """
    Robustly extracts top management (CEO, CFO, etc.) from Wikipedia, LinkedIn, Crunchbase, or AI models.
    Now includes LinkedIn URLs, location, and bio for each executive.
    Returns:
        (list, str): (structured_list, formatted_text)
    """
    print(f"🔍 Fetching top management for: {company_name}")
    management_results = []
    formatted_text = ""

    # =====================================================
    # 1️⃣ Gather Context from Multiple Sources
    # =====================================================
    if not text.strip():
        text = get_wikipedia_summary(company_name)

    # Search for company leadership info (PARALLEL)
    context_queries = [
        f'"{company_name}" leadership team CEO CFO executives',
        f'"{company_name}" CFO COO CTO CMO CISO chief officer',
        f'{company_name} CEO "chief executive" OR CFO OR CTO',
    ]
    
    serpapi_key = os.getenv("SERPAPI_KEY")
    if serpapi_key:
        print(f"   → Running {len(context_queries)} management queries in PARALLEL...")
        mgmt_search_results = serpapi_parallel_search(context_queries, serpapi_key, num_results=10)
        for r in mgmt_search_results:
            snippet = r.get("snippet", "")
            if snippet:
                text += "\n\n" + snippet
        print(f"   ✅ Management parallel search: {len(mgmt_search_results)} results")

    # =====================================================
    # 2️⃣ AI Extraction - Get Names First
    # =====================================================
    prompt = f"""
You are a corporate research analyst. Extract ONLY **strict C-suite** executives currently at "{company_name}".

ALLOWED ROLES (and titles that **start with "Chief"** mapping to these): CEO, CFO, COO, CTO, CRO, CISO, CPO (Chief Product Officer), CMO, CLO (Chief Legal Officer).
❌ EXCLUDE everyone else: board members only, regional heads, EVPs, VPs, Partners, MDs (unless also Chief…), presidents without Chief title, general counsel without "Chief Legal Officer", etc.

RULES:
- **position** is REQUIRED for every person. Use the exact published title (e.g. "Chief Financial Officer", "CEO").
- If you cannot confidently assign an allowed C-suite title from reliable sources, **omit that person entirely** (do not guess).

For EACH executive, provide:
- name: Full legal name
- position: Official C-suite title (REQUIRED — see allowed list above)
  - status: "Current" or "Past"
- location: City, State/Country (if known, else "")
- current_employee_url: A DEDICATED page URL on the company's website for THIS SPECIFIC INDIVIDUAL. 
  VALID examples (person's name in the URL):
    - https://www.evercore.com/team/ed-banks/
    - https://www.vsacapital.com/vsa-capital-team/andrew-raca
    - https://company.com/people/john-smith
    - https://company.com/leadership/jane-doe
  INVALID (DO NOT USE - these are generic pages):
    - https://company.com/about
    - https://company.com/about-us  
    - https://company.com/team (without person name)
    - https://company.com/leadership (without person name)
    - https://company.com/ (homepage)
  If no dedicated individual page exists, use empty string "".
- bio: Professional executive summary (3-5 sentences) with SPECIFIC, VERIFIABLE details:

EXAMPLE BIO STYLE:
"Experienced CEO and Board Director with successful track record in Technology and Financial Services. 20+ years in decision making roles for Private Equity and Corporates. Skilled in General Management, Transformational Leadership, Strategy Development and M&A. International profile having operated out of US, UK and EMEA. MBA from Harvard Business School and BSc in Computer Science from MIT."

BIO REQUIREMENTS:
- Write in third person, professional tone
- Start with current/past leadership roles and experience
- Include industry expertise and sectors
- Mention key skills and competencies
- Note international experience if applicable
- End with SPECIFIC education: name ACTUAL universities and degree types (e.g., "MBA from DePaul University and BSc in Computer Systems from University of Nevada")

CRITICAL BIO RULES - DO NOT USE:
- NEVER write "holds a degree from a leading university" - name the ACTUAL university
- NEVER write "graduated from a top institution" - be specific
- NEVER write "academic background in the field" - list actual degrees
- NEVER write "earned credentials from a prestigious school" - name it
- NEVER use bracketed references like (1), (2), [4], [5] 
- If you don't know the actual university, OMIT education entirely rather than being vague
- NO bullet points, write as flowing paragraph

CONTEXT ABOUT {company_name}:
{text[:8000]}

CRITICAL: The CEO is the most important. Make sure to find and include the CEO.
If the context doesn't mention executives, use your knowledge to identify them.

Return ONLY valid JSON array (no markdown, no explanation):
[
  {{
    "name": "John Smith",
    "position": "Chief Executive Officer",
    "status": "Current",
    "location": "San Francisco, CA",
    "current_employee_url": "https://company.com/team/john-smith",
    "bio": "Experienced CEO and Operating Partner with successful track record in Technology and SaaS. 25+ years in decision making roles for Private Equity and Fortune 500 companies. Skilled in General Management, Digital Transformation, Strategy Development and M&A. International profile having operated across US, Europe and Asia Pacific. MBA from Stanford Graduate School of Business."
  }}
]

JSON:"""

    ai_response = openrouter_chat("perplexity/sonar-pro", prompt, f"TopManagement-{company_name}")

    # Try to extract JSON
    try:
        match = re.search(r"\[.*\]", ai_response, re.S)
        if match:
            management_results = json.loads(match.group(0))
            print(f"✅ Sonar extracted {len(management_results)} executives")
    except Exception as e:
        print(f"⚠️ Sonar JSON parse failed: {e}")
        management_results = []

    # =====================================================
    # 3️⃣ Claude Fallback
    # =====================================================
    if not management_results:
        fallback_prompt = f"""
List ONLY **strict C-suite** executives currently at {company_name}: CEO, CFO, COO, CTO, CRO, CISO, CPO, CMO, CLO — or other titles that **start with "Chief"** for those functions.
Exclude board-only, regional heads, VPs, partners, etc. **position is REQUIRED**; if unsure of an allowed C-suite title, omit the person.

For each person provide:
- name: Full name
- position: Official C-suite title (required)
- status: "Current"
- location: Where they are based
- current_employee_url: ONLY a dedicated individual page URL (e.g., /team/john-smith, /people/jane-doe). Use "" if no dedicated page exists. Do NOT use generic pages like /about, /team, /leadership, or homepage.
- bio: Brief professional background (1-2 sentences)

Context: {text[:5000]}

Return JSON array only:
[{{"name": "...", "position": "...", "status": "Current", "location": "...", "current_employee_url": "...", "bio": "..."}}]
"""
        fallback_resp = openrouter_chat("anthropic/claude-3.5-sonnet", fallback_prompt, f"FallbackMgmt-{company_name}")
        try:
            match = re.search(r"\[.*\]", fallback_resp, re.S)
            if match:
                management_results = json.loads(match.group(0))
                print(f"✅ Claude fallback found {len(management_results)} executives")
        except Exception as e:
            print(f"⚠️ Claude fallback parse failed: {e}")

    raw_mgmt_ct = len(management_results)
    management_results = [
        m
        for m in management_results
        if isinstance(m, dict)
        and (m.get("position") or m.get("title") or "").strip()
        and title_is_strict_csuite((m.get("position") or m.get("title") or "").strip())
    ]
    if raw_mgmt_ct != len(management_results):
        print(f"   → C-suite filter: kept {len(management_results)} / {raw_mgmt_ct} executives (strict Chief / acronym list)")

    # =====================================================
    # 4️⃣ Search LinkedIn for EACH Executive in parallel
    # =====================================================
    print(f"🔗 Searching LinkedIn profiles for {len(management_results)} executives (parallel)...")

    def _find_exec_linkedin(m):
        name = m.get("name", "")
        position = (m.get("position") or m.get("title") or "").strip()
        if not name or not position or not title_is_strict_csuite(position):
            return
        linkedin_url = search_linkedin_profile(name, company_name, position)
        if linkedin_url:
            m["linkedin_url"] = linkedin_url
            print(f"   ✅ Found LinkedIn for {name}")
        else:
            m["linkedin_url"] = ""
            print(f"   ⚠️ No LinkedIn found for {name}")

    with ThreadPoolExecutor(max_workers=min(len(management_results) or 1, 5)) as exec_li_ex:
        list(exec_li_ex.map(_find_exec_linkedin, management_results))

    # =====================================================
    # 5️⃣ Clean Bios - Remove Citations & Generic Text
    # =====================================================
    for m in management_results:
        bio = m.get("bio", "")
        if bio:
            # Remove bracketed citation references like (1), (2), [4], [5]
            bio = re.sub(r'\s*\(\d+\)', '', bio)
            bio = re.sub(r'\s*\[\d+\]', '', bio)
            bio = re.sub(r'\s*\(\d+,\s*\d+\)', '', bio)  # (1, 2)
            bio = re.sub(r'\s*\[\d+,\s*\d+\]', '', bio)  # [1, 2]
            
            # Remove generic educational phrases
            generic_patterns = [
                r'Holds?\s+a\s+(bachelor\'?s?|master\'?s?|doctorate|PhD|degree)\s+from\s+a\s+(leading|top|prestigious|renowned)\s+(U\.S\.|US|university|institution|school)[^.]*\.',
                r'Earned\s+a\s+(bachelor\'?s?|master\'?s?|doctorate|PhD|degree)\s+from\s+a\s+(leading|top|prestigious|renowned)\s+(U\.S\.|US|university|institution|school)[^.]*\.',
                r'Graduated\s+from\s+a\s+(leading|top|prestigious|renowned)\s+(U\.S\.|US|university|institution|school)[^.]*\.',
                r'Has\s+an?\s+academic\s+background\s+in\s+(the\s+)?field[^.]*\.',
                r'Earned\s+credentials?\s+from\s+a\s+(prestigious|top|leading)\s+(school|institution)[^.]*\.',
            ]
            for pattern in generic_patterns:
                bio = re.sub(pattern, '', bio, flags=re.IGNORECASE)
            
            # Clean up whitespace and double periods
            bio = re.sub(r'\s{2,}', ' ', bio).strip()
            bio = re.sub(r'\.{2,}', '.', bio)
            m["bio"] = bio
    
    # =====================================================
    # 6️⃣ Clean & Deduplicate URLs
    # =====================================================
    
    def is_valid_individual_page_url(url: str, person_name: str) -> bool:
        """
        Check if URL is a dedicated individual page (not a generic page).
        Valid: URLs that contain the person's name or a slug derived from it.
        Invalid: Generic pages like /about, /team, /leadership, homepage.
        """
        if not url or not url.startswith("http"):
            return False
        
        url_lower = url.lower()
        
        # Extract URL path (remove domain)
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path = parsed.path.lower().rstrip('/')
        except:
            path = url_lower.split('/')[-1] if '/' in url_lower else ''
        
        # Generic page patterns to reject
        generic_patterns = [
            '/about', '/about-us', '/aboutus', '/about_us',
            '/team', '/our-team', '/the-team', '/teams',
            '/leadership', '/leadership-team', '/leaders',
            '/management', '/management-team', '/executive-team',
            '/executives', '/board', '/board-of-directors',
            '/people', '/staff', '/company', '/corporate',
        ]
        
        # Check if path is ONLY a generic pattern (no individual identifier after)
        path_parts = path.split('/')
        # Filter empty parts
        path_parts = [p for p in path_parts if p]
        
        if not path_parts:
            # Homepage
            return False
        
        # If URL ends with just a generic term, reject it
        # e.g., /about, /team, /leadership (without individual name)
        if len(path_parts) == 1 and any(path_parts[0] == g.strip('/') for g in generic_patterns):
            return False
        
        # If last path part is generic (without specific person), reject
        last_part = path_parts[-1] if path_parts else ''
        if last_part in ['about', 'about-us', 'team', 'our-team', 'leadership', 
                         'management', 'people', 'staff', 'executives', 'board']:
            return False
        
        # Check if person name (or slug) appears in URL
        # Create name variants for matching
        name_parts = person_name.lower().split()
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[-1] if len(name_parts) > 1 else ''
        name_slug = '-'.join(name_parts)  # john-smith
        name_slug_underscore = '_'.join(name_parts)  # john_smith
        
        # Check if any name variant appears in URL
        if first_name and last_name:
            if last_name in url_lower or name_slug in url_lower or name_slug_underscore in url_lower:
                return True
            # Also check for variations like first-last or last-first
            if f"{first_name}-{last_name}" in url_lower or f"{last_name}-{first_name}" in url_lower:
                return True
        
        # If URL has more than 2 path parts and last part looks like a name slug (contains dash)
        # e.g., /team/john-smith or /people/jane-doe
        if len(path_parts) >= 2 and '-' in last_part and len(last_part) > 3:
            return True
        
        # Default: reject if we can't confirm it's individual-specific
        return False
    
    clean_data = []
    seen = set()
    for m in management_results:
        name = m.get("name", "").strip()
        position = (m.get("position") or m.get("title") or "").strip()
        status = m.get("status", "Current").capitalize()
        linkedin_url = m.get("linkedin_url", "").strip()
        location = m.get("location", "").strip()
        bio = m.get("bio", "").strip()
        current_employee_url = m.get("current_employee_url", "").strip()

        if not name or not position or not title_is_strict_csuite(position):
            continue
        
        # Validate that current_employee_url is a dedicated individual page
        if current_employee_url and not is_valid_individual_page_url(current_employee_url, name):
            print(f"   ⚠️ Rejecting generic URL for {name}: {current_employee_url}")
            current_employee_url = ""
        
        key = (name.lower(), position.lower())
        if key not in seen:
            seen.add(key)
            clean_data.append({
                "name": name,
                "position": position,
                "status": status,
                "linkedin_url": linkedin_url if linkedin_url.startswith("http") else "",
                "current_employee_url": current_employee_url if current_employee_url.startswith("http") else "",
                "location": location,
                "bio": bio
            })

    if clean_data:
        formatted_text = "; ".join([
            f"{m['name']} — {m['position']} ({m['status']})" + 
            (f" [{m['location']}]" if m.get('location') else "")
            for m in clean_data
        ])
        print(f"✅ Found {len(clean_data)} management entries with enhanced data for {company_name}")
    else:
        formatted_text = "⚠️ No top management found for this company."
        print("⚠️ No valid management found.")

    return clean_data, formatted_text

# ============================================
# Counterparty Individual Enrichment
# ============================================

def enrich_counterparties_with_individuals(events: list, main_company: str) -> list:
    """
    Second-pass enrichment: batched Perplexity calls + deduped SerpAPI for company website / company LinkedIn.

    Individual profile LinkedIn is skipped during /analyze by default (use UI 🔎 Find on event cards).
    Set ENRICH_INDIVIDUAL_LINKEDIN_IN_ANALYZE=1 to restore bulk Serp calls here.

    Env:
      COUNTERPARTY_ENRICH_BATCH_SIZE — deals per Perplexity call (default 5)
      COUNTERPARTY_ENRICH_PARALLEL_BATCHES — concurrent batch requests (default 3)
    """
    import os
    import json
    import requests

    OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPEN_ROUTER_KEY")
    if not OPENROUTER_KEY:
        print("   ⚠️ No OpenRouter key for individual enrichment")
        return events

    _ = main_company

    try:
        batch_sz = int(os.getenv("COUNTERPARTY_ENRICH_BATCH_SIZE", "5"))
    except ValueError:
        batch_sz = 5
    batch_sz = max(1, min(batch_sz, 8))

    try:
        batch_parallel = int(os.getenv("COUNTERPARTY_ENRICH_PARALLEL_BATCHES", "3"))
    except ValueError:
        batch_parallel = 3
    batch_parallel = max(1, min(batch_parallel, 6))

    do_bulk_ind_li = os.getenv("ENRICH_INDIVIDUAL_LINKEDIN_IN_ANALYZE", "").strip().lower() in ("1", "true", "yes")
    if not do_bulk_ind_li:
        print(
            "   → Counterparty individual LinkedIn: deferred (use 🔎 Find in UI per person). "
            "Set ENRICH_INDIVIDUAL_LINKEDIN_IN_ANALYZE=1 for bulk Serp during /analyze.",
            flush=True,
        )

    def _merge_enrichment_list_into_counterparties(counterparties: list, enrichment_data) -> None:
        if not enrichment_data or not isinstance(enrichment_data, list):
            return
        print(f"         → Merging {len(enrichment_data)} counterparty blocks from model", flush=True)
        for enrich_cp in enrichment_data:
            enrich_company = (enrich_cp.get("company") or "").lower()
            enrich_url = enrich_cp.get("press_release_url", "")
            enrich_linkedin = enrich_cp.get("company_linkedin_url", "")
            enrich_individuals = enrich_cp.get("individuals", [])
            print(f"         → {enrich_company}: {len(enrich_individuals)} individuals (model)", flush=True)

            for cp in counterparties:
                cp_name = cp.get("company_name", "").lower()
                if enrich_company in cp_name or cp_name in enrich_company or any(
                    word in cp_name for word in enrich_company.split() if len(word) > 3
                ):
                    if enrich_url and not cp.get("press_release_url"):
                        cp["press_release_url"] = enrich_url

                    if enrich_linkedin and not cp.get("company_linkedin_url"):
                        cp["company_linkedin_url"] = enrich_linkedin

                    if "individuals" not in cp:
                        cp["individuals"] = []

                    existing_names = [i.get("name", "").lower() for i in cp["individuals"]]
                    for ind in enrich_individuals:
                        ind_name = ind.get("name", "")
                        ind_title = (ind.get("title") or "").strip()
                        if not ind_name or not ind_title or not title_is_strict_csuite(ind_title):
                            continue
                        if ind_name and ind_name.lower() not in existing_names:
                            cp["individuals"].append(
                                {
                                    "name": ind_name,
                                    "title": ind_title,
                                    "linkedin_url": ind.get("linkedin_url", ""),
                                }
                            )
                            existing_names.append(ind_name.lower())
                    break

    rules_block = """Find for EACH legal entity / counterparty named under that deal:

1. **C-SUITE INDIVIDUALS** for that company:
   - PREFERRED: C-suite executives (CEO, CFO, COO, CTO, CRO, CISO, CPO, CMO, CLO, or titles starting with Chief) who are explicitly quoted or named in the deal announcement for that party.
   - ALSO ACCEPTABLE: The known CEO or top executive of the target/acquirer company at the approximate time of the deal, even if not explicitly quoted.
   - For INVESTOR counterparties (VC firms, PE funds): include the General Partner, Managing Partner, or Partner who led this investment if known.
   **Hard cap: at most 2 individuals per company.**
   **title is REQUIRED** per person; use their actual title (e.g. "General Partner", "CEO", "Chief Financial Officer").

2. **ANNOUNCEMENT URL** — that company's own press release for this deal, if known.

Return ONLY valid JSON, no markdown. Shape:
{
  "deals": [
    {
      "batch_event_index": 0,
      "counterparties": [
        {
          "company": "Company Name",
          "press_release_url": "",
          "company_linkedin_url": "",
          "individuals": [{"name": "", "title": "", "linkedin_url": ""}]
        }
      ]
    }
  ]
}

batch_event_index MUST match the deal section index (0 .. N-1). Include every deal section below."""

    def _parse_json_with_deals(raw: str):
        start_obj = raw.find("{")
        end_obj = raw.rfind("}") + 1
        if start_obj == -1 or end_obj <= start_obj:
            return None
        try:
            return json.loads(raw[start_obj:end_obj])
        except Exception:
            return None

    def _enrich_batch(chunk: list) -> None:
        """chunk: [(global_event_idx, event), ...] — each event must have ≥1 named counterparty."""
        deal_sections = []
        chunk_events_ordered: list = []
        bi = 0
        for _gi, ev in chunk:
            event_short = ev.get("Event (short)", "")
            announcement_date = ev.get("Announcement Date", "")
            closed_date = ev.get("Closed Date", "")
            counterparties = ev.get("counterparties", [])
            cp_names = [cp.get("company_name", "") for cp in counterparties if cp.get("company_name")]
            if not cp_names:
                continue
            date_context = ""
            if announcement_date:
                date_context += f"Announced: {announcement_date}"
            if closed_date:
                date_context += f", Closed: {closed_date}" if date_context else f"Closed: {closed_date}"
            if not date_context:
                date_context = "Date unknown"
            deal_sections.append(
                f"### Deal batch_event_index={bi}\n"
                f'Short description: "{event_short}"\n'
                f"Dates: {date_context}\n"
                f"Companies involved: {', '.join(cp_names)}\n"
            )
            chunk_events_ordered.append(ev)
            bi += 1
        if not deal_sections:
            return

        query = (
            f"You will enrich {len(deal_sections)} distinct corporate deals in ONE response.\n"
            f"{rules_block}\n\n---\n" + "\n".join(deal_sections)
        )

        mtok = min(16000, 800 + 2200 * len(deal_sections))
        print(f"      📦 Perplexity batch: {len(deal_sections)} deals in 1 call (max_tokens={mtok})…", flush=True)
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "perplexity/sonar-pro",
                    "messages": [{"role": "user", "content": query}],
                    "temperature": 0.1,
                    "max_tokens": mtok,
                },
                timeout=90,
            )
            if response.status_code != 200:
                print(f"      ⚠️ Batch enrichment HTTP {response.status_code}", flush=True)
                return
            raw = response.json()["choices"][0]["message"]["content"].strip()
            data = _parse_json_with_deals(raw)
            if not data:
                print("      ⚠️ Batch enrichment JSON parse failed", flush=True)
                return
            deals = data.get("deals")
            if not isinstance(deals, list):
                print("      ⚠️ Batch enrichment missing deals[]", flush=True)
                return
            by_bi: Dict[int, list] = {}
            for d in deals:
                if not isinstance(d, dict):
                    continue
                try:
                    bidx = int(d.get("batch_event_index", -1))
                except (TypeError, ValueError):
                    continue
                if bidx < 0:
                    continue
                by_bi[bidx] = d.get("counterparties") or []

            n_ev = len(chunk_events_ordered)

            # Model sometimes uses 1-based batch_event_index (1..N with no 0)
            if by_bi and 0 not in by_bi:
                ks = sorted(by_bi.keys())
                if ks == list(range(1, n_ev + 1)):
                    by_bi = {k - 1: v for k, v in by_bi.items()}

            def _cps_from_deal_elem(elem):
                if isinstance(elem, list):
                    return elem
                if isinstance(elem, dict):
                    return elem.get("counterparties") or []
                return []

            # Positional fallback when batch_event_index is missing or wrong for some rows.
            for bidx in range(n_ev):
                if bidx not in by_bi and bidx < len(deals):
                    cps = _cps_from_deal_elem(deals[bidx])
                    if cps:
                        by_bi[bidx] = cps
                        print(f"      ℹ️ Batch: used positional fallback for index={bidx}", flush=True)

            for bidx, ev in enumerate(chunk_events_ordered):
                cp_list = by_bi.get(bidx)
                if not cp_list:
                    print(f"      ⚠️ Batch: no counterparties for batch_event_index={bidx}", flush=True)
                    continue
                _merge_enrichment_list_into_counterparties(ev.get("counterparties") or [], cp_list)
        except Exception as e:
            print(f"      ⚠️ Batch enrichment error: {e}", flush=True)

    indexed = [
        (i, ev)
        for i, ev in enumerate(events)
        if ev.get("counterparties")
        and any((cp.get("company_name") or "").strip() for cp in (ev.get("counterparties") or []))
    ]
    batches = [indexed[j : j + batch_sz] for j in range(0, len(indexed), batch_sz)]
    print(
        f"\n   🤝 Counterparty enrichment: {len(indexed)} events with named counterparties → "
        f"{len(batches)} batched Perplexity call(s) (batch_size≤{batch_sz}, parallel≤{batch_parallel})",
        flush=True,
    )

    if batches:
        with ThreadPoolExecutor(max_workers=batch_parallel) as executor:
            list(executor.map(_enrich_batch, batches))

    print("\n🔍 Resolving counterparty websites & company LinkedIn (deduped by company name)…", flush=True)

    all_cp_pairs = [
        (event, cp)
        for event in events
        for cp in event.get("counterparties", [])
        if cp.get("company_name")
    ]

    def _cp_key(cp: dict) -> str:
        return (cp.get("company_name") or "").strip().lower()

    leaders: Dict[str, dict] = {}
    followers: Dict[str, list] = {}
    for _event, cp in all_cp_pairs:
        k = _cp_key(cp)
        if not k:
            continue
        if k not in leaders:
            leaders[k] = cp
            followers[k] = []
        elif cp is not leaders[k]:
            followers[k].append(cp)

    def _propagate_url_fields(lead: dict, follower: dict) -> None:
        for fld in ("company_website", "company_url", "website", "company_linkedin_url"):
            v = lead.get(fld)
            if v and not follower.get(fld):
                follower[fld] = v

    def _resolve_company_site_and_li(cp: dict) -> None:
        cp_name = cp.get("company_name", "")
        cp_website = cp.get("company_website", "") or cp.get("company_url", "") or cp.get("website", "")
        existing_linkedin = cp.get("company_linkedin_url", "")

        if not cp_website:
            print(f"   → Searching website for: {cp_name}…", flush=True)
            found_website = search_company_website(cp_name)
            if found_website:
                cp["company_website"] = found_website
                cp["company_url"] = found_website
                cp["website"] = found_website
                cp_website = found_website
            else:
                print(f"   ⚠️ No website found for {cp_name}", flush=True)

        needs_linkedin_search = False
        if not existing_linkedin:
            needs_linkedin_search = True
        elif existing_linkedin:
            slug = (
                existing_linkedin.split("/company/")[-1].rstrip("/").lower()
                if "/company/" in existing_linkedin
                else ""
            )
            name_slug = cp_name.lower().replace(" ", "-").replace(",", "").replace(".", "").replace("'", "")
            if slug and (slug == name_slug or slug.replace("-", "") == name_slug.replace("-", "")):
                needs_linkedin_search = True

        if needs_linkedin_search:
            print(f"   → Searching company LinkedIn for: {cp_name}…", flush=True)
            real_linkedin = search_company_linkedin(cp_name, cp_website)
            if real_linkedin:
                if existing_linkedin and existing_linkedin != real_linkedin:
                    print(f"   ✅ Corrected LinkedIn: {existing_linkedin} → {real_linkedin}", flush=True)
                else:
                    print(f"   ✅ Found LinkedIn: {real_linkedin}", flush=True)
                cp["company_linkedin_url"] = real_linkedin
            elif not existing_linkedin:
                print(f"   ⚠️ No LinkedIn found for {cp_name}", flush=True)

    print(f"   → {len(all_cp_pairs)} counterparty rows, {len(leaders)} unique company names", flush=True)

    leader_items = list(leaders.items())

    def _run_leader(item):
        _k, cp = item
        _resolve_company_site_and_li(cp)

    with ThreadPoolExecutor(max_workers=8) as cp_ex:
        list(cp_ex.map(_run_leader, leader_items))

    for k, lead in leaders.items():
        for fo in followers[k]:
            _propagate_url_fields(lead, fo)

    if do_bulk_ind_li:

        def _enrich_individuals_for_cp(event_cp_tuple):
            _event, cp = event_cp_tuple
            cp_name = cp.get("company_name", "")
            individuals = [
                ind
                for ind in (cp.get("individuals") or [])
                if isinstance(ind, dict)
                and (ind.get("name") or "").strip()
                and (ind.get("title") or "").strip()
                and title_is_strict_csuite((ind.get("title") or "").strip())
            ]
            cp["individuals"] = individuals
            if not individuals:
                return

            print(
                f"   👤 Searching LinkedIn for {len(individuals)} individuals at {cp_name} (parallel)…",
                flush=True,
            )

            def _find_ind_linkedin(ind):
                ind_name = ind.get("name", "")
                ind_title = (ind.get("title") or "").strip()
                existing_ind_linkedin = ind.get("linkedin_url", "")
                if not ind_name:
                    return
                if not ind_title or not title_is_strict_csuite(ind_title):
                    return
                if existing_ind_linkedin and "linkedin.com/in/" in existing_ind_linkedin:
                    return
                person_linkedin = search_linkedin_profile(ind_name, cp_name, ind_title)
                if person_linkedin:
                    ind["linkedin_url"] = person_linkedin
                    print(f"      ✅ Found LinkedIn for {ind_name}: {person_linkedin}", flush=True)
                else:
                    print(f"      ⚠️ No LinkedIn found for {ind_name}", flush=True)

            with ThreadPoolExecutor(max_workers=min(len(individuals), 5)) as ind_ex:
                list(ind_ex.map(_find_ind_linkedin, individuals))

        with ThreadPoolExecutor(max_workers=8) as ind_ex:
            list(ind_ex.map(_enrich_individuals_for_cp, all_cp_pairs))
    else:

        def _strip_and_filter_individuals_only(event_cp_tuple):
            _event, cp = event_cp_tuple
            individuals = [
                ind
                for ind in (cp.get("individuals") or [])
                if isinstance(ind, dict)
                and (ind.get("name") or "").strip()
                and (ind.get("title") or "").strip()
                and title_is_strict_csuite((ind.get("title") or "").strip())
            ]
            cp["individuals"] = individuals

        list(map(_strip_and_filter_individuals_only, all_cp_pairs))

    return events


# ============================================
# FILE 1: generate_events.py (or your generator file)
# ============================================

def detect_company_type(company_name: str, serpapi_key: str) -> dict:
    """
    Detects if a company is a startup/small company vs established enterprise.
    Returns company type info to guide search strategy.
    """
    from serpapi import GoogleSearch
    import re
    
    print(f"   🔍 Detecting company type for: {company_name}")
    
    result = {
        "is_startup": False,
        "is_small_company": False,
        "founded_year": None,
        "estimated_size": "unknown",
        "company_type": "enterprise",  # default
        "confidence": 0.0
    }
    
    try:
        # Quick search to understand company profile (PARALLEL)
        queries = [
            f'"{company_name}" founded startup',
            f'"{company_name}" series funding OR seed round OR accelerator',
            f'"{company_name}" site:crunchbase.com OR site:linkedin.com/company'
        ]
        
        print(f"   → Running {len(queries)} company type queries in PARALLEL...")
        parallel_results = serpapi_parallel_search(queries, serpapi_key, num_results=5)
        all_snippets = " ".join([f"{r.get('title', '')} {r.get('snippet', '')}" for r in parallel_results])
        print(f"   ✅ Company type search: {len(parallel_results)} results")
        
        all_snippets_lower = all_snippets.lower()
        
        # Check for startup indicators
        startup_signals = [
            "startup", "founded in 20", "seed round", "series a", "series b",
            "accelerator", "incubator", "early-stage", "venture-backed",
            "pre-seed", "angel investment", "bootstrap", "climate tech startup",
            "fintech startup", "healthtech", "saas startup", "founded 2018",
            "founded 2019", "founded 2020", "founded 2021", "founded 2022",
            "founded 2023", "founded 2024", "founded 2025", "young company",
            "emerging company", "growth-stage", "scale-up"
        ]
        
        # Check for enterprise indicators
        enterprise_signals = [
            "fortune 500", "nasdaq:", "nyse:", "publicly traded", "billion revenue",
            "global leader", "multinational", "established in 19", "founded 19",
            "100+ years", "50,000 employees", "10,000 employees", "headquarters",
            "s&p 500", "dow jones", "ftse 100", "dax", "cac 40"
        ]
        
        startup_score = sum(1 for s in startup_signals if s in all_snippets_lower)
        enterprise_score = sum(1 for s in enterprise_signals if s in all_snippets_lower)
        
        # Try to extract founding year
        year_patterns = [
            r'founded (?:in )?(\d{4})',
            r'established (?:in )?(\d{4})',
            r'since (\d{4})',
            r'started (?:in )?(\d{4})'
        ]
        for pattern in year_patterns:
            match = re.search(pattern, all_snippets_lower)
            if match:
                year = int(match.group(1))
                result["founded_year"] = year
                # Companies founded after 2015 are more likely startups
                if year >= 2015:
                    startup_score += 3
                elif year >= 2010:
                    startup_score += 1
                break

        # Yahoo Finance: mega-cap EV forces enterprise regardless of snippet tie / thin startup signals
        if yf is not None:
            try:
                ticker = lookup_ticker(company_name)
                if ticker:
                    ydata = get_yahoo_finance_data(ticker)
                    ev_m = ydata.get("enterprise_value_m") or 0
                    if ev_m >= 1000:
                        print(
                            f"   📈 Yahoo override: large cap EV=${ev_m}M → forcing enterprise",
                            flush=True,
                        )
                        result["company_type"] = "enterprise"
                        result["estimated_size"] = "large"
                        result["is_startup"] = False
                        result["is_small_company"] = False
                        result["confidence"] = 1.0
                        print(
                            f"   📊 Company type: {result['company_type']} (startup_score={startup_score}, enterprise_score={enterprise_score})",
                            flush=True,
                        )
                        if result["founded_year"]:
                            print(f"   📅 Founded: {result['founded_year']}")
                        return result
            except Exception:
                pass
        
        # Determine company type
        total_signals = startup_score + enterprise_score
        if total_signals > 0:
            result["confidence"] = max(startup_score, enterprise_score) / total_signals
        
        if startup_score > enterprise_score:
            result["is_startup"] = True
            result["company_type"] = "startup"
            if startup_score >= 5:
                result["estimated_size"] = "early-stage"
            else:
                result["estimated_size"] = "growth-stage"
        elif enterprise_score > startup_score:
            result["company_type"] = "enterprise"
            result["estimated_size"] = "large"
        else:
            # Equal scores: prefer enterprise — low snippet overlap ties wrongly favored startups before.
            result["company_type"] = "enterprise"
            result["estimated_size"] = "large"
            result["is_startup"] = False
            result["is_small_company"] = False
        
        print(f"   📊 Company type: {result['company_type']} (startup_score={startup_score}, enterprise_score={enterprise_score})")
        if result["founded_year"]:
            print(f"   📅 Founded: {result['founded_year']}")
            
    except Exception as e:
        print(f"   ⚠️ Company type detection error: {e}")
    
    return result


def resolve_company_search_label(name: str) -> str:
    """
    When users paste a website (e.g. https://www.equifax.com/), Serp may still return hits but the CE
    LLM prompt must name the real company — otherwise extraction returns empty arrays quickly.
    """
    s = (name or "").strip()
    if not s.startswith(("http://", "https://")):
        return s
    try:
        from urllib.parse import urlparse

        netloc = urlparse(s).netloc.replace("www.", "").split(":")[0]
        if not netloc:
            return s
        parts = netloc.split(".")
        label = parts[0] if parts else netloc
        label = label.replace("-", " ").strip()
        return label.title() if label else s
    except Exception:
        return s


def generate_corporate_events(company_name: str, max_events: int = 20) -> list:
    """
    Fetches and extracts corporate M&A events for a company using web search and LLM.
    Automatically detects if company is a startup and adjusts search strategy accordingly.
    
    OPTIMIZED: Parallel SerpAPI queries; optional parallel LLM shards (CORPORATE_EVENTS_SHARD_SIZE, 0 = single call).
    
    Args:
        company_name: Name of the company to search for
        max_events: Maximum number of events to return
        
    Returns:
        List of dictionaries with keys: "Date", "Event (short)", "Event type", "Event value (USD)", "Source URL", "counterparties"
    """
    import os, json, re, time

    OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPEN_ROUTER_KEY")
    SERPAPI_KEY = os.getenv("SERPAPI_KEY")
    if not OPENROUTER_KEY or not SERPAPI_KEY:
        print("Missing API keys")
        return []

    _ce_raw_input = (company_name or "").strip()
    company_name = resolve_company_search_label(_ce_raw_input)
    if company_name != _ce_raw_input:
        print(
            f"   → Corporate events: resolved label {company_name!r} from pasted URL/query {_ce_raw_input[:48]!r}…",
            flush=True,
        )

    print(f"⚡ Fetching corporate events for: {company_name} (max_events={max_events})")
    
    # Detect company type to optimize search strategy
    company_info = detect_company_type(company_name, SERPAPI_KEY)

    # TESTING MODE: Only 1 query when max_events <= 1
    if max_events <= 1:
        print("⚡ TESTING MODE: Using minimal queries to save credits")
        queries = [
            f'"{company_name}" acquisition OR merger OR investment OR funding',
        ]
    elif company_info.get("is_startup") or company_info.get("is_small_company") or company_info.get("company_type") in ["startup", "small_company"]:
        # === STARTUP-OPTIMIZED QUERIES (reduced to 18 highest-yield) ===
        print("🚀 Using STARTUP-optimized search queries (PARALLEL)")
        queries = [
            # FUNDING (highest priority - 6 queries)
            f'"{company_name}" "raises" OR "raised" million funding',
            f'"{company_name}" "seed round" OR "pre-seed" OR "series A"',
            f'"{company_name}" "series B" OR "series C" OR "series D"',
            f'"{company_name}" funding 2025 OR 2024 OR 2023',
            f'"{company_name}" "led by" investment venture capital',
            f'"{company_name}" grant OR award OR prize',
            
            # NEWS SOURCES (best coverage - 6 queries)
            f'"{company_name}" site:techcrunch.com',
            f'"{company_name}" site:crunchbase.com funding',
            f'"{company_name}" site:eu-startups.com OR site:sifted.eu',
            f'"{company_name}" site:prnewswire.com OR site:businesswire.com',
            f'"{company_name}" site:venturebeat.com OR site:forbes.com startup',
            f'"{company_name}" site:dealroom.co',
            
            # DEALS & PARTNERSHIPS (6 queries)
            f'"{company_name}" accelerator OR incubator Y Combinator Techstars',
            f'"{company_name}" partnership OR "strategic partnership"',
            f'"{company_name}" acquired by OR acquisition',
            f'"{company_name}" "backed by" OR "portfolio company"',
            f'"{company_name}" climate tech OR cleantech OR sustainability',
            f'"{company_name}" announces investment OR funding round',
        ]
    else:
        # === ENTERPRISE-OPTIMIZED QUERIES (reduced to 18 highest-yield) ===
        print("🏢 Using ENTERPRISE-optimized search queries (PARALLEL)")
        queries = [
            # PRESS RELEASES (most accurate - 3 queries)
            f'"{company_name}" acquisition site:prnewswire.com OR site:businesswire.com',
            f'"{company_name}" acquisition site:globenewswire.com OR site:reuters.com',
            f'"{company_name}" acquisition site:bloomberg.com',
            
            # ACQUISITION PATTERNS (high-yield - 4 queries)
            f'"{company_name}" acquires OR acquired OR "has acquired"',
            f'"{company_name}" buys OR bought OR merger',
            f'"{company_name}" "announces acquisition" OR "completes acquisition"',
            f'"{company_name}" "acquisition of" OR "strategic acquisition"',
            
            # YEAR-BASED (recent deals - 3 queries covering 6 years)
            f'"{company_name}" acquisition 2025 OR 2024 OR 2023',
            f'"{company_name}" acquisition 2022 OR 2021 OR 2020',
            f'"{company_name}" acquisition 2019 OR 2018 OR 2017',
            
            # INVESTMENT/PE (4 queries)
            f'"{company_name}" "private equity" OR "growth equity" investment',
            f'"{company_name}" site:pitchbook.com OR site:pehub.com',
            f'"{company_name}" site:crunchbase.com funding history',
            f'"{company_name}" "raises" OR "raised" funding series',
            
            # DIVESTITURES & PARTNERSHIPS (4 queries)
            f'"{company_name}" sold OR divested OR divestiture',
            f'"{company_name}" partnership OR "joint venture" OR collaboration',
            f'"{company_name}" "bolt-on acquisition" OR "expands" acquisition',
            f'"{company_name}" M&A deal OR transaction',
        ]

    shard_sz = corporate_events_shard_size()
    use_legacy_ce_llm = shard_sz <= 0 or len(queries) <= 1

    # ========================================
    # 🚀 PARALLEL SEARCH EXECUTION
    # ========================================
    start_time = time.time()
    print(f"   → Running {len(queries)} search queries in PARALLEL...")

    grouped_for_shards = None
    if use_legacy_ce_llm:
        search_results = serpapi_parallel_search(queries, SERPAPI_KEY, num_results=15)
        elapsed = time.time() - start_time
        print(f"   ✅ Parallel search completed in {elapsed:.1f}s ({len(search_results)} unique results)")
        print(f"   → Collected {len(search_results)} unique search results")
        if not search_results:
            print("❌ No search results found")
            return []
        results_to_analyze = len(search_results)
        context = format_ce_journal_from_hits(search_results)
    else:
        grouped_for_shards = serpapi_parallel_search_grouped(queries, SERPAPI_KEY, num_results=15)
        elapsed = time.time() - start_time
        total_hits = sum(len(h[1]) for h in grouped_for_shards)
        print(f"   ✅ Parallel grouped search completed in {elapsed:.1f}s ({total_hits} hits across {len(queries)} queries)")
        if total_hits == 0:
            print("❌ No search results found")
            return []
        results_to_analyze = total_hits
        context = ""

    mode_msg = "legacy single LLM" if use_legacy_ce_llm else f"sharded ({shard_sz} queries/shard)"
    print(f"   → Preparing {results_to_analyze} snippets for AI extraction ({mode_msg})...", flush=True)

    # Determine if this is a startup for prompt customization
    is_startup_search = company_info.get("is_startup") or company_info.get("is_small_company") or company_info.get("company_type") in ["startup", "small_company"]

    if is_startup_search:
        extraction_checklist = f'''EXTRACTION CHECKLIST FOR STARTUPS - scan for ALL of these:
□ FUNDING ROUNDS (HIGHEST PRIORITY for startups):
  - Seed round, Pre-seed (look for: "seed funding", "pre-seed", "angel round")
  - Series A, Series B, Series C, etc. (look for: "series A", "raises $X million")
  - Bridge rounds, extension rounds
  - Convertible notes, SAFE agreements
  - Equity crowdfunding
  
□ GRANTS & NON-DILUTIVE FUNDING:
  - Government grants (Innovate UK, EIC Accelerator, EU grants)
  - Research grants, innovation awards
  - Climate/sustainability grants
  - Competition prize money
  
□ ACCELERATORS & INCUBATORS:
  - Y Combinator, Techstars, 500 Startups participation
  - Demo Day presentations
  - Startup competition wins
  - Incubator program completion
  
□ PARTNERSHIPS & CONTRACTS:
  - Strategic partnerships with enterprises
  - Pilot programs, proof of concept deals
  - Major customer contracts
  - Distribution agreements
  
□ ACQUISITIONS (startups get acquired):
  - Being acquired by larger company
  - Acqui-hire situations
  - Exit events
  
□ INVESTMENTS MADE BY STARTUP (if any):
  - Acquisitions of smaller companies
  - Strategic investments

STARTUP-SPECIFIC SIGNALS to look for:
- "raises", "raised", "secures", "closes" + funding amount
- "led by", "participated by", "backed by" + investor names
- "graduates from", "selected for", "joins" + accelerator name
- "wins", "awarded", "receives" + grant or prize
- "partners with", "signs deal with" + enterprise name'''
    else:
        extraction_checklist = f'''EXTRACTION CHECKLIST - scan for ALL of these:
□ Companies that "{company_name}" acquired (look for: "acquired", "buys", "bought", "acquisition of")
□ Companies that "{company_name}" merged with
□ Investors/PE firms/VCs that invested in "{company_name}"
□ Assets/divisions that "{company_name}" sold or divested
□ Regional/country-specific acquisitions (Brazil, UK, Australia, etc.)
□ Small bolt-on acquisitions and strategic purchases
□ FUNDING ROUNDS: Seed, Series A, Series B, etc. (look for: "raises", "raised", "funding round", "led by")
□ PARTNERSHIPS: Strategic partnerships, joint ventures, collaborations
□ IPO or SPAC transactions

COMMON MISSED DEALS - pay special attention to:
- Startup funding rounds (Seed, Series A/B/C, etc.)
- Strategic partnerships and collaborations
- Brazilian acquisitions (look for: Brazil, Latin America, LATAM)
- Research company acquisitions (look for: Research, Solutions, Analytics)
- Climate tech, fintech, healthtech sector deals
- Early-stage investments and accelerator programs'''

    def build_ce_prompt(results_to_analyze: int, max_events_return: int, journal_context: str, shard_note: str = "") -> str:
        note_prefix = (shard_note.strip() + "\n\n") if shard_note.strip() else ""
        return f'''Extract ALL corporate events for "{company_name}" from the {results_to_analyze} search results below.

YOUR GOAL: Find and return up to {max_events_return} UNIQUE corporate events including M&A, funding, grants, accelerators, and partnerships.

{note_prefix}{extraction_checklist}

EXTRACTION RULES:
1. Each unique target company = separate event (even if small deal)
2. Investments INTO the company = also events (VC/PE firm invests in company)
3. Funding rounds ARE corporate events (Series A, Seed round, etc.)
4. Partnerships and JVs ARE corporate events
5. If date is unclear, use the article date or "Jan 1, [year]" 
6. Extract ALL deals - do not filter by size or importance

OUTPUT: Return exactly {max_events_return} events if that many exist in the search results.

For EACH verified event, extract these EXACT fields:

1. **announcement_date**: When the deal was ANNOUNCED in format "MMM DD, YYYY" (e.g., "Nov 30, 2020")
   - Look for phrases like "announced on", "press release dated", "disclosed on"
   - This is when the deal was first made public
   - If unknown, use empty string ""

2. **closed_date**: When the deal was COMPLETED/CLOSED in format "MMM DD, YYYY" (e.g., "Feb 28, 2022")
   - Look for phrases like "completed", "closed", "finalized", "consummated"
   - This is when the transaction was legally completed
   - If deal not yet closed or date unknown, use empty string ""
   
   DATE ACCURACY RULES (CRITICAL):
   - ONLY use dates tied to THIS transaction (announcement, signing, closing) — not company founding, prior milestones, or unrelated history in the snippet.
   - NEVER use "founded in YYYY", "established in", "since YYYY", "incorporated", or "started" as announcement_date or closed_date unless the snippet is explicitly about THIS deal and uses that date for the deal.
   - Look for phrases like "announced on [date]", "press release dated", "completed on [date]", "closed [date]", "signed on [date]".
   - "Search result date: …" is the article's publish/index date. Use it as announcement_date ONLY when ALL of the following apply: (a) the source URL is a direct company press release domain or a major financial news outlet (not a database, directory, aggregator, or Wikipedia), AND (b) the body text contains no more specific deal-announcement phrase. NEVER use the search result date when it is obviously from a listing site, news aggregator, or is significantly older than the deal context.
   - A year mentioned as context (e.g. "the 2018 acquisition") is NOT the announcement date of the current article — do not confuse background history with the event date.
   - If article mentions year but not exact date, use "Jan 1, YYYY" and add "(approximate)" to the event description.
   - Do not guess — leave dates empty rather than picking an unrelated older date from the snippet.
   - Cross-reference dates across multiple sources when possible.
   - Prefer announcement_date if only one date is available.

4. **event_short**: Precise description following these patterns:
   - Acquisition: "{company_name} acquired [Target] to [brief purpose]"
   - Investment: "[Investor] invested in {company_name}"
   - Merger: "{company_name} merged with [Target]"
   - Sale: "{company_name} sold [Asset/Division] to [Buyer]"
   - Keep it concise: 10-20 words maximum

5. **description**: Professional, neutral 2-4 sentence description of the event.
   Write as a financial analyst explaining the deal to clients:
   - State what happened (transaction type, parties involved, deal value if known)
   - Explain the strategic rationale or context (why this deal matters)
   - Include key terms (cash, stock, financing structure) if mentioned
   - Use absolute dates where relevant
   - Be factual and neutral - no speculation
   - Do NOT use bullet points - write in flowing prose
   
   Example: "On February 24, 2025, Kynetec, a global leader in agricultural market research, completed its acquisition of Freshlogic, an Australian food and beverage analytics firm. The transaction, terms undisclosed, expands Kynetec's capabilities in food supply chain intelligence and strengthens its presence in the Asia-Pacific region. The deal was backed by Paine Schwartz Partners, Kynetec's majority shareholder."

6. **deal_type**: Use EXACTLY one of these (match case exactly):
   - "Acquisition" — company acquired another company
   - "Sale" — company was sold/acquired by another
   - "IPO" — initial public offering
   - "MBO" — management buyout
   - "Investment" — VC/PE investment, funding round (Seed, Series A/B/C, etc.)
   - "Strategic Review" — exploring strategic options
   - "Divestment" — selling off assets/divisions
   - "Restructuring" — corporate restructuring
   - "Dual track" — pursuing multiple exit options
   - "Closing" — deal completion/closing
   - "Grant" — government grant, innovation grant, research funding
   - "Debt financing" — debt/loan financing
   - "Bankruptcy" — bankruptcy filing
   - "Reorganisation" — corporate reorganization
   - "Employee tender offer" — employee stock buyback
   - "Rebrand" — company rebranding
   - "Partnership" — strategic partnership, collaboration, alliance
   - "Accelerator" — accelerator/incubator program participation
   - "Award" — competition win, prize, recognition with funding

7. **deal_status**: Use EXACTLY one of these based on the deal's current state:
   - "Completed" — deal is finalized/closed
   - "In Market" — deal is actively being marketed
   - "Not yet launched" — deal announced but not started
   - "Strategic Review" — company exploring options
   - "Deal Prep" — preparing for transaction
   - "In Exclusivity" — exclusive negotiations ongoing
   
   How to determine status:
   - If closed_date exists → "Completed"
   - If "exploring strategic alternatives" mentioned → "Strategic Review"
   - If "exclusive negotiations" mentioned → "In Exclusivity"
   - If only announcement_date and no close → check article for status clues
   - Default to "Completed" for historical deals

8. **value_usd**: Format EXACTLY as shown in these examples:
   - "$44,000,000,000 (enterprise value)"
   - "$2,225,000,000 (cash)"
   - "$550,000,000 (mix of cash & stock; net of cash acquired)"
   - "Reported / estimated > $500,000,000 (company did not disclose)"
   - "Undisclosed"
   
   Rules for value formatting:
   - Always use commas: $44,000,000,000 NOT $44000000000
   - Convert billions: "$2.2B" → "$2,200,000,000"
   - Convert millions: "$550M" → "$550,000,000"
   - Include transaction type in parentheses: (enterprise value), (cash), (mix of cash & stock)

9. **source_url**: The BEST URL for this event announcement/news article.
   - Prefer official company press releases (e.g., kynetec.com/kynetec-acquires-freshlogic)
   - If no press release, use the best news article URL from search results
   - Extract the FULL URL exactly as shown in the search results
   - If no URL available, use empty string ""

9b. **investment_amount_m**: (OPTIONAL — funding/investment events only) Deal amount in millions of the base currency as a number (e.g., 25.5 for $25.5M). Use null if not a funding event or if amount is undisclosed.

9c. **investment_currency**: (OPTIONAL — funding/investment events only) ISO currency code (e.g., "USD", "EUR", "GBP"). Use "" if not a funding event or unknown.

9d. **funding_stage**: (OPTIONAL — funding/investment events only) Funding round label, e.g. "Seed", "Series A", "Series B", "Series C", "Growth", "Venture Debt". Use "" if not applicable.

9e. **deal_terms**: (OPTIONAL — M&A/acquisition events only) Brief deal structure description, e.g. "all-cash acquisition", "all-stock merger", "mixed cash and stock", "leveraged buyout". Use "" if not an M&A event or if terms are undisclosed.

10. **counterparties**: Array of ALL companies involved in this deal with their ROLES.
   
   COUNTERPARTY TYPES (use exact type_id):
   - type_id: 17, type: "Target" — company being acquired/invested in/going public
   - type_id: 18, type: "Acquirer" — purchasing company in acquisition
   - type_id: 24, type: "Investor (majority)" — majority stake investor
   - type_id: 25, type: "Investor (minority)" — minority stake investor  
   - type_id: 26, type: "Investor (unknown)" — investor with unknown stake size
   - type_id: 19, type: "Seller" — company selling/divesting its stake
   - type_id: 20, type: "Joint Venture Partner" — JV partner
   
   For EACH counterparty include:
   - company_name: Exact company name as it appears in sources (REQUIRED)
   - type_id: Number from list above (REQUIRED)
   - type: Text label from list above (REQUIRED)
   - role_description: Brief description of their role in this specific deal (REQUIRED)
   - company_linkedin_url: LinkedIn company page URL if known, otherwise ""
   - press_release_url: Company's own press release URL for this deal if found, otherwise ""
   - individuals: Array of **strict C-suite** people quoted or named for this deal ONLY:
     Allowed: titles starting with **Chief** or CEO, CFO, COO, CTO, CRO, CISO, CPO, CMO, CLO as clear roles.
     **title is REQUIRED** — if you cannot assign a confident allowed C-suite title, omit the person (do not search filler names).
     For each individual include:
     - name: Full name (e.g., "Peter Berweger")
     - title: Allowed C-suite title only (e.g., "Chief Financial Officer", "CEO") — REQUIRED
     - linkedin_url: "" (leave empty for now)

11. **advisors**: Array of professional advisory firms that advised on this transaction.
   
   ⚠️ IMPORTANT: EXCLUDE LEGAL ADVISORS - Only extract financial and consulting advisors!
   
   ADVISOR TYPES TO INCLUDE:
   - Financial advisors (investment banks): Goldman Sachs, Morgan Stanley, JP Morgan, Lazard, Evercore, Centerview Partners, PJT Partners, Rothschild, Moelis, Jefferies, etc.
   - Consulting/Due Diligence: McKinsey, BCG, Bain, Deloitte, EY, KPMG, PwC, etc.
   
   ❌ DO NOT INCLUDE:
   - Legal advisors / Law firms (Skadden, Sullivan & Cromwell, Wachtell Lipton, Kirkland & Ellis, Simpson Thacher, Latham & Watkins, Davis Polk, Freshfields, Clifford Chance, etc.)
   - Any firm providing legal counsel, legal representation, or legal services
   
   For EACH advisor include:
   - advisor_name: Exact name of the advisory firm (REQUIRED)
   - advisor_type: "Financial Advisor" | "Due Diligence" | "Tax Advisor" | "Other" (NOT "Legal Advisor")
   - advised_party: Which counterparty they advised (e.g., "S&P Global", "Target", "Buyer")
   - announcement_url: URL where this advisor relationship was mentioned, if available
   
   ADVISOR EXTRACTION RULES:
   ✓ ONLY extract advisors EXPLICITLY mentioned in deal announcements/press releases
   ✓ Look for phrases like "advised by", "financial advisor to", "served as financial advisor"
   ✓ Investment banks often advise on deal terms, valuation, and negotiations
   ✓ DO NOT infer or guess advisors - only include if explicitly stated in sources
   ✓ If no advisors explicitly mentioned, use empty array []
   ✗ NEVER include law firms or legal counsel (out of scope)

COUNTERPARTY EXTRACTION RULES:
✓ EVERY deal has at least 2 counterparties (e.g., Acquirer + Target)
✓ The company "{company_name}" should appear as a counterparty in each event
✓ For acquisitions: identify both Acquirer (18) and Target (17)
✓ For divestitures: identify Seller (19) and Acquirer/Buyer (18)
✓ For investments: identify Investor (24/25/26) and Target (17)
✓ For mergers: both companies can be Target (17) if merger of equals, or one Acquirer + one Target
✓ For IPOs: company going public is Target (17)

CRITICAL EXTRACTION RULES:
✓ Extract ALL acquisitions - large deals, small bolt-on acquisitions, and regional purchases
✓ Include acquisitions even if deal value is undisclosed or unknown
✓ For major mergers: Extract BOTH announcement date AND completion date as separate events
✓ Match company names exactly as they appear in sources
✓ Extract actual transaction values - convert "billion" and "million" to full numbers
✓ If value not disclosed, use "Undisclosed"
✓ Distinguish between: announcement, agreement, and completion/close
✓ Only include M&A transactions - NO earnings, conferences, partnerships without transactions

SMALL/REGIONAL DEALS - IMPORTANT:
✓ Include acquisitions of small companies, regional businesses, product lines
✓ Include private equity investments (majority or minority stakes)
✓ Include bolt-on acquisitions that expand capabilities or geographic reach
✓ Even if limited information is available, include the deal with what data you have

Search results to analyze:
{journal_context}

Return ONLY valid JSON array (no markdown, no explanation):
[
  {{
    "announcement_date": "Nov 30, 2020",
    "closed_date": "",
    "event_short": "S&P Global and IHS Markit announced definitive all-stock merger",
    "description": "On November 30, 2020, S&P Global Inc. announced a definitive agreement to acquire IHS Markit Ltd. in an all-stock transaction valued at approximately $44 billion. The combination creates a leading provider of data, analytics and workflow solutions across capital, commodity and automotive markets. The deal is expected to generate approximately $480 million in annual cost synergies and is subject to regulatory approvals.",
    "deal_type": "Acquisition",
    "deal_status": "In Exclusivity",
    "value_usd": "$44,000,000,000 (enterprise value)",
    "source_url": "https://press.spglobal.com/2020-11-30-S-P-Global-and-IHS-Markit-to-Merge",
    "investment_amount_m": null,
    "investment_currency": "",
    "funding_stage": "",
    "deal_terms": "all-stock merger",
    "counterparties": [
      {{"company_name": "S&P Global", "type_id": 18, "type": "Acquirer", "role_description": "Acquiring company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Douglas Peterson", "title": "President & CEO", "linkedin_url": ""}}]}},
      {{"company_name": "IHS Markit", "type_id": 17, "type": "Target", "role_description": "Target company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Lance Uggla", "title": "Chairman & CEO", "linkedin_url": ""}}]}}
    ],
    "advisors": [
      {{"advisor_name": "Goldman Sachs", "advisor_type": "Financial Advisor", "advised_party": "S&P Global", "announcement_url": ""}}
    ]
  }},
  {{
    "announcement_date": "Nov 30, 2020",
    "closed_date": "Feb 28, 2022",
    "event_short": "S&P Global completed merger with IHS Markit",
    "description": "On February 28, 2022, S&P Global completed its acquisition of IHS Markit following receipt of all required regulatory approvals. The transaction, first announced in November 2020, was valued at $44 billion in an all-stock deal. The combined entity becomes a leading provider of credit ratings, benchmarks, analytics, and data solutions serving global capital and commodity markets.",
    "deal_type": "Closing",
    "deal_status": "Completed",
    "value_usd": "$44,000,000,000 (enterprise value)",
    "source_url": "https://press.spglobal.com/2022-02-28-Merger-Complete",
    "counterparties": [
      {{"company_name": "S&P Global", "type_id": 18, "type": "Acquirer", "role_description": "Acquiring company", "company_linkedin_url": "", "press_release_url": "", "individuals": []}},
      {{"company_name": "IHS Markit", "type_id": 17, "type": "Target", "role_description": "Target company", "company_linkedin_url": "", "press_release_url": "", "individuals": []}}
    ],
    "advisors": []
  }},
  {{
    "announcement_date": "Jan 15, 2021",
    "closed_date": "Jan 15, 2021",
    "event_short": "S&P Global acquired Visible Alpha",
    "description": "S&P Global acquired Visible Alpha, a financial technology company specializing in consensus estimate data and company-level research analytics. The acquisition strengthens S&P Global's Market Intelligence division by adding granular financial model data sourced from over 170 contributing investment research firms. Terms of the transaction were not disclosed.",
    "deal_type": "Acquisition",
    "deal_status": "Completed",
    "value_usd": "Undisclosed",
    "source_url": "https://www.spglobal.com/visible-alpha-acquisition",
    "investment_amount_m": null,
    "investment_currency": "",
    "funding_stage": "",
    "deal_terms": "all-cash acquisition",
    "counterparties": [
      {{"company_name": "S&P Global", "type_id": 18, "type": "Acquirer", "role_description": "Acquiring company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Martina Cheung", "title": "Chief Operating Officer", "linkedin_url": ""}}]}},
      {{"company_name": "Visible Alpha", "type_id": 17, "type": "Target", "role_description": "Target company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Scott Ryles", "title": "CEO", "linkedin_url": ""}}]}}
    ],
    "advisors": [
      {{"advisor_name": "Jefferies", "advisor_type": "Financial Advisor", "advised_party": "Visible Alpha", "announcement_url": ""}}
    ]
  }}
]

JSON:'''

    try:
        if use_legacy_ce_llm:
            prompt = build_ce_prompt(results_to_analyze, max_events, context, shard_note="")
            print("   🤖 CE single-call LLM: OpenRouter (may take 1–4 min for large journals)...", flush=True)
            events = openrouter_extract_ce_events_json(
                prompt, OPENROUTER_KEY, max_tokens=32000, timeout=180, max_retries=2
            )
            result = normalize_llm_ce_events_to_rows(events, company_name, max_events)
        else:
            shards = []
            for grp_i in range(0, len(grouped_for_shards), shard_sz):
                chunk = grouped_for_shards[grp_i : grp_i + shard_sz]
                hits = []
                seen_urls = set()
                for _q, reslist in chunk:
                    for r in reslist:
                        url = (r.get("link") or "").strip()
                        if url:
                            if url in seen_urls:
                                continue
                            seen_urls.add(url)
                        hits.append(r)
                shards.append(hits)

            total_shards = len(shards)
            num_shards_approx = (len(queries) + shard_sz - 1) // max(1, shard_sz)
            per_shard_cap = max(max_events, min(120, max_events * max(5, num_shards_approx)))

            try:
                _ce_conc = int(os.getenv("CORPORATE_EVENTS_LLM_CONCURRENCY", "4"))
            except ValueError:
                _ce_conc = 4
            _ce_conc = max(1, min(_ce_conc, 12))
            print(
                f"   🤖 CE parallel LLM: {total_shards} shard(s), up to {_ce_conc} concurrent OpenRouter calls "
                f"(read timeout 180s/call; expect ~1–3 min wall-clock per wave)",
                flush=True,
            )

            llm_t0 = time.time()
            all_rows = []

            def run_shard(shard_idx: int, shard_hits: list):
                if not shard_hits:
                    return []
                t_shard = time.time()
                journal = format_ce_journal_from_hits(shard_hits)
                note = (
                    "SHARD CONTEXT: You are processing shard %s of %s (a subset of parallel web searches). "
                    "Extract every distinct corporate event supported by these snippets only. "
                    "The same real-world deal may appear in another shard; overlapping outputs are OK and will be merged downstream."
                ) % (shard_idx, total_shards)
                pr = build_ce_prompt(len(shard_hits), per_shard_cap, journal, shard_note=note)
                tok_floor = 6000 if is_startup_search else 12000
                mtok = min(16000, max(tok_floor, 220 * len(shard_hits)))
                print(
                    f"   … CE shard {shard_idx}/{total_shards}: request started ({len(shard_hits)} snippets, max_tokens={mtok})",
                    flush=True,
                )
                with _ce_llm_concurrency_semaphore():
                    raw_ev = openrouter_extract_ce_events_json(
                        pr, OPENROUTER_KEY, max_tokens=mtok, timeout=180, max_retries=2
                    )
                rows = normalize_llm_ce_events_to_rows(raw_ev, company_name, per_shard_cap)
                print(
                    f"   ✅ CE shard {shard_idx}/{total_shards}: finished in {time.time() - t_shard:.1f}s ({len(rows)} rows)",
                    flush=True,
                )
                return rows

            max_workers = min(12, max(1, total_shards))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(run_shard, si + 1, sh): si for si, sh in enumerate(shards)}
                for fut in as_completed(futures):
                    try:
                        all_rows.extend(fut.result())
                    except Exception as _shard_exc:
                        print(f"   ⚠️ Corporate-events shard failed: {_shard_exc}")

            print(
                f"   ✅ Parallel LLM extraction finished in {time.time() - llm_t0:.1f}s "
                f"({total_shards} shards, {len(all_rows)} candidate rows pre-merge)",
                flush=True,
            )
            if not all_rows:
                print(
                    "   ⚠️ CE shards produced no rows — often caused by URL pasted as company "
                    "(now auto-resolved) or OpenRouter returning []. Check warnings above.",
                    flush=True,
                )
            result = merge_dedupe_ce_rows(all_rows, max_events)

        print(f"SUCCESS: {len(result)} corporate events loaded for {company_name}")

        # SECOND PASS: Enrich counterparties with individuals using Perplexity
        print(f"   → Enriching counterparties with individuals...", flush=True)
        result = enrich_counterparties_with_individuals(result, company_name)

        # Validate and fix date logic issues
        print(f"\n📅 Validating event dates...")
        result = validate_and_fix_event_dates(result)

        # Log counterparty summary
        total_counterparties = sum(len(e.get("counterparties", [])) for e in result)
        total_individuals = sum(len(cp.get("individuals", [])) for e in result for cp in e.get("counterparties", []))
        total_advisors = sum(len(e.get("advisors", [])) for e in result)
        print(f"   → {total_counterparties} counterparties extracted across {len(result)} events")
        print(f"   → {total_individuals} individuals identified")
        print(f"   → {total_advisors} advisors identified")

        # Log date and status extraction
        events_with_announcement = sum(1 for e in result if e.get("Announcement Date"))
        events_with_closed = sum(1 for e in result if e.get("Closed Date"))
        print(f"   → Dates: {events_with_announcement} with announcement date, {events_with_closed} with closed date")

        # Log deal types and statuses
        deal_types = {}
        deal_statuses = {}
        for e in result:
            dt = e.get("Deal Type", "Unknown")
            ds = e.get("Deal Status", "Unknown")
            deal_types[dt] = deal_types.get(dt, 0) + 1
            deal_statuses[ds] = deal_statuses.get(ds, 0) + 1
        print(f"   → Deal types: {deal_types}")
        print(f"   → Deal statuses: {deal_statuses}")

        return result

    except Exception as e:
        print(f"Event generation failed: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_ceo_from_serpapi_ai(company_name: str) -> str:
    """
    Extracts ONLY the CURRENT CEO using SERPAPI + strict AI prompt.
    No regex, no guessing. Returns CEO name or "".
    """

    print(f"🔎 SERPAPI + AI CEO extractor for {company_name}")

    queries = [
        f"{company_name} current CEO",
        f"{company_name} CEO",
        f"who is the CEO of {company_name}",
        f"{company_name} chief executive officer",
    ]

    # PARALLEL search for CEO queries
    print(f"   → Running {len(queries)} CEO queries in PARALLEL...")
    parallel_results = serpapi_parallel_search(queries, SERPAPI_KEY, num_results=10)
    serp_text = "\n\n".join([f"{r.get('title', '')}\n{r.get('snippet', '')}" for r in parallel_results])
    print(f"   ✅ CEO search: {len(parallel_results)} results")

    if not serp_text.strip():
        print("❌ No SERPAPI text for CEO extraction.")
        return ""

    # ------------------------------------------------------
    # 🔥 STRICT CEO-ONLY PROMPT (never guesses)
    # ------------------------------------------------------
    prompt = f"""
Extract ONLY the CURRENT CEO of "{company_name}" from the text below.

RULES:
- Return ONLY the CEO's full name.
- No sentences.
- No extra words.
- No titles.
- No guessing.
- If the CEO is not explicitly mentioned in the text, return EXACTLY: NONE

Text:
{serp_text[:6000]}
"""

    ai_ceo = openrouter_chat(
        "perplexity/sonar-pro",
        prompt,
        f"CEO-Extractor-{company_name}"
    )

    if not ai_ceo:
        return ""

    ai_ceo = ai_ceo.strip()

    if ai_ceo.upper() == "NONE":
        print("❌ AI reports no explicit CEO found.")
        return ""

    print(f"✅ CEO (AI extracted): {ai_ceo}")
    return ai_ceo

# ===========================================
def get_ceo_from_serpapi(company_name: str) -> str:
    """
    Highly reliable CEO extractor modeled after get_top_management().
    Uses:
      1. SERPAPI direct extraction
      2. SERPAPI source scraping
      3. Sonar-Pro confirmation (NO guessing)
      4. Claude formatting only
      5. Regex fallbacks

    Returns: exact CEO name or "".
    """

    print(f"🔎 SERPAPI (Advanced CEO Extraction) → {company_name}")

    # =====================================================
    # 1️⃣ SERPAPI Google Search Queries (PARALLEL)
    # =====================================================
    queries = [
        f'"{company_name}" CEO',
        f'"{company_name}" current CEO',
        f'who is the CEO of "{company_name}"',
        f'"{company_name}" chief executive officer',
        f'"{company_name}" leadership team CEO',
        f'"{company_name}" CEO site:linkedin.com',
        f'"{company_name}" CEO site:crunchbase.com',
    ]

    # PARALLEL search for all CEO queries at once
    print(f"   → Running {len(queries)} advanced CEO queries in PARALLEL...")
    parallel_results = serpapi_parallel_search(queries, SERPAPI_KEY, num_results=10)
    results_text = "\n".join([f"{r.get('title', '')}. {r.get('snippet', '')}" for r in parallel_results])
    print(f"   ✅ Advanced CEO search: {len(parallel_results)} results")

    # If SERPAPI returned nothing
    if not results_text.strip():
        print("⚠️ No SERPAPI results found.")
    else:
        print("📄 SERPAPI gathered CEO data (raw text length:", len(results_text), ")")

    # =====================================================
    # 2️⃣ Extract using strong patterns
    # =====================================================
    patterns = [
        r"CEO(?: of [A-Za-z0-9&.,\s]+)? is ([A-Z][a-zA-Z.'\- ]+)",
        r"([A-Z][a-zA-Z.'\- ]+) is the CEO",
        r"CEO[:\-]\s*([A-Z][a-zA-Z.'\- ]+)",
        r"Chief Executive Officer[:\-]?\s*([A-Z][a-zA-Z.'\- ]+)",
        r"CEO\s+([A-Z][a-zA-Z.'\- ]+)",
    ]

    for p in patterns:
        match = re.search(p, results_text)
        if match:
            ceo_name = match.group(1).strip()
            print(f"🟢 CEO extracted by SERPAPI pattern: {ceo_name}")
            return ceo_name

    # =====================================================
    # 3️⃣ SONAR PRO VALIDATION (NOT GUESSING)
    # =====================================================
    sonar_prompt = f"""
From the text below, identify ONLY the current CEO of {company_name}.
If no CEO name is explicitly mentioned, reply with EXACTLY: "NONE"

Text:
{results_text[:6000]}
"""

    sonar_reply = openrouter_chat("perplexity/sonar-pro", sonar_prompt, f"CEO-Validate-{company_name}")

    if sonar_reply and "NONE" not in sonar_reply.upper():
        # Extract name from Sonar reply
        m = re.search(r"[A-Z][a-zA-Z.'\- ]+", sonar_reply.strip())
        if m:
            ceo_name = m.group(0).strip()
            print(f"🟡 CEO confirmed by Sonar-Pro: {ceo_name}")
            return ceo_name

    # =====================================================
    # 4️⃣ Claude clean formatting if messy
    # =====================================================
    if sonar_reply and len(sonar_reply.split()) <= 6:
        try:
            m = re.search(r"[A-Z][a-zA-Z.'\- ]+", sonar_reply.strip())
            ceo_name = m.group(0).strip()
            print(f"🔵 CEO formatted by Claude: {ceo_name}")
            return ceo_name
        except:
            pass

    # =====================================================
    # 5️⃣ FINAL Regex fallback
    # =====================================================
    fallback_match = re.search(
        r"([A-Z][a-z]+ [A-Z][a-zA-Z.'\-]+)[,]? (?:CEO|Chief Executive Officer)",
        results_text
    )
    if fallback_match:
        ceo_name = fallback_match.group(1).strip()
        print(f"🟣 CEO extracted by fallback regex: {ceo_name}")
        return ceo_name

    print("❌ CEO not found in SERPAPI or extraction patterns.")
    return ""

# ============================================================
# 🔹 Company Summary Generator
# ============================================================
def generate_summary(company_name, text="", yahoo_data=None):
    """
    Company summary where CEO is ALWAYS extracted using
    SERPAPI + strict AI CEO extractor (zero hallucination).
    Uses web search as fallback when Wikipedia has no data.

    Pass pre-fetched ``yahoo_data`` (dict from enrich_with_yahoo_finance) to skip
    a redundant Yahoo Finance lookup inside this function.
    """
    
    # Extract domain/company name from URL if needed
    search_name = company_name
    website_from_input = ""
    if company_name.startswith("http://") or company_name.startswith("https://"):
        website_from_input = company_name
        from urllib.parse import urlparse
        parsed = urlparse(company_name)
        domain = parsed.netloc.replace("www.", "")
        search_name = domain.split(".")[0]  # e.g., "ecorth" from "ecorth.com"
        print(f"   → Extracted company name '{search_name}' from URL")

    # ------ Step 1: Get source text (Wikipedia first, then web search) ------
    if not text.strip():
        text = get_wikipedia_summary(search_name)
    
    # If Wikipedia has no useful data, use web search (PARALLEL)
    if not text.strip() or len(text) < 100:
        print(f"   → Wikipedia has no data for {search_name}, using web search (PARALLEL)...")
        # Search for company info from multiple sources
        search_queries = [
            f'"{search_name}" company about headquarters',
            f'"{search_name}" site:linkedin.com/company',
            f'"{search_name}" site:crunchbase.com',
            f'"{search_name}" founded CEO location',
            f'site:{website_from_input.replace("https://", "").replace("http://", "").rstrip("/")}' if website_from_input else f'"{search_name}" company',
        ]
        search_queries = [q for q in search_queries if q]  # Filter empty queries
        
        serpapi_key = os.environ.get("SERPAPI_KEY", "")
        if serpapi_key and search_queries:
            print(f"   → Running {len(search_queries)} summary queries in PARALLEL...")
            parallel_results = serpapi_parallel_search(search_queries, serpapi_key, num_results=5)
            search_text = "\n".join([f"{r.get('title', '')}: {r.get('snippet', '')}" for r in parallel_results])
            if search_text.strip():
                text = search_text
                print(f"   ✅ Parallel summary search: {len(parallel_results)} results, {len(text)} chars")
    
    # ------ Step 1.5: Find press page via direct URL checking + search ------
    press_page_url = ""
    website_base = ""
    domain_for_search = ""
    
    if website_from_input:
        from urllib.parse import urlparse
        parsed = urlparse(website_from_input)
        domain_for_search = parsed.netloc.replace("www.", "")
        website_base = f"{parsed.scheme}://{parsed.netloc}"
    
    def check_url_exists(url, timeout=5):
        """Check if a URL exists and returns 200."""
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True, 
                               headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                return True
            # Some sites block HEAD, try GET
            resp = requests.get(url, timeout=timeout, allow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
            return resp.status_code == 200
        except:
            return False
    
    # Step 1.5a: Try common press page URL patterns (PARALLEL)
    if website_base:
        common_press_paths = [
            "/press", "/news", "/newsroom", "/media",
            "/press-releases", "/press-room", "/pressroom",
            "/about/press", "/about/news", "/about-us/press",
            "/about-us/news", "/about-us/press-room",
            "/company/news", "/company/press",
            "/corporate/press", "/corporate/news",
            "/insights", "/resources/news", "/resources/press",
            "/en/press", "/en/news", "/en/newsroom",
        ]
        
        print(f"   → Checking {len(common_press_paths)} press page paths in PARALLEL on {website_base}...")
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def check_press_path(path):
            test_url = website_base + path
            try:
                if check_url_exists(test_url):
                    return test_url
            except:
                pass
            return None
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_path = {executor.submit(check_press_path, p): p for p in common_press_paths}
            for future in as_completed(future_to_path):
                try:
                    result = future.result()
                except Exception:
                    # Cancelled futures raise CancelledError here — skip them
                    continue
                if result and not press_page_url:
                    press_page_url = result
                    print(f"   ✅ Found press page: {press_page_url}")
                    # Cancel remaining futures
                    for f in future_to_path:
                        f.cancel()
    
    # Step 1.5b: If not found, search via SerpAPI (PARALLEL)
    if not press_page_url and domain_for_search:
        press_search_queries = [
            f'site:{domain_for_search} press OR newsroom OR "press releases"',
            f'site:{domain_for_search} news announcements',
        ]
        
        serpapi_key = os.environ.get("SERPAPI_KEY", "")
        if serpapi_key:
            print(f"   → Running {len(press_search_queries)} press page queries in PARALLEL...")
            press_results = serpapi_parallel_search(press_search_queries, serpapi_key, num_results=5)

            def _is_press_section(url: str) -> bool:
                """Accept only section-level press/news pages, not individual articles."""
                if not url:
                    return False
                try:
                    from urllib.parse import urlparse as _up
                    path = _up(url).path.rstrip("/").lower()
                    segs = [s for s in path.split("/") if s]
                    SECTION_KW = {"news", "press", "newsroom", "press-releases", "press-room",
                                  "media", "media-center", "announcements", "updates", "articles",
                                  "blog", "insights", "in-the-news", "coverage", "category"}
                    if not any(kw in path for kw in SECTION_KW):
                        return False
                    if len(segs) > 4:
                        return False
                    last = segs[-1] if segs else ""
                    if len(last) > 60:
                        return False
                    if re.search(r'\d{4}[-/]\d{2}', last):
                        return False
                    return True
                except Exception:
                    return False

            for result in press_results:
                link = result.get("link", "")
                if link and _is_press_section(link):
                    if check_url_exists(link):
                        press_page_url = link
                        print(f"   ✅ Found press page via search: {press_page_url}")
                        break
                elif link:
                    print(f"   ⚠️ Skipped press URL (looks like article): {link}")
    
    if press_page_url:
        print(f"   📰 Press page URL: {press_page_url}")
    else:
        print(f"   ⚠️ No press page found")

    # ------ Step 1.6: Add targeted financial/investor search context ------
    financial_queries = [
        f'"{search_name}" enterprise value OR EV revenue EBITDA',
        f'"{search_name}" investors OR "backed by" OR "portfolio of"',
        f'"{search_name}" site:pitchbook.com OR site:crunchbase.com',
    ]
    serpapi_key = os.environ.get("SERPAPI_KEY", "")
    if serpapi_key and financial_queries:
        try:
            print(f"   → Running {len(financial_queries)} financial context queries in PARALLEL...")
            fin_results = serpapi_parallel_search(financial_queries, serpapi_key, num_results=5)
            fin_text = "\n".join(
                [f"{r.get('title', '')}: {r.get('snippet', '')}" for r in fin_results]
            )
            if fin_text.strip():
                text += "\n\n" + fin_text
                print(f"   ✅ Added financial context: {len(fin_results)} results, {len(fin_text)} chars")
        except Exception as e:
            print(f"   ⚠️ Financial context search failed: {e}")

    # Use pre-fetched Yahoo data if passed in (avoids a double lookup)
    if yahoo_data is None:
        yahoo_data = enrich_with_yahoo_finance(search_name, website_from_input)
    if yahoo_data:
        institutional_holders = yahoo_data.get("institutional_holders", []) or []
        yahoo_context = f"""
Yahoo Finance Data (authoritative, use these values):
- Enterprise Value: ${yahoo_data.get('enterprise_value_m')}M {yahoo_data.get('currency', '')}
- Revenue (TTM): ${yahoo_data.get('revenue_m')}M {yahoo_data.get('currency', '')}
- EBITDA (TTM): ${yahoo_data.get('ebitda_m')}M {yahoo_data.get('currency', '')}
- EBITDA Margin: {round(yahoo_data.get('ebitda_margin', 0) * 100, 1) if yahoo_data.get('ebitda_margin') else 'N/A'}%
- Market Cap: ${yahoo_data.get('market_cap_m')}M
- EV/Revenue: {yahoo_data.get('ev_revenue')}x
- EV/EBITDA: {yahoo_data.get('ev_ebitda')}x
- Revenue Growth: {round(yahoo_data.get('revenue_growth', 0) * 100, 1) if yahoo_data.get('revenue_growth') else 'N/A'}%
- Employees: {yahoo_data.get('employees')}
- Exchange: {yahoo_data.get('exchange')} ({yahoo_data.get('ticker')})
- Institutional Ownership: {round((yahoo_data.get('institutional_ownership_pct') or 0) * 100, 1)}%
- Top Institutional Holders: {', '.join([h['name'] for h in institutional_holders[:5]])}
- Source: {yahoo_data.get('source_url')}
"""
        text = yahoo_context + "\n\n" + text
        print("   ✅ Yahoo Finance data injected into context")

    # ------ Step 2: Use Perplexity for accurate company info ------
    
    # Ownership status types - comprehensive classification
    ownership_types = [
        # Public vs Private (most universal)
        "Public",           # Listed on stock exchange
        "Private",          # Privately held, general
        # By Investor/Owner Type
        "Venture-Backed",   # Owned by VC firms
        "Private Equity-Backed",  # Owned by PE firms
        "Family-Owned",     # Controlled by a family
        "Employee-Owned",   # ESOP structure
        "Founder-Owned",    # Still controlled by founders
        "Institutional-Owned",  # Owned by institutions
        # Special Categories
        "Government-Owned", # Public sector/state-owned
        "Non-Profit",       # Mission-driven, no shareholders
        "Subsidiary",       # Owned by parent company
        "Cooperative",      # Member-owned cooperative
        "Partnership",      # LP, LLP structure
    ]
    
    # website_base already defined in Step 1.5 above
    
    prompt = f"""
You are a professional researcher. Find and extract complete company details for "{search_name}".

Return ONLY in this exact markdown format (no extra text):

**Company Details**
- Company Name: <full legal/common name>
- Former Name: <previous legal/common name, or Unknown>
- Year Founded: <year>
- Website: <full URL like https://www.example.com>
- LinkedIn: <full LinkedIn URL like https://www.linkedin.com/company/example>
- Press Page: <full URL to company's press releases or news page>
- Headquarters: <city, country>
- Ownership Status: <ownership type>
- Primary Business Focus: <the main business focus category that best describes this company's core activity>
- Primary Sectors: <comma-separated list of the main sectors this company operates in>
- Secondary Sectors: <comma-separated list of additional but less central sectors, or "None">
- CEO: <full name>
- Investors: <comma-separated list of known investors (VCs, PE firms, corporates), or Unknown>
- Last Investment Amount: <latest funding round / investment amount, e.g. 50 or 50m, or Unknown>
- Last Investment Currency: <ISO currency code, e.g. USD, EUR, GBP, or Unknown>
- Last Investment Date: <YYYY-MM-DD if available, otherwise YYYY-MM or YYYY, or Unknown>
- Last Investment Source: <source URL for latest funding/investment data, or Unknown>
- Revenues: <latest revenue in millions, or Unknown>
- Revenues Currency: <ISO currency code, e.g. USD, EUR, GBP, or Unknown>
- Revenues Year: <year, or Unknown>
- Revenues Source: <source URL for revenue data, or Unknown>
- Enterprise Value: <enterprise value in millions, or Unknown>
- Enterprise Value Currency: <ISO currency code, e.g. USD, EUR, GBP, or Unknown>
- Enterprise Value Year: <year, or Unknown>
- Enterprise Value Source: <source URL for enterprise value data, or Unknown>
- EBITDA: <latest EBITDA in millions, or Unknown>
- EBITDA Currency: <ISO currency code, e.g. USD, EUR, GBP, or Unknown>
- EBITDA Year: <year, or Unknown>
- EBITDA Source: <source URL for EBITDA data, or Unknown>

CRITICAL RULES:

1. For Website: Must be a full URL starting with https:// or http://

2. For LinkedIn: Must be the full LinkedIn company page URL

3. For Press Page: THIS IS IMPORTANT - Find the company's official press releases, news, or announcements page.
   {f"Start by checking: {website_base}/press, {website_base}/news, {website_base}/newsroom, {website_base}/press-releases, {website_base}/about-us/press-room" if website_base else ""}
   Common URL patterns to look for:
   - /press, /press-releases, /news, /newsroom, /media
   - /about/press, /about-us/press, /about-us/press-room
   - /company/news, /corporate/press
   Examples of real press pages:
   - https://risk.lexisnexis.co.uk/about-us/press-room/press-release
   - https://plana.earth/press
   - https://www.apple.com/newsroom/
   Must be a FULL URL. If you cannot find it, write "Unknown"

4. For Headquarters: Format as "City, Country" using STANDARDIZED country names:
   - Use "UK" (not England, Scotland, Wales, Britain, United Kingdom, Great Britain)
   - Use "USA" (not United States, America, US)
   - Use "UAE" (not United Arab Emirates)
   - Use standard country names for others (Germany, France, etc.)

5. For Ownership Status: Choose EXACTLY ONE from this list:
   {', '.join(ownership_types)}
   
   ⚠️ FIRST CHECK IF PUBLIC: Before anything else, check if company is publicly traded!
   - Search for ticker symbol (e.g., NASDAQ: TEM, NYSE: AAPL)
   - Check if company had an IPO
   - If listed on ANY stock exchange → answer "Public"
   
   CLASSIFICATION GUIDELINES:
   - "Public" = Listed on stock exchange (NYSE, NASDAQ, LSE, TSX, etc.) - CHECK THIS FIRST!
   - "Private" = Privately held with no known institutional backing
   - "Venture-Backed" = Has received VC funding (Series A, B, C, etc.) but NOT public
   - "Private Equity-Backed" = Owned/controlled by PE firm(s) like KKR, Blackstone, Carlyle
   - "Family-Owned" = Controlled by a founding family
   - "Founder-Owned" = Still majority-controlled by original founders
   - "Employee-Owned" = ESOP or employee ownership structure
   - "Institutional-Owned" = Owned by pension funds, sovereign wealth funds (but not public)
   - "Government-Owned" = State-owned enterprise or public sector entity
   - "Non-Profit" = 501(c)(3), charity, foundation, mission-driven organization
   - "Subsidiary" = Wholly or majority owned by another company
   - "Cooperative" = Member-owned cooperative structure
   - "Partnership" = LP, LLP, or partnership structure
   
   Examples: Tempus AI = "Public" (NASDAQ: TEM), Apple = "Public" (NASDAQ: AAPL)
   
6. Primary Business Focus Definition:
   - This is the SINGLE most important business focus category that describes the company's core activity.
   - Choose the ONE category that best fits the company's primary business model and main revenue source.
   - Examples of business focus categories:
     - "Software" = Companies that develop and sell software products (SaaS, enterprise software, etc.)
     - "Financial Services" = Banks, payment processors, fintech, investment firms
     - "Healthcare" = Hospitals, health services, medical providers
     - "Data & Analytics" = Data platforms, analytics tools, business intelligence
     - "Consumer Internet" = Online consumer services, marketplaces, e-commerce platforms
     - "Business Services" = B2B services, consulting, professional services
     - "Pharmaceuticals" = Drug development, pharma companies
     - "Medical Equipment" = Medical devices, equipment manufacturers
     - "Telecommunications" = Telecom providers, network infrastructure
     - "Manufacturing" = Industrial manufacturing, production
     - "Retail" = Retail stores, consumer goods retail
     - "Energy & Commodities" = Energy companies, commodity trading
     - And many more specific categories
   - This should be more specific than a sector - it's the primary business model/focus area.

7. Sector Definition (for Primary/Secondary Sectors):
   - You MUST choose sector names ONLY from this exact list — do NOT invent new sector names:
     Technology, Information Technology, Healthcare, Financial Services, Consumer Goods,
     Consumer Discretionary, Consumer Staples, Industrials, Energy, Real Estate,
     Telecommunications, Utilities, Materials, Environment, Maritime, Aerospace & Defense,
     Media, Education, Government, Non-Profit, Transportation, Retail, Business Services,
     Data & Analytics, Pharmaceuticals, Biotechnology, Food & Beverage, Agriculture,
     Construction, Insurance, Defense, Cybersecurity, Logistics, Mining, Chemicals,
     Automotive, Aviation, Shipping, CleanTech, FinTech, HealthTech, PropTech
   - Primary Sectors = where the company generates most of its value / core business.
   - Secondary Sectors = adjacent areas or important but non-core activities.
   - When in doubt, pick the closest broad sector from the list above (e.g. "Marine Technology" → "Maritime", "Ocean Intelligence" → "Technology").

8. Search your knowledge for this company if the source text is insufficient
9. For financial metrics, prioritize the most recent disclosed figures and cite a source URL when available.
10. If you truly cannot find a value, write "Unknown"

{"Known website: " + website_from_input if website_from_input else ""}

Source text for reference:
{text[:6000]}
"""
    # Use Perplexity for better web-connected results
    summary = openrouter_chat(
        "perplexity/sonar-pro",
        prompt,
        f"Company Info - {search_name}"
    )

    if not summary:
        # Only fall back to GPT if Sonar fails entirely. Sonar has live web access
        # and is the better source for investors/financial metrics.
        summary = openrouter_chat(
            "openai/gpt-4o-mini",
            prompt,
            "Company Info Extractor"
        )

    if not summary:
        return "❌ No details found."

    print(f"   → AI returned company info: {summary[:300]}...")

    # ------ Step 3: Get CEO strictly from SERPAPI ------
    ceo = get_ceo_from_serpapi_ai(company_name)
    if not ceo:
        ceo = ""   # fallback empty — but NEVER hallucinate

    # ------ Step 4: Replace CEO line and inject press page if found ------
    final_lines = []
    ceo_replaced = False
    press_page_replaced = False

    for line in summary.split("\n"):
        cleaned = line.lower().replace("–", "-").replace("—", "-").strip()

        if cleaned.startswith("- ceo") or cleaned.startswith("ceo"):
            final_lines.append(f"- CEO: {ceo}")
            ceo_replaced = True
        elif ("press page:" in cleaned or "press-page:" in cleaned) and press_page_url:
            # Check if AI returned "Unknown" for press page - replace with our found URL
            val = line.split(":", 1)[-1].strip().lower()
            if val in ["unknown", "", "not found", "n/a"]:
                final_lines.append(f"- Press Page: {press_page_url}")
                press_page_replaced = True
                print(f"   → Injected press page URL from search: {press_page_url}")
            else:
                final_lines.append(line)
        else:
            final_lines.append(line)

    if not ceo_replaced:
        final_lines.append(f"- CEO: {ceo}")
    
    # If press page wasn't in the output at all but we found one, add it
    if press_page_url and not press_page_replaced:
        result_text = "\n".join(final_lines)
        if "press page:" not in result_text.lower():
            final_lines.append(f"- Press Page: {press_page_url}")
            print(f"   → Added press page URL from search: {press_page_url}")

    if yahoo_data:
        yahoo_source = yahoo_data.get("source_url", "")
        yahoo_currency = yahoo_data.get("currency", "")
        yahoo_year = str(datetime.now().year)
        institutional_holders = yahoo_data.get("institutional_holders", []) or []
        yahoo_fields = {}

        if institutional_holders:
            yahoo_fields["Investors"] = ", ".join([h.get("name", "") for h in institutional_holders[:5] if h.get("name")])
        if yahoo_data.get("revenue_m") is not None:
            yahoo_fields["Revenues"] = str(yahoo_data.get("revenue_m"))
            yahoo_fields["Revenues Currency"] = yahoo_currency
            yahoo_fields["Revenues Year"] = yahoo_year
            yahoo_fields["Revenues Source"] = yahoo_source
        if yahoo_data.get("enterprise_value_m") is not None:
            yahoo_fields["Enterprise Value"] = str(yahoo_data.get("enterprise_value_m"))
            yahoo_fields["Enterprise Value Currency"] = yahoo_currency
            yahoo_fields["Enterprise Value Year"] = yahoo_year
            yahoo_fields["Enterprise Value Source"] = yahoo_source
        if yahoo_data.get("ebitda_m") is not None:
            yahoo_fields["EBITDA"] = str(yahoo_data.get("ebitda_m"))
            yahoo_fields["EBITDA Currency"] = yahoo_currency
            yahoo_fields["EBITDA Year"] = yahoo_year
            yahoo_fields["EBITDA Source"] = yahoo_source

        if yahoo_fields:
            _INVALID = {"unknown", "n/a", "none", "-", ""}
            for field, value in yahoo_fields.items():
                field_lower = field.lower()
                replaced = False
                for i, line in enumerate(final_lines):
                    if ":" not in line:
                        continue
                    fname = line.split(":", 1)[0].lstrip("- ").strip().lower()
                    if fname == field_lower:
                        existing_val = line.split(":", 1)[-1].strip().lower()
                        if existing_val in _INVALID:
                            # Replace the LLM's Unknown/empty with Yahoo data
                            final_lines[i] = f"- {field}: {value}"
                        replaced = True
                        break
                if not replaced:
                    final_lines.append(f"- {field}: {value}")

    return "\n".join(final_lines).strip()

# ============================================================
# 🔹 Company Description Generator
# ============================================================
def _parse_company_details_markdown(company_details: str) -> dict:
    """
    Parse the markdown produced by generate_summary() into a dict of fields.

    Expected shape:
    **Company Details**
    - Field: Value
    """
    if not company_details:
        return {}

    fields = {}
    for raw_line in (company_details or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("**") and line.endswith("**"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = (k or "").strip().lower()
        val = (v or "").strip()
        if not key:
            continue
        fields[key] = val
    return fields


def detect_ownership_from_description(description: str) -> dict:
    """
    Use LLM to infer ownership type from a company description.
    Returns a dict with primary_ownership_type, secondary_ownership_types,
    confidence, and reasoning — mapped to the Xano OWNERSHIP_TYPES values.
    """
    if not description or not description.strip():
        return {"ownership": "", "confidence": "Low", "reasoning": "No description provided"}

    # Map prompt output → Xano OWNERSHIP_TYPES labels
    OWNERSHIP_MAP = {
        "publicly listed":              "Public",
        "publicly traded":              "Public",
        "public":                       "Public",
        "vc-backed":                    "Venture Capital",
        "vc backed":                    "Venture Capital",
        "venture capital":              "Venture Capital",
        "venture-backed":               "Venture Capital",
        "pe-owned":                     "Private Equity",
        "pe owned":                     "Private Equity",
        "private equity":               "Private Equity",
        "sponsor-backed":               "Private Equity",
        "sponsor backed":               "Private Equity",
        "subsidiary of public company": "Subsidiary",
        "subsidiary of private company":"Subsidiary",
        "subsidiary":                   "Subsidiary",
        "acquired":                     "Acquired",
        "government-owned":             "Government",
        "government owned":             "Government",
        "state-owned":                  "Government",
        "state owned":                  "Government",
        "government":                   "Government",
        "nonprofit":                    "Foundation",
        "non-profit":                   "Foundation",
        "not-for-profit":               "Foundation",
        "foundation":                   "Foundation",
        "employee-owned":               "Employee-Owned",
        "employee owned":               "Employee-Owned",
        "cooperative":                  "Consortium",
        "co-op":                        "Consortium",
        "consortium":                   "Consortium",
        "fund":                         "Fund",
        "institutionally backed":       "Fund",
        "institutional":                "Fund",
        "partnership":                  "Partnership",
        "founder-owned":                "Private",
        "founder owned":                "Private",
        "family-owned":                 "Private",
        "family owned":                 "Private",
        "bootstrapped":                 "Private",
        "privately held":               "Private",
        "private":                      "Private",
    }

    prompt = f"""You are given a company description.

Your task is to determine the most likely ownership type of the company based only on the information provided in the description.

Possible ownership types:
- Publicly Listed
- Privately Held
- VC-backed
- PE-owned
- Founder-owned
- Family-owned
- Bootstrapped
- Government-owned
- State-owned
- Subsidiary of Public Company
- Subsidiary of Private Company
- Employee-owned
- Cooperative
- Nonprofit
- Institutionally Backed
- Sponsor-backed

Instructions:
1. Read the company description carefully.
2. Infer the most likely ownership type based on wording such as:
   - mentions of venture funding, Series A/B/C, investors, startup, venture capital → VC-backed
   - mentions of private equity firms, buyouts, majority ownership, portfolio company → PE-owned
   - mentions of public markets, stock exchange, listed company, ticker symbol → Publicly Listed
   - mentions of subsidiary, division, owned by another company → Subsidiary of Public Company or Subsidiary of Private Company
   - mentions of family control, founder control, self-funded growth, government ownership, nonprofit status, etc.
3. If multiple ownership types apply, return a primary_ownership_type and optional secondary_ownership_types.
4. If there is not enough information to confidently assign a more specific category, default to "Privately Held".
5. Do not invent facts that are not supported by the description.
6. Return confidence as High, Medium, or Low.

Return ONLY valid JSON (no markdown, no extra text):
{{
  "primary_ownership_type": "",
  "secondary_ownership_types": [],
  "confidence": "",
  "reasoning": ""
}}

Company description:
{description[:3000]}"""

    try:
        raw = openrouter_chat("openai/gpt-4o-mini", prompt, "Ownership Detector")
        if not raw:
            return {"ownership": "", "confidence": "Low", "reasoning": "LLM returned empty"}

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        result = json.loads(cleaned)
        primary = (result.get("primary_ownership_type") or "").strip().lower()
        xano_type = OWNERSHIP_MAP.get(primary, "")

        # Try partial match if exact key not found
        if not xano_type:
            for key, val in OWNERSHIP_MAP.items():
                if key in primary or primary in key:
                    xano_type = val
                    break

        if not xano_type:
            xano_type = "Private"  # safe default

        print(f"[Ownership] '{primary}' → '{xano_type}' (confidence: {result.get('confidence','?')})")
        return {
            "ownership": xano_type,
            "confidence": result.get("confidence", "Medium"),
            "reasoning": result.get("reasoning", ""),
            "secondary": result.get("secondary_ownership_types", []),
        }
    except Exception as e:
        print(f"[Ownership] Detection error: {e}")
        return {"ownership": "", "confidence": "Low", "reasoning": str(e)}


def generate_description(company_name, text="", company_details=""):
    """
    Generates a professional, neutral, fact-based company profile in a consistent, useful format.

    Args:
        company_name (str): The name of the company.
        text (str): Optional source text to extract description from.
        company_details (str): Optional verified company details to include in context.

    Returns:
        str: A consistent multi-line company profile, or an error message if generation fails.
    """
    # Use provided text or fetch from Wikipedia
    if not text.strip():
        text = get_wikipedia_summary(company_name)
    parsed = _parse_company_details_markdown(company_details or "")
    year_founded = parsed.get("year founded", "")
    headquarters = parsed.get("headquarters", "")
    ownership_status = parsed.get("ownership status", "")
    ceo = parsed.get("ceo", "")
    primary_business_focus = parsed.get("primary business focus", "")
    # NOTE: Sectors are intentionally not emitted in the final description output.
    # We still parse them above in case other parts of the pipeline need them later.
    primary_sectors = parsed.get("primary sectors", "")
    secondary_sectors = parsed.get("secondary sectors", "")

    # Combine verified details and source text for context
    combined_context = f"""
Verified Company Information:
{company_details if company_details else ''}

Additional Context:
{text[:6000]}
"""
    prompt = f"""You are Company Profile Writer v2. You produce professional, neutral, fact-based company profiles that are highly useful for analysts.

RULES:
- Write in an objective tone, avoiding marketing language, flowery adjectives, or adverbs.
- NEVER use generic non-factual words: 'significant', 'important', 'best', 'leading', 'cutting-edge', 'innovative'.
- Provide SPECIFIC factual details where available (e.g., funding rounds and investors: names, amounts, rounds).
- Do NOT invent data. If a field is unknown, write "Unknown" (or "None" where appropriate).
- Do NOT include promotional sentences like "More information can be found on their website".
- Do NOT mention the company website URL in the description.
- Prefer concrete nouns over vague claims (e.g., "credit bureau data", "shipping telemetry", "hospital EHR analytics").

OUTPUT FORMAT (MUST follow exactly; 6 lines; no extra lines):
Snapshot: <1 sentence: what the company is/does, include year+HQ if known>
What they do: <2–3 clauses on core offering; be specific about the job-to-be-done>
Products/services: <1 sentence naming key products/services or product categories; include key data types if relevant>
Customers & markets: <who buys/uses it + typical industries + geography if known>
Business model & distribution: <how it makes money + delivery channels (SaaS, API, licenses, services, etc.) if known>
Ownership & key events: <ownership status/parent + notable funding/acquisitions/disposals with dates/amounts if known>

OWNERSHIP RULES:
- If PE-backed: specify the PE firm and when sponsorship occurred.
- If VC-funded: list only the last officially confirmed round and main investors.
- If no disclosed venture/PE backing: simply state "private" - do NOT mention lack of backing.
- If public: state the exchange.

FOR DATA/ANALYTICS PROVIDERS:
- Describe data types (e.g., residential vs commercial real estate, financial instruments, etc.)
- Specify distinct products if 2 or more exist.
- Reader must understand what dataset is at the core of the offering.

VERIFIED FIELDS (use these first; do NOT contradict them):
- Company: {company_name}
- Primary business focus: {primary_business_focus or "Unknown"}
- Year founded: {year_founded or "Unknown"}
- Headquarters: {headquarters or "Unknown"}
- CEO: {ceo or "Unknown"}
- Ownership status: {ownership_status or "Unknown"}

Now generate the company profile using ONLY the verified information below and any reliable facts present in the additional context. Do NOT invent data.

{combined_context}
"""
    result = openrouter_chat("openai/gpt-4o-mini", prompt, "Company Profile Writer v2")
    # Validate the description
    if not result or len(result.strip()) < 40:
        return "❌ No factual description could be generated."
    # Clean up - remove any trailing website mentions
    result = result.strip()
    # Remove common trailing patterns about websites
    import re
    result = re.sub(r'\s*(For more information|More information|Visit|Learn more)[^.]*\.?\s*$', '', result, flags=re.IGNORECASE)
    result = re.sub(r'\s*\(?https?://[^\s\)]+\)?\s*\.?\s*$', '', result)
    # Normalize to the expected 6-line format (robust against model drift).
    expected_labels = [
        "Snapshot:",
        "What they do:",
        "Products/services:",
        "Customers & markets:",
        "Business model & distribution:",
        "Ownership & key events:",
    ]

    def _starts_with_label(line: str, label: str) -> bool:
        return (line or "").strip().lower().startswith(label.lower())

    raw_lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
    picked = {}
    for ln in raw_lines:
        for label in expected_labels:
            if _starts_with_label(ln, label) and label not in picked:
                picked[label] = ln
                break

    # If the model returned already-unlabeled lines, assume they are in order.
    if not picked:
        if len(raw_lines) >= len(expected_labels):
            for i, label in enumerate(expected_labels):
                picked[label] = f"{label} {raw_lines[i]}".strip()

    # Deterministic fallbacks for missing required lines.
    if "Snapshot:" not in picked:
        parts = []
        if primary_business_focus and primary_business_focus.strip() and primary_business_focus.strip().lower() not in ["unknown", "n/a"]:
            parts.append(f"{company_name} is a {primary_business_focus.strip()} company")
        else:
            parts.append(f"{company_name} is a company")
        if year_founded and year_founded.strip().lower() not in ["unknown", "n/a"]:
            parts.append(f"founded in {year_founded.strip()}")
        if headquarters and headquarters.strip().lower() not in ["unknown", "n/a"]:
            parts.append(f"headquartered in {headquarters.strip()}")
        picked["Snapshot:"] = "Snapshot: " + ", ".join(parts) + "."

    if "What they do:" not in picked:
        picked["What they do:"] = "What they do: Unknown"
    if "Products/services:" not in picked:
        picked["Products/services:"] = "Products/services: Unknown"
    if "Customers & markets:" not in picked:
        picked["Customers & markets:"] = "Customers & markets: Unknown"
    if "Business model & distribution:" not in picked:
        picked["Business model & distribution:"] = "Business model & distribution: Unknown"
    if "Ownership & key events:" not in picked:
        ownership_txt = ownership_status.strip() if ownership_status else "Unknown"
        picked["Ownership & key events:"] = f"Ownership & key events: {ownership_txt}"

    # Final output should be plain text (no bullet/label prefixes).
    out_lines = []
    for label in expected_labels:
        ln = picked.get(label, "").strip()
        if _starts_with_label(ln, label):
            ln = ln[len(label):].strip()
        out_lines.append(ln or "Unknown")

    return " ".join(out_lines).strip()

# ============================================================
# 🔹 Subsidiary Data Generator
# ============================================================
def get_wikipedia_subsidiaries(company_name: str):
    """
    Attempts to extract subsidiaries directly from the company's Wikipedia page.
    Returns a list of subsidiary names if available.
    """
    try:
        url = f"https://en.wikipedia.org/wiki/{company_name.replace(' ', '_')}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        subsidiaries = set()

        # 1️⃣ Try infobox section
        for row in soup.select("table.infobox tr"):
            header = row.find("th")
            if header and "Subsidiaries" in header.text:
                links = row.find_all("a")
                for link in links:
                    text = link.get_text(strip=True)
                    if text and not text.startswith(("http", "#")):
                        subsidiaries.add(text)

        # 2️⃣ Try separate "Subsidiaries" headings
        for h2 in soup.find_all("h2"):
            if "Subsidiaries" in h2.get_text():
                ul = h2.find_next("ul")
                if ul:
                    for li in ul.find_all("li"):
                        text = li.get_text(strip=True)
                        if text:
                            subsidiaries.add(text)

        return list(subsidiaries)
    except Exception as e:
        print(f"⚠️ Wikipedia subsidiary fetch failed: {e}")
        return []


def generate_subsidiary_data(company_name: str, company_description: str = ""):
    """
    Fetches accurate current subsidiaries of a company using Wikipedia + SerpAPI + AI enrichment.
    Stores full description (no truncation).
    """
    print(f"🏢 Generating enriched subsidiary data for: {company_name}")
    subsidiaries = []

    # Step 1️⃣: Wikipedia first
    wiki_subs = get_wikipedia_subsidiaries(company_name)
    if wiki_subs:
        print(f"✅ Found {len(wiki_subs)} subsidiaries from Wikipedia: {wiki_subs[:8]}")

    # Step 2️⃣: Gather broader context via SerpAPI
    query = f"{company_name} subsidiaries OR child companies site:linkedin.com OR site:crunchbase.com OR site:craft.co OR site:wikipedia.org"
    serp_results = []
    try:
        params = {"q": query, "hl": "en", "gl": "us", "num": 30, "api_key": SERPAPI_KEY}
        search = GoogleSearch(params)
        serp_data = search.get_dict().get("organic_results", [])
        serp_results = [r.get("link") for r in serp_data if r.get("link")]
        print(f"✅ Found {len(serp_results)} possible subsidiary links from SerpAPI.")
    except Exception as e:
        print(f"⚠️ SerpAPI subsidiary fetch failed: {e}")

    # Step 3️⃣: AI enrichment with Wikipedia + Serp context
    serp_context = "\n".join(serp_results[:20])
    prompt = f"""
You are a professional corporate researcher.

TASK:
Using the Wikipedia list and online context, produce a structured JSON array of **current subsidiaries** of "{company_name}".
Each subsidiary object must contain:
- name
- url
- description
- sector
- linkedin_members
- country
- logo (use company favicon URL if possible)

Wikipedia subsidiaries:
{wiki_subs}

Additional links:
{serp_context}

Return ONLY valid JSON array (no text, no comments).
"""

    ai_response = openrouter_chat("anthropic/claude-3.5-sonnet", prompt, "Subsidiaries Extractor")

    try:
        match = re.search(r'\[.*\]', ai_response, re.S)
        if match:
            subsidiaries = json.loads(match.group(0))
            print(f"✅ Extracted {len(subsidiaries)} subsidiaries from AI model.")
    except Exception as e:
        print(f"⚠️ AI subsidiary JSON parse error: {e}")
        return []

    # Step 4️⃣: Logo guarantee + data cleaning
    def get_favicon(url):
        try:
            domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            return f"https://www.google.com/s2/favicons?sz=64&domain_url={domain}"
        except Exception:
            return "https://www.google.com/s2/favicons?sz=64&domain_url=google.com"

    for sub in subsidiaries:
        # --- Ensure logo always exists ---
        url = sub.get("url", "")
        if url and not url.startswith("http"):
            url = "https://" + url
        sub["url"] = url

        # ✅ Try fetching a real logo from Google first
        if not sub.get("logo"):
            sub["logo"] = fetch_logo_free(sub.get("name") or sub.get("url") or company_name)




        if not isinstance(sub.get("linkedin_members"), int):
            try:
                sub["linkedin_members"] = int(re.sub(r"\D", "", str(sub["linkedin_members"]))) if sub.get("linkedin_members") else 0
            except:
                sub["linkedin_members"] = 0

        sub["description"] = sub.get("description", "").strip()

        # ✅ Store using list-based DB interface
        try:
            store_subsidiaries(company_name, [sub])
        except Exception as db_err:
            print(f"⚠️ Database store error for {sub.get('name')}: {db_err}")

    return subsidiaries