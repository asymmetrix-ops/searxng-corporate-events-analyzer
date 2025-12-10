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
                            "link": url
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
    Searches for a person's LinkedIn profile URL using REAL web search (Startpage).
    Query: "company name" + "person name" + linkedin
    Picks the first LinkedIn profile URL from search results.
    Falls back to AI if real search fails.
    """
    if not name:
        return ""
    
    # Clean the name
    clean_name = re.sub(r'\b(Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Jr\.?|Sr\.?|III|II|IV)\b', '', name, flags=re.I).strip()
    
    # Query format: "company" "name" linkedin - as user suggested
    query1 = f'{company_name} {clean_name} linkedin'
    url = search_linkedin_startpage(query1)
    if url:
        return url
    
    # Alternative: name + position + linkedin  
    query2 = f'{clean_name} {position} linkedin'
    url = search_linkedin_startpage(query2)
    if url:
        return url
    
    # Last resort: AI fallback
    prompt = f"""LinkedIn URL for {clean_name} {position} at {company_name}.
Return ONLY the URL. Format: https://www.linkedin.com/in/username
If unknown, reply: UNKNOWN"""

    try:
        response = openrouter_chat("anthropic/claude-3.5-sonnet", prompt, f"LinkedIn-{clean_name}")
        if response and "linkedin.com/in/" in response.lower() and "unknown" not in response.lower():
            match = re.search(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)', response, re.I)
            if match:
                return f"https://www.linkedin.com/in/{match.group(1)}"
    except:
        pass
    
    return ""


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
    
    # Search queries ordered by specificity
    search_queries = [
        f'"{clean_name}" official website',
        f'{clean_name} company website',
        f'"{company_name}"',
    ]
    
    for query in search_queries:
        try:
            params = {"q": query, "num": 10, "api_key": SERPAPI_KEY}
            search = GoogleSearch(params)
            result = search.get_dict()
            
            if "error" in result:
                print(f"   ⚠️ SerpAPI website search error: {result['error']}")
                continue
            
            results = result.get("organic_results", [])
            
            for r in results:
                link = r.get("link", "")
                title = r.get("title", "").lower()
                snippet = r.get("snippet", "").lower()
                
                # Skip social media, directories, news sites
                skip_domains = ['linkedin.com', 'facebook.com', 'twitter.com', 'x.com', 
                               'crunchbase.com', 'bloomberg.com', 'reuters.com', 'wikipedia.org',
                               'zoominfo.com', 'dnb.com', 'glassdoor.com', 'indeed.com',
                               'yelp.com', 'yellowpages.com', 'bbb.org', 'manta.com']
                if any(domain in link.lower() for domain in skip_domains):
                    continue
                
                # Check if company name appears in title or domain
                name_lower = clean_name.lower()
                name_words = set(name_lower.split())
                
                # Extract domain from URL
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(link)
                    domain = parsed.netloc.lower().replace('www.', '')
                    domain_name = domain.split('.')[0] if '.' in domain else domain
                except:
                    domain = ""
                    domain_name = ""
                
                # Match criteria: name in title, or name words in domain
                name_in_title = name_lower in title or any(word in title for word in name_words if len(word) > 3)
                name_in_domain = any(word in domain_name for word in name_words if len(word) > 3)
                
                if name_in_title or name_in_domain:
                    # Normalize URL
                    if not link.startswith('http'):
                        link = f"https://{link}"
                    print(f"   🌐 Found website for {company_name}: {link}")
                    return link
                    
        except Exception as e:
            print(f"   ⚠️ SerpAPI website search error: {e}")
            continue
    
    return ""


def search_company_linkedin(company_name: str, website_url: str = "") -> str:
    """
    Search for a COMPANY's LinkedIn page using multiple queries via SerpAPI.
    More accurate than AI guessing - uses real search results.
    
    Args:
        company_name: The company name to search for
        website_url: Optional company website to help narrow results
        
    Returns:
        LinkedIn company URL if found, empty string otherwise
    """
    if not company_name and not website_url:
        return ""
    
    serpapi_available = bool(SERPAPI_KEY)

    # Clean company name - remove common suffixes for better matching
    clean_name = (company_name or "").strip()
    for suffix in [', Inc.', ', Inc', ' Inc.', ' Inc', ', LLC', ' LLC', ', Ltd.', ', Ltd', ' Ltd.', ' Ltd',
                   ', Corp.', ', Corp', ' Corp.', ' Corp', ', Limited', ' Limited', ', Co.', ' Co.']:
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)].strip()
    
    # Extract domain from website for additional matching
    domain_hint = ""
    url_for_domain = website_url or ""
    # If the company name itself is a URL, use it to extract domain
    if not url_for_domain and clean_name.startswith("http"):
        url_for_domain = clean_name
    if url_for_domain:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url_for_domain)
            domain = parsed.netloc.replace("www.", "")
            domain = domain.split('.')[0] if '.' in domain else domain
            if domain and len(domain) > 2:
                domain_hint = domain
        except:
            pass
    
    # Multiple search strategies - ordered by expected accuracy (SerpAPI first)
    serpapi_queries = [
        f'"{clean_name}" site:linkedin.com/company/',
        f'"{clean_name}" {domain_hint} site:linkedin.com/company/' if domain_hint else None,
        f'{clean_name} linkedin company page',
        f'"{company_name}" linkedin',
    ]
    serpapi_queries = [q for q in serpapi_queries if q and serpapi_available]
    
    found_urls = []
    
    for query in serpapi_queries:
        try:
            params = {"q": query, "num": 10, "api_key": SERPAPI_KEY}
            search = GoogleSearch(params)
            result = search.get_dict()
            
            if "error" in result:
                print(f"   ⚠️ SerpAPI company LinkedIn search error: {result['error']}")
                continue
            
            results = result.get("organic_results", [])
            for r in results:
                link = r.get("link", "")
                title = r.get("title", "").lower()
                
                if "linkedin.com/company/" not in link:
                    continue
                
                match = re.search(r'linkedin\.com/company/([a-zA-Z0-9_-]+)', link)
                if not match:
                    continue
                
                company_slug = match.group(1).lower()
                
                if company_slug in ['company', 'jobs', 'pulse', 'learning', 'about']:
                    continue
                
                clean_name_lower = clean_name.lower()
                name_words = set(clean_name_lower.split())
                slug_words = set(company_slug.replace('-', ' ').replace('_', ' ').split())
                
                name_in_title = clean_name_lower in title
                name_in_slug = any(word in company_slug for word in name_words if len(word) > 2)
                slug_matches_name = len(name_words & slug_words) >= 1
                
                if name_in_title or name_in_slug or slug_matches_name:
                    normalized_url = f"https://www.linkedin.com/company/{company_slug}/"
                    if normalized_url not in found_urls:
                        found_urls.append(normalized_url)
                        print(f"   🔗 Found LinkedIn for {company_name}: {normalized_url}")
                        return normalized_url
                        
        except Exception as e:
            print(f"   ⚠️ SerpAPI company search error: {e}")
            continue
    
    # Fallback: real web search (Startpage) using domain + linkedin as suggested
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
    
    for query in fallback_queries:
        url = search_company_linkedin_startpage(query, clean_name=clean_name, domain_hint=domain_hint)
        if url:
            print(f"   🔗 Startpage LinkedIn for {company_name}: {url}")
            return url
    
    # If we found any URLs but none were high confidence, return the first one
    if found_urls:
        return found_urls[0]
    
    return ""


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

    # Search for company leadership info
    context_queries = [
        f'"{company_name}" leadership team CEO CFO executives',
        f'"{company_name}" management team board directors',
        f'{company_name} CEO "chief executive" OR CFO OR CTO',
    ]
    
    for query in context_queries:
        try:
            params = {
                "q": query,
                "num": 10,
                "api_key": os.getenv("SERPAPI_KEY"),
            }
            search = GoogleSearch(params)
            results = search.get_dict().get("organic_results", [])
            context_snippets = " ".join([r.get("snippet", "") for r in results if r.get("snippet")])
            text += "\n\n" + context_snippets
        except Exception as e:
            print(f"⚠️ Context search failed: {e}")

    # =====================================================
    # 2️⃣ AI Extraction - Get Names First
    # =====================================================
    prompt = f"""
