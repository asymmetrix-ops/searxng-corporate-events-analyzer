import os
import json
from supabase import create_client, Client
from dotenv import load_dotenv

# ============================================================
# 🔹 Environment Setup and Supabase Initialization
# ============================================================
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# 🔹 Text Sanitization Helper
# ============================================================
def clean_text(text):
    """Ensure all stored data is safe and in string/JSON form."""
    if text is None:
        return ""
    if isinstance(text, (dict, list)):
        try:
            return json.dumps(text)
        except Exception:
            return str(text)
    if not isinstance(text, str):
        text = str(text)
    return text.strip().replace("\0", "")


# ============================================================
# 🔹 Valuation Reports Management
# ============================================================
def store_report(company, summary, description, corporate_events="", top_management=""):
    """
    Always insert a new record (no overwrite, no deduplication).
    Keeps full history of each run.
    """
    data = {
        "company": clean_text(company),
        "summary": clean_text(summary),
        "description": clean_text(description),
        "corporate_events": clean_text(corporate_events),
        "top_management": clean_text(top_management),
    }

    try:
        response = supabase.table("valuation_reports").insert(data).execute()
        if response.data:
            print(f"✅ New report stored for: {company} (ID: {response.data[0].get('id', 'unknown')})")
            return response.data
        else:
            print(f"⚠️ No response when inserting report for: {company}")
            return False
    except Exception as e:
        print(f"⚠️ Exception while storing report for {company}: {e}")
        return False


def get_reports():
    """Retrieve all valuation reports from the database."""
    try:
        response = (
            supabase.table("valuation_reports")
            .select("*")
            .order("id", desc=True)
            .execute()
        )
        return response.data if response.data else []
    except Exception as e:
        print(f"⚠️ Exception while fetching reports: {e}")
        return []


# ============================================================
# 🔹 Search History Management
# ============================================================
def store_search(query, results, summary, description, corporate_events="", top_management=""):
    """
    Always insert new record (keeps full query log, even if duplicate).
    """
    data = {
        "query": clean_text(query),
        "results": clean_text(results),
        "summary": clean_text(summary),
        "description": clean_text(description),
        "corporate_events": clean_text(corporate_events),
        "top_management": clean_text(top_management),
    }

    try:
        response = supabase.table("search_history").insert(data).execute()
        if response.data:
            print(f"✅ Search history inserted for: {query}")
            return response.data
        else:
            print(f"⚠️ Insert failed for search: {query}")
            return False
    except Exception as e:
        print(f"⚠️ Exception while storing search history for {query}: {e}")
        return False


def get_search_history():
    """Retrieve all previous search history entries."""
    try:
        response = (
            supabase.table("search_history")
            .select("*")
            .order("id", desc=True)
            .execute()
        )
        return response.data if response.data else []
    except Exception as e:
        print(f"⚠️ Exception while fetching search history: {e}")
        return []


# ============================================================
# 🔹 Company Subsidiaries Management
# ============================================================
def store_subsidiaries(company, subsidiaries):
    """
    Always insert new subsidiary rows.
    Allows duplicates (useful for comparison and historical tracking).
    """
    if not subsidiaries or not isinstance(subsidiaries, list):
        return

    data = []
    for sub in subsidiaries:
        data.append({
            "company": clean_text(company),
            "name": clean_text(sub.get("name", "")),
            "logo": clean_text(sub.get("logo", "")),
            "description": clean_text(sub.get("description", "")),
            "sector": clean_text(sub.get("sector", "")),
            "linkedin_members": sub.get("linkedin_members") or 0,
            "country": clean_text(sub.get("country", "")),
        })

    try:
        response = supabase.table("company_subsidiaries").insert(data).execute()
        if response.data:
            print(f"✅ Inserted {len(data)} subsidiaries for {company} (duplicates allowed)")
            return response.data
        else:
            print(f"⚠️ No response inserting subsidiaries for {company}")
            return False
    except Exception as e:
        print(f"⚠️ Failed to store subsidiaries for {company}: {e}")
        return False


def get_subsidiaries(company):
    """Retrieve all subsidiaries for a given company (including duplicates)."""
    try:
        response = (
            supabase.table("company_subsidiaries")
            .select("*")
            .eq("company", company)
            .order("id", desc=True)
            .execute()
        )
        return response.data if response.data else []
    except Exception as e:
        print(f"⚠️ Error fetching subsidiaries for {company}: {e}")
        return []
