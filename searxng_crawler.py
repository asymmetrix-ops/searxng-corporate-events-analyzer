# searxng_crawler.py
# This module provides functions to scrape company information from websites, Wikipedia, and
# fallback to AI-generated content using SerpAPI and Playwright for static and dynamic pages.

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from serpapi import GoogleSearch
import os

# Optional: Playwright for JS-heavy sites
from playwright.sync_api import sync_playwright

# ============================================================
# 🔹 Static Page Scraper
# ============================================================
def scrape_static_page(url):
    """
    Scrapes static HTML content from a given URL.

    Args:
        url (str): The URL of the page to scrape.

    Returns:
        str: The extracted text from paragraphs, divs, articles, or sections, or empty string on error.
    """
    try:
        # Set user-agent to mimic a browser
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        # Parse HTML content with BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        texts = []

        # Extract text from relevant tags, keeping only substantial content
        for tag in soup.find_all(["p", "div", "article", "section"]):
            txt = tag.get_text(separator=" ", strip=True)
            if len(txt) > 50:
                texts.append(txt)

        return " ".join(texts)
    except Exception as e:
        # Log error and return empty string if scraping fails
        print(f"Error scraping {url}: {e}")
        return ""

# ============================================================
# 🔹 JavaScript Page Scraper
# ============================================================
def scrape_js_page(url, timeout=60000):
    """
    Scrapes content from JavaScript-heavy pages using Playwright.

    Args:
        url (str): The URL of the page to scrape.
        timeout (int): Timeout for page loading in milliseconds (default: 60000).

    Returns:
        str: The extracted text from the page body, or empty string on error.
    """
    try:
        # Initialize Playwright and launch a headless Chromium browser
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Navigate to the URL and wait until network is idle
            page.goto(url, wait_until="networkidle", timeout=timeout)
            text = page.inner_text("body")
            browser.close()
            return text
    except Exception as e:
        # Log error and return empty string if scraping fails
        print(f"Error scraping JS page {url}: {e}")
        return ""

# ============================================================
# 🔹 Wikipedia Text Fetcher
# ============================================================
def fetch_wikipedia_text(company_name):
    """
    Fetches company information from Wikipedia using SerpAPI to find the relevant page.

    Args:
        company_name (str): The name of the company to search for.

    Returns:
        str: The extracted text from Wikipedia paragraphs, or empty string on error.
    """
    # Construct search query to find Wikipedia page
    query = f"{company_name} site:wikipedia.org"
    params = {
        "q": query,
        "hl": "en",
        "gl": "us",
        "num": 1,
        "api_key": os.getenv("SERPAPI_KEY")
    }
    try:
        # Perform search using SerpAPI
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        if not results:
            return ""
        wiki_url = results[0].get("link")
        if not wiki_url:
            return ""
        # Fetch and parse Wikipedia page
        r = requests.get(wiki_url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        # Extract substantial paragraphs
        paragraphs = [p.get_text() for p in soup.find_all("p") if len(p.get_text()) > 50]
        return " ".join(paragraphs)
    except Exception as e:
        # Log error and return empty string if fetching fails
        print(f"Error fetching Wikipedia text for {company_name}: {e}")
        return ""

# ============================================================
# 🔹 Main Website Scraper
# ============================================================
def scrape_website(base_url=None, company_name=None, use_js_fallback=True):
    """
    Scrapes company information with layered fallbacks:
    1. Website scraping (root, /about, /leadership).
    2. JavaScript rendering if static content is insufficient.
    3. Wikipedia fallback if content is still short.
    4. AI-generated summary as a last resort.

    Args:
        base_url (str, optional): The base URL to scrape.
        company_name (str, optional): The company name for Wikipedia or AI fallback.
        use_js_fallback (bool): Whether to use Playwright for JS-heavy sites (default: True).

    Returns:
        str: The combined scraped text, or empty string if all methods fail.
    """
    combined_text = ""

    # --- Step 1: Website scraping ---
    if base_url:
        # Define URLs to scrape (root, about, leadership pages)
        pages_to_scrape = [
            base_url,
            urljoin(base_url, "/about"),
            urljoin(base_url, "/leadership")
        ]

        # Scrape each page and combine text
        for url in pages_to_scrape:
            text = scrape_static_page(url)
            combined_text += text + " "

        # Use Playwright for JS-heavy sites if content is insufficient
        if use_js_fallback and len(combined_text.strip()) < 500:
            print("Static scraping insufficient, using JS rendering...")
            js_text = scrape_js_page(base_url)
            combined_text += js_text

    # --- Step 2: Wikipedia fallback ---
    if company_name and len(combined_text.strip()) < 500:
        print("Content too short, trying Wikipedia...")
        wiki_text = fetch_wikipedia_text(company_name)
        combined_text += wiki_text

    # --- Step 3: GPT fallback ---
    if company_name and len(combined_text.strip()) < 500:
        print("Still insufficient, using GPT to generate company info...")
        # Import generate_summary here to avoid circular import
        from searxng_analyzer import generate_summary
        combined_text = generate_summary(f"Provide company info for: {company_name}")

    return combined_text.strip()