You are a corporate research analyst. Research and extract the COMPLETE current executive leadership team for "{company_name}".

IMPORTANT: You MUST include ALL of these roles if they exist:
- CEO (Chief Executive Officer) - REQUIRED
- CFO (Chief Financial Officer)
- COO (Chief Operating Officer)  
- CTO (Chief Technology Officer)
- CMO (Chief Marketing Officer)
- CLO/General Counsel (Chief Legal Officer)
- CHRO/CPO (Chief Human Resources/People Officer)
- President / Managing Director
- Chairman of the Board
- Other C-suite or Senior VP roles

For EACH executive, provide:
- name: Full legal name
- position: Official title
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
- bio: Professional executive summary (3-5 sentences) in THIS EXACT STYLE:

EXAMPLE BIO STYLE:
"Experienced CEO and Board Director with successful track record in Technology and Financial Services. 20+ years in decision making roles for Private Equity and Corporates. Skilled in General Management, Transformational Leadership, Strategy Development and M&A. International profile having operated out of US, UK and EMEA. MBA from Harvard Business School."

BIO REQUIREMENTS:
- Write in third person, professional tone
- Start with current/past leadership roles and experience
- Include industry expertise and sectors
- Mention key skills and competencies
- Note international experience if applicable
- End with education background (degrees, universities)
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
List the **current top management** (CEO, CFO, CTO, etc.) of {company_name}.

For each person provide:
- name: Full name
- position: Official title
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

    # =====================================================
    # 4️⃣ Search LinkedIn for EACH Executive Individually
    # =====================================================
    print(f"🔗 Searching LinkedIn profiles for {len(management_results)} executives...")
    
    for m in management_results:
        name = m.get("name", "")
        position = m.get("position", "")
        
        if name:
            # Search for this person's LinkedIn profile
            linkedin_url = search_linkedin_profile(name, company_name, position)
            
            if linkedin_url:
                m["linkedin_url"] = linkedin_url
                print(f"   ✅ Found LinkedIn for {name}")
            else:
                m["linkedin_url"] = ""
                print(f"   ⚠️ No LinkedIn found for {name}")

    # =====================================================
    # 5️⃣ Clean & Deduplicate
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
        position = m.get("position", "").strip()
        status = m.get("status", "Current").capitalize()
        linkedin_url = m.get("linkedin_url", "").strip()
        location = m.get("location", "").strip()
        bio = m.get("bio", "").strip()
        current_employee_url = m.get("current_employee_url", "").strip()
        
        if not name or not position:
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
    Second-pass enrichment: Uses Perplexity to find individuals and announcement URLs for each deal.
    
    Args:
        events: List of corporate events with counterparties
        main_company: The main company being researched
        
    Returns:
        Enriched events with individuals and press release URLs added to counterparties
    """
    import os, json, requests
    
    OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPEN_ROUTER_KEY")
    if not OPENROUTER_KEY:
        print("   ⚠️ No OpenRouter key for individual enrichment")
        return events
    
    # Enrich ALL events (no limit)
    total_events = len(events)
    
    for idx, event in enumerate(events):
        event_short = event.get("Event (short)", "")
        announcement_date = event.get("Announcement Date", "")
        closed_date = event.get("Closed Date", "")
        counterparties = event.get("counterparties", [])
        
        if not counterparties:
            continue
            
        # Build query for this deal
        cp_names = [cp.get("company_name", "") for cp in counterparties if cp.get("company_name")]
        if len(cp_names) < 1:
            print(f"         → Skipping: no counterparty names found")
            continue
        
        # Build date context
        date_context = ""
        if announcement_date:
            date_context += f"Announced: {announcement_date}"
        if closed_date:
            date_context += f", Closed: {closed_date}" if date_context else f"Closed: {closed_date}"
        if not date_context:
            date_context = "Date unknown"
            
        print(f"      [{idx+1}/{total_events}] Enriching: {event_short[:50]}...")
            
        query = f"""For the corporate deal: "{event_short}"
Date: {date_context}
Companies involved: {', '.join(cp_names)}

Find for EACH company involved:

1. **KEY EXECUTIVES** involved in this deal:
   - CEO, President, or Managing Director
   - CFO or deal leads
   - Executives quoted in press releases
   - PE firm Partners (if applicable)

2. **ANNOUNCEMENT URL** - each company's own press release or announcement about this deal

Return JSON with this EXACT format:
{{
  "counterparties": [
    {{
      "company": "Company Name",
      "press_release_url": "https://company.com/news/deal-announcement",
      "company_linkedin_url": "https://www.linkedin.com/company/company-name/",
      "individuals": [
        {{"name": "Full Name", "title": "Title at Company", "linkedin_url": ""}},
        {{"name": "Full Name", "title": "Title at Company", "linkedin_url": ""}}
      ]
    }}
  ]
}}

Return ONLY valid JSON, no other text. Include ALL companies from the deal."""

        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "perplexity/sonar-pro",
                    "messages": [{"role": "user", "content": query}],
                    "temperature": 0.1,
                    "max_tokens": 2000
                },
                timeout=45
            )
            
            if response.status_code == 200:
                raw = response.json()["choices"][0]["message"]["content"].strip()
                
                # Extract JSON (could be object or array)
                start_obj = raw.find('{')
                end_obj = raw.rfind('}') + 1
                start_arr = raw.find('[')
                end_arr = raw.rfind(']') + 1
                
                enrichment_data = None
                
                # Try object format first
                if start_obj != -1 and end_obj > start_obj:
                    try:
                        enrichment_data = json.loads(raw[start_obj:end_obj])
                        if "counterparties" in enrichment_data:
                            enrichment_data = enrichment_data["counterparties"]
                    except:
                        pass
                
                # Try array format
                if not enrichment_data and start_arr != -1 and end_arr > start_arr:
                    try:
                        enrichment_data = json.loads(raw[start_arr:end_arr])
                    except:
                        pass
                
                if enrichment_data and isinstance(enrichment_data, list):
                    print(f"         → Found {len(enrichment_data)} companies in enrichment response")
                    # Map enrichment data to counterparties
                    for enrich_cp in enrichment_data:
                        enrich_company = enrich_cp.get("company", "").lower()
                        enrich_url = enrich_cp.get("press_release_url", "")
                        enrich_linkedin = enrich_cp.get("company_linkedin_url", "")
                        enrich_individuals = enrich_cp.get("individuals", [])
                        print(f"         → {enrich_company}: {len(enrich_individuals)} individuals found")
                        
                        # Find matching counterparty
                        for cp in counterparties:
                            cp_name = cp.get("company_name", "").lower()
                            if enrich_company in cp_name or cp_name in enrich_company or \
                               any(word in cp_name for word in enrich_company.split() if len(word) > 3):
                                
                                # Update press release URL
                                if enrich_url and not cp.get("press_release_url"):
                                    cp["press_release_url"] = enrich_url
                                
                                # Update LinkedIn URL
                                if enrich_linkedin and not cp.get("company_linkedin_url"):
                                    cp["company_linkedin_url"] = enrich_linkedin
                                
                                # Add individuals
                                if "individuals" not in cp:
                                    cp["individuals"] = []
                                    
                                existing_names = [i.get("name", "").lower() for i in cp["individuals"]]
                                for ind in enrich_individuals:
                                    ind_name = ind.get("name", "")
                                    if ind_name and ind_name.lower() not in existing_names:
                                        cp["individuals"].append({
                                            "name": ind_name,
                                            "title": ind.get("title", ""),
                                            "linkedin_url": ind.get("linkedin_url", "")
                                        })
                                        existing_names.append(ind_name.lower())
                                break
                                
        except Exception as e:
            print(f"      ⚠️ Enrichment failed for '{event_short[:30]}...': {e}")
            continue
    
    # =====================================================
    # Post-process: Find Website URLs and verify LinkedIn URLs
    # AI often hallucinates URLs, so we verify/find with SerpAPI
    # =====================================================
    print(f"\n🔍 Finding counterparty website & LinkedIn URLs via search...")
    for event in events:
        counterparties = event.get("counterparties", [])
        for cp in counterparties:
            cp_name = cp.get("company_name", "")
            cp_website = cp.get("company_website", "") or cp.get("company_url", "") or cp.get("website", "")
            existing_linkedin = cp.get("company_linkedin_url", "")
            
            if not cp_name:
                continue
            
            # ==========================================
            # 1) Find Website URL if missing
            # ==========================================
            if not cp_website:
                print(f"   → Searching website for: {cp_name}...")
                found_website = search_company_website(cp_name)
                if found_website:
                    cp["company_website"] = found_website
                    cp["company_url"] = found_website
                    cp["website"] = found_website
                    cp_website = found_website  # Use for LinkedIn search below
                else:
                    print(f"   ⚠️ No website found for {cp_name}")
            
            # ==========================================
            # 2) Find/Verify LinkedIn URL
            # ==========================================
            needs_linkedin_search = False
            if not existing_linkedin:
                needs_linkedin_search = True
            elif existing_linkedin:
                # Check if the URL looks like it might be AI-generated (generic pattern)
                # e.g., "linkedin.com/company/company-name" when name is "Company Name Inc."
                slug = existing_linkedin.split('/company/')[-1].rstrip('/').lower() if '/company/' in existing_linkedin else ""
                name_slug = cp_name.lower().replace(' ', '-').replace(',', '').replace('.', '').replace("'", '')
                # If slug is suspiciously similar to a direct name conversion, verify it
                if slug and (slug == name_slug or slug.replace('-', '') == name_slug.replace('-', '')):
                    needs_linkedin_search = True
            
            if needs_linkedin_search:
                print(f"   → Searching LinkedIn for: {cp_name}...")
                real_linkedin = search_company_linkedin(cp_name, cp_website)
                if real_linkedin:
                    if existing_linkedin and existing_linkedin != real_linkedin:
                        print(f"   ✅ Corrected LinkedIn: {existing_linkedin} → {real_linkedin}")
                    else:
                        print(f"   ✅ Found LinkedIn: {real_linkedin}")
                    cp["company_linkedin_url"] = real_linkedin
                elif not existing_linkedin:
                    print(f"   ⚠️ No LinkedIn found for {cp_name}")
            
            # ==========================================
            # 3) Find LinkedIn URLs for individuals in this counterparty
            # ==========================================
            individuals = cp.get("individuals", [])
            if individuals:
                print(f"   👤 Searching LinkedIn for {len(individuals)} individuals at {cp_name}...")
                for ind in individuals:
                    ind_name = ind.get("name", "")
                    ind_title = ind.get("title", "")
                    existing_ind_linkedin = ind.get("linkedin_url", "")
                    
                    if not ind_name:
                        continue
                    
                    # Skip if already has a valid LinkedIn URL
                    if existing_ind_linkedin and "linkedin.com/in/" in existing_ind_linkedin:
                        continue
                    
                    # Search for this person's LinkedIn profile
                    person_linkedin = search_linkedin_profile(ind_name, cp_name, ind_title)
                    if person_linkedin:
                        ind["linkedin_url"] = person_linkedin
                        print(f"      ✅ Found LinkedIn for {ind_name}: {person_linkedin}")
                    else:
                        print(f"      ⚠️ No LinkedIn found for {ind_name}")
    
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
        # Quick search to understand company profile
        queries = [
            f'"{company_name}" founded startup',
            f'"{company_name}" series funding OR seed round OR accelerator',
            f'"{company_name}" site:crunchbase.com OR site:linkedin.com/company'
        ]
        
        all_snippets = ""
        for q in queries:
            try:
                params = {"q": q, "num": 5, "api_key": serpapi_key}
                results = GoogleSearch(params).get_dict().get("organic_results", [])
                for r in results:
                    all_snippets += f" {r.get('title', '')} {r.get('snippet', '')}"
            except:
                pass
        
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
            # If unclear, check if we found ANY M&A history
            if "acquired" in all_snippets_lower or "acquisition" in all_snippets_lower:
                result["company_type"] = "enterprise"
            else:
                # Default to startup-friendly search for unknown companies
                result["is_small_company"] = True
                result["company_type"] = "small_company"
        
        print(f"   📊 Company type: {result['company_type']} (startup_score={startup_score}, enterprise_score={enterprise_score})")
        if result["founded_year"]:
            print(f"   📅 Founded: {result['founded_year']}")
            
    except Exception as e:
        print(f"   ⚠️ Company type detection error: {e}")
    
    return result


def generate_corporate_events(company_name: str, max_events: int = 20) -> list:
    """
    Fetches and extracts corporate M&A events for a company using web search and LLM.
    Automatically detects if company is a startup and adjusts search strategy accordingly.
    
    OPTIMIZED: Uses parallel SerpAPI queries for ~5x faster search.
    
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

    # ========================================
    # 🚀 PARALLEL SEARCH EXECUTION
    # ========================================
    start_time = time.time()
    print(f"   → Running {len(queries)} search queries in PARALLEL...")
    
    search_results = serpapi_parallel_search(queries, SERPAPI_KEY, num_results=15)
    
    elapsed = time.time() - start_time
    print(f"   ✅ Parallel search completed in {elapsed:.1f}s ({len(search_results)} unique results)")
    
    print(f"   → Collected {len(search_results)} unique search results")

    if not search_results:
        print("❌ No search results found")
        return []

    # Format context - use all results for comprehensive coverage
    context = ""
    results_to_analyze = len(search_results)
    for i, result in enumerate(search_results, 1):
        context += f"[{i}] {result['title']}\n{result['snippet']}\nSource: {result['link']}\n\n"

    print(f"   → Sending {results_to_analyze} results to AI for extraction...")
    
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

    prompt = f'''Extract ALL corporate events for "{company_name}" from the {results_to_analyze} search results below.

YOUR GOAL: Find and return up to {max_events} UNIQUE corporate events including M&A, funding, grants, accelerators, and partnerships.

{extraction_checklist}

EXTRACTION RULES:
1. Each unique target company = separate event (even if small deal)
2. Investments INTO the company = also events (VC/PE firm invests in company)
3. Funding rounds ARE corporate events (Series A, Seed round, etc.)
4. Partnerships and JVs ARE corporate events
5. If date is unclear, use the article date or "Jan 1, [year]" 
6. Extract ALL deals - do not filter by size or importance

OUTPUT: Return exactly {max_events} events if that many exist in the search results.

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
   - ONLY use dates EXPLICITLY stated in the search results
   - Look for phrases like "announced on [date]", "completed [date]", "dated [date]"
   - If article mentions year but not exact date, check the article date/URL for clues
   - If ONLY year is known: use "Jan 1, YYYY" and add "(approximate)" to event description
   - NEVER guess dates - if unsure, use article publication date with "(announced)" note
   - Cross-reference dates across multiple sources when possible
   - Prefer announcement_date if only one date is available

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
   - individuals: Array of key people mentioned in press releases/articles for this deal
     For each individual include:
     - name: Full name (e.g., "Peter Berweger")
     - title: Role at the company (e.g., "CEO", "CFO", "Managing Director")
     - linkedin_url: "" (leave empty for now)
     Look for: CEOs, CFOs, deal leads, executives quoted in press releases about this transaction

11. **advisors**: Array of professional advisory firms that advised on this transaction.
   
   ADVISOR TYPES:
   - Financial advisors (investment banks): Goldman Sachs, Morgan Stanley, JP Morgan, Lazard, Evercore, Centerview Partners, PJT Partners, Rothschild, Moelis, Jefferies, etc.
   - Legal advisors (law firms): Skadden, Sullivan & Cromwell, Wachtell Lipton, Kirkland & Ellis, Simpson Thacher, Latham & Watkins, Davis Polk, Freshfields, Clifford Chance, etc.
   - Consulting/Due Diligence: McKinsey, BCG, Bain, Deloitte, EY, KPMG, PwC, etc.
   
   For EACH advisor include:
   - advisor_name: Exact name of the advisory firm (REQUIRED)
   - advisor_type: "Financial Advisor" | "Legal Advisor" | "Due Diligence" | "Tax Advisor" | "Other"
   - advised_party: Which counterparty they advised (e.g., "S&P Global", "Target", "Buyer")
   - announcement_url: URL where this advisor relationship was mentioned, if available
   
   ADVISOR EXTRACTION TIPS:
   - Look for phrases like "advised by", "financial advisor to", "legal counsel to", "represented by"
   - Investment banks often advise on deal terms, valuation, and negotiations
   - Law firms handle legal documentation, regulatory filings, due diligence
   - If no advisors mentioned, use empty array []

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
{context}

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
    "counterparties": [
      {{"company_name": "S&P Global", "type_id": 18, "type": "Acquirer", "role_description": "Acquiring company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Douglas Peterson", "title": "President & CEO", "linkedin_url": ""}}]}},
      {{"company_name": "IHS Markit", "type_id": 17, "type": "Target", "role_description": "Target company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Lance Uggla", "title": "Chairman & CEO", "linkedin_url": ""}}]}}
    ],
    "advisors": [
      {{"advisor_name": "Goldman Sachs", "advisor_type": "Financial Advisor", "advised_party": "S&P Global", "announcement_url": ""}},
      {{"advisor_name": "Davis Polk & Wardwell", "advisor_type": "Legal Advisor", "advised_party": "S&P Global", "announcement_url": ""}}
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
    "counterparties": [
      {{"company_name": "S&P Global", "type_id": 18, "type": "Acquirer", "role_description": "Acquiring company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Martina Cheung", "title": "President, S&P Global Market Intelligence", "linkedin_url": ""}}]}},
      {{"company_name": "Visible Alpha", "type_id": 17, "type": "Target", "role_description": "Target company", "company_linkedin_url": "", "press_release_url": "", "individuals": [{{"name": "Scott Ryles", "title": "CEO", "linkedin_url": ""}}]}}
    ],
    "advisors": [
      {{"advisor_name": "Jefferies", "advisor_type": "Financial Advisor", "advised_party": "Visible Alpha", "announcement_url": ""}}
    ]
  }}
]

JSON:'''

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
            json={
                "model": "anthropic/claude-3.5-sonnet:beta",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,  # Slightly increased for more creative extraction
                "max_tokens": 32000  # Doubled to allow more events with enhanced counterparty data
            },
            timeout=180
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"].strip()

        # Extract JSON
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start == -1 or end == 0:
            print("No JSON found in response")
            return []

        events = json.loads(raw[start:end])

        # Transform to match your table structure with counterparties
        result = []
        for i, e in enumerate(events[:max_events], 1):
            # Process counterparties with enhanced fields
            counterparties = []
            raw_counterparties = e.get("counterparties", [])
            for cp in raw_counterparties:
                # Process individuals for this counterparty
                individuals = []
                raw_individuals = cp.get("individuals", [])
                for ind in raw_individuals:
                    individuals.append({
                        "name": str(ind.get("name", "")).strip(),
                        "title": str(ind.get("title", "")).strip(),
                        "linkedin_url": str(ind.get("linkedin_url", "")).strip()
                    })
                
                counterparties.append({
                    "company_name": str(cp.get("company_name", "")).strip(),
                    "type_id": int(cp.get("type_id", 0)),
                    "type": str(cp.get("type", "Unknown")).strip(),
                    "role_description": str(cp.get("role_description", "")).strip(),
                    "company_linkedin_url": str(cp.get("company_linkedin_url", "")).strip(),
                    "press_release_url": str(cp.get("press_release_url", "")).strip(),
                    "individuals": individuals
                })
            
            # Use announcement_date as primary, fall back to date field for backwards compatibility
            announcement_date = str(e.get("announcement_date", e.get("date", ""))).strip()
            closed_date = str(e.get("closed_date", "")).strip()
            
            # For display, prefer announcement_date, then closed_date
            display_date = announcement_date if announcement_date else closed_date
            if not display_date:
                display_date = "Unknown"
            
            # Extract deal type and status (new fields)
            deal_type = str(e.get("deal_type", e.get("event_type", "Unknown"))).strip()
            deal_status = str(e.get("deal_status", "")).strip()
            # Auto-set status to Completed if closed_date exists and no status provided
            if not deal_status and closed_date:
                deal_status = "Completed"
            elif not deal_status:
                deal_status = "Unknown"
            
            # Extract advisors
            advisors = []
            for adv in e.get("advisors", []):
                if adv and isinstance(adv, dict):
                    advisors.append({
                        "advisor_name": str(adv.get("advisor_name", "")).strip(),
                        "advisor_type": str(adv.get("advisor_type", "")).strip(),
                        "advised_party": str(adv.get("advised_party", "")).strip(),
                        "announcement_url": str(adv.get("announcement_url", "")).strip()
                    })
            
            result.append({
                "Announcement Date": announcement_date,
                "Closed Date": closed_date,
                "Date": display_date,  # Keep for backwards compatibility
                "Event (short)": str(e.get("event_short", e.get("event", "Unknown event"))).strip(),
                "Description": str(e.get("description", "")).strip(),
                "Deal Type": deal_type,
                "Deal Status": deal_status,
                "Event type": deal_type,  # Keep for backwards compatibility
                "Event value (USD)": str(e.get("value_usd", e.get("value", "Undisclosed"))).strip(),
                "Source URL": str(e.get("source_url", "")).strip(),
                "counterparties": counterparties,
                "advisors": advisors
            })

        print(f"SUCCESS: {len(result)} corporate events loaded for {company_name}")
        
        # SECOND PASS: Enrich counterparties with individuals using Perplexity
        print(f"   → Enriching counterparties with individuals...")
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

    serp_text = ""

    for q in queries:
        try:
            params = {
                "q": q,
                "num": 10,
                "hl": "en",
                "gl": "us",
                "api_key": SERPAPI_KEY
            }
            search = GoogleSearch(params).get_dict()
            results = search.get("organic_results", [])

            for r in results:
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                serp_text += f"{title}\n{snippet}\n\n"

        except Exception as e:
            print("⚠️ SERPAPI error:", e)

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
    # 1️⃣ SERPAPI Google Search Queries
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

    results_text = ""

    for q in queries:
        try:
            params = {
                "q": q,
                "num": 10,
                "hl": "en",
                "gl": "us",
                "api_key": SERPAPI_KEY,
            }

            search = GoogleSearch(params)
            res = search.get_dict().get("organic_results", [])

            for r in res:
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                results_text += f"{title}. {snippet}\n"
        except Exception as e:
            print(f"⚠️ SERPAPI CEO search failed: {e}")

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
def generate_summary(company_name, text=""):
    """
    Company summary where CEO is ALWAYS extracted using
    SERPAPI + strict AI CEO extractor (zero hallucination).
    Uses web search as fallback when Wikipedia has no data.
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
    
    # If Wikipedia has no useful data, use web search
    if not text.strip() or len(text) < 100:
        print(f"   → Wikipedia has no data for {search_name}, using web search...")
        # Search for company info from multiple sources
        search_queries = [
            f'"{search_name}" company about headquarters',
            f'"{search_name}" site:linkedin.com/company',
            f'"{search_name}" site:crunchbase.com',
            f'"{search_name}" founded CEO location',
            f'site:{website_from_input.replace("https://", "").replace("http://", "").rstrip("/")}' if website_from_input else f'"{search_name}" company',
        ]
        search_text = ""
        for q in search_queries:
            if q:  # Skip empty queries
                result = serpapi_search(q, num_results=5)
                if result:
                    search_text += result + "\n\n"
        if search_text.strip():
            text = search_text
            print(f"   → Collected {len(text)} chars of search data")
    
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
    
    # Step 1.5a: Try common press page URL patterns directly
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
        
        print(f"   → Checking common press page paths on {website_base}...")
        for path in common_press_paths:
            if press_page_url:
                break
            test_url = website_base + path
            try:
                if check_url_exists(test_url):
                    press_page_url = test_url
                    print(f"   ✅ Found press page: {press_page_url}")
                    break
            except:
                continue
    
    # Step 1.5b: If not found, search via SerpAPI
    if not press_page_url and domain_for_search:
        press_search_queries = [
            f'site:{domain_for_search} press OR newsroom OR "press releases"',
            f'site:{domain_for_search} news announcements',
        ]
        
        for pq in press_search_queries:
            if press_page_url:
                break
            try:
                import os
                serpapi_key = os.environ.get("SERPAPI_KEY", "")
                if serpapi_key:
                    resp = requests.get(
                        "https://serpapi.com/search",
                        params={"q": pq, "api_key": serpapi_key, "num": 5},
                        timeout=10
                    )
                    if resp.ok:
                        data = resp.json()
                        organic = data.get("organic_results", [])
                        for result in organic:
                            link = result.get("link", "")
                            # Check if it's a press/news page
                            press_keywords = ["/press", "/news", "/newsroom", "/media", "/announcements", "press-release", "/insights"]
                            if any(kw in link.lower() for kw in press_keywords):
                                # Validate the URL exists
                                if check_url_exists(link):
                                    press_page_url = link
                                    print(f"   ✅ Found press page via search: {press_page_url}")
                                    break
            except Exception as e:
                print(f"   → Press page search error: {e}")
    
    if press_page_url:
        print(f"   📰 Press page URL: {press_page_url}")
    else:
        print(f"   ⚠️ No press page found")

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
   - A *sector* is a broad category of the economy that groups together companies with similar business activities.
   - Examples: Technology, Healthcare, Financial Services, Consumer Goods, Industrials, Energy, Real Estate,
     Telecommunications, Utilities, Materials, Public Sector, Education, Non-Profit, Government, etc.
   - Primary Sectors = where the company generates most of its value / core business.
   - Secondary Sectors = adjacent areas or important but non-core activities.

8. Search your knowledge for this company if the source text is insufficient
9. If you truly cannot find a value, write "Unknown"

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
        return "❌ No details found."
    
    print(f"   → AI returned company info: {summary[:300]}...")
    summary = openrouter_chat(
        "openai/gpt-4o-mini",
        prompt,
        "Company Info Extractor"
    )

    if not summary:
        return "❌ No details found."

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

    return "\n".join(final_lines).strip()

# ============================================================
# 🔹 Company Description Generator
# ============================================================
def generate_description(company_name, text="", company_details=""):
    """
    Generates a professional, neutral, fact-based company description in a single paragraph.

    Args:
        company_name (str): The name of the company.
        text (str): Optional source text to extract description from.
        company_details (str): Optional verified company details to include in context.

    Returns:
        str: A single-paragraph company description, or an error message if generation fails.
    """
    # Use provided text or fetch from Wikipedia
    if not text.strip():
        text = get_wikipedia_summary(company_name)
    # Combine verified details and source text for context
    combined_context = f"""
Verified Company Information:
{company_details if company_details else ''}

Additional Context:
{text[:6000]}
"""
    prompt = f"""You are Company Description Writer v1. You produce professional, neutral, and fact-based company descriptions in a single paragraph, using only company websites and reliable news sources.

RULES:
- Write in an objective tone, avoiding marketing language, flowery adjectives, or adverbs.
- NEVER use generic non-factual words: 'significant', 'important', 'best', 'leading', 'cutting-edge', 'innovative'.
- Provide SPECIFIC factual details, especially for funding rounds and investors (names, amounts, rounds, percentages).
- All content must be ONE SINGLE PARAGRAPH - no bullet points, no lists, no line breaks.
- Do NOT include a concluding sentence or summary - end when factual information ends.
- Do NOT include promotional sentences like "More information can be found on their website".
- Do NOT mention the company website URL in the description.

REQUIRED CONTENT (include where available):
- Year founded
- Products and services (if data provider: specify data types with granularity)
- CEO and founder(s)
- Acquisitions and disposals
- Funding rounds (last confirmed round, main investors)
- Headquarters location (city/country only, no street address)
- Ownership structure

OWNERSHIP RULES:
- If PE-backed: specify the PE firm and when sponsorship occurred.
- If VC-funded: list only the last officially confirmed round and main investors.
- If no disclosed venture/PE backing: simply state "private" - do NOT mention lack of backing.
- If public: state the exchange.

FOR DATA/ANALYTICS PROVIDERS:
- Describe data types (e.g., residential vs commercial real estate, financial instruments, etc.)
- Specify distinct products if 2 or more exist.
- Reader must understand what dataset is at the core of the offering.

REFERENCE EXAMPLE (follow this style):
"IMPECT is a football analytics software company founded in 2014 by Stefan Reinartz, Jens Hegeler, Lukas Keppler, and Matthias Sienz, headquartered in Cologne, Germany. The company develops cloud-based tools and data services for clubs, coaches, scouts and federations, focusing on tactical insight, player performance, opponent analysis, and internal benchmarking using proprietary metrics. One of its signature innovations is the Packing metric, which measures how effectively players move the ball past opponents using passing and positioning, intended to provide higher explanatory power for game success than basic statistics like possession or pass completion. Impect operates a SaaS business model, collecting and owning event data from a large number of matches across many leagues (over 40,000 matches annually in 252 countries for 150+ teams). Its products include a scouting platform, analysis tools, data APIs, and raw event datasets. In 2025, Impect was acquired by Catapult Sports in a deal worth up to EUR78m ($91m), as part of a strategy by Catapult to integrate tactical and scouting analytics into its broader video-, performance- and wearable-technology product suite."

Now write a single-paragraph description for "{company_name}" using ONLY the verified information below. Do NOT invent data.

{combined_context}
"""
    result = openrouter_chat("openai/gpt-4o-mini", prompt, "Company Description Writer v1")
    # Validate the description
    if not result or len(result.strip()) < 40:
        return "❌ No factual description could be generated."
    # Clean up - ensure single paragraph, remove any trailing website mentions
    result = result.strip()
    # Remove common trailing patterns about websites
    import re
    result = re.sub(r'\s*(For more information|More information|Visit|Learn more)[^.]*\.?\s*$', '', result, flags=re.IGNORECASE)
    result = re.sub(r'\s*\(?https?://[^\s\)]+\)?\s*\.?\s*$', '', result)
    return result.strip()

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