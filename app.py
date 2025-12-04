# ============================================================
# app.py — SearXNG AI Research & Valuation Assistant
# ============================================================

import os
import json
import re
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from serpapi import GoogleSearch
# from google_search_results import GoogleSearch
import hashlib
import ast
import uuid

from searxng_analyzer import (
    generate_summary,
    generate_description,
    get_wikipedia_summary,
    generate_corporate_events,
    get_top_management,
    generate_subsidiary_data
)
from searxng_db import (
    store_report,
    get_reports,
    store_search,
    get_search_history,
    get_subsidiaries 
)
from searxng_pdf import create_pdf_from_text
import requests
from typing import Optional

# ============================================================
# 🔹 Xano API Integration
# ============================================================

XANO_BASE_URL = "https://xdil-abvj-o7rq.e2.xano.io"

def check_company_by_url(website_url: str) -> Optional[dict]:
    """
    Check if a company already exists in the Xano database by URL.
    Returns: {'id': 1234} if found, None if not found
    """
    try:
        endpoint = f"{XANO_BASE_URL}/api:8Bv5PK4I/get_company_by_url"
        response = requests.get(endpoint, params={"website_url": website_url}, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # API returns null or empty if not found
        if data is None or data == {} or (isinstance(data, dict) and data.get("id") is None):
            return None
        
        return data
    except Exception as e:
        print(f"[Xano] Error checking company by URL: {e}")
        return None

def get_company_by_id(company_id: int) -> Optional[dict]:
    """
    Fetch full company data from Xano by company ID.
    Returns: Company data dict or None if error
    """
    try:
        endpoint = f"{XANO_BASE_URL}/api:GYQcK4au/Get_new_company/{company_id}"
        response = requests.get(endpoint, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Xano] Error fetching company by ID: {e}")
        return None

def get_corporate_events_by_company_id(company_id: int) -> Optional[list]:
    """
    Fetch corporate events for a company from Xano.
    Returns: List of corporate events or None if error
    """
    try:
        endpoint = f"{XANO_BASE_URL}/api:y4OAXSVm/Get_investors_corporate_events"
        response = requests.get(endpoint, params={"new_company_id": company_id}, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("New_Events_Wits_Advisors", [])
    except Exception as e:
        print(f"[Xano] Error fetching corporate events: {e}")
        return None

def create_corporate_event(event_data: dict) -> dict:
    """
    Create a new corporate event in Xano database.
    
    event_data should contain:
    - title: str
    - announcement_date: str (YYYY-MM-DD)
    - closed_date: str (YYYY-MM-DD) optional
    - deal_type: str
    - deal_status: str
    - investment_amount: str optional
    - currency_id: int optional
    - counterparties: list of dicts with name, role, announcement_url, linkedin_url, individuals
    
    Returns: API response dict with success status
    """
    endpoint = f"{XANO_BASE_URL}/api:617tZc8l/create_corporate_event"
    
    try:
        response = requests.post(endpoint, json=event_data, timeout=30)
        
        if response.status_code == 200:
            return response.json()
        else:
            error_text = response.text[:500] if response.text else "No response body"
            return {"success": False, "error": f"HTTP {response.status_code}: {error_text}"}
            
    except requests.exceptions.ConnectionError as e:
        return {"success": False, "error": f"Connection Error: {str(e)}"}
    except requests.exceptions.Timeout as e:
        return {"success": False, "error": f"Timeout: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============================================================
# 🔹 Normalization Helpers
# ============================================================

def normalize_top_management(data):
    """Ensure keys match the expected format (role → position, add status if missing)."""
    if not data:
        return []
    try:
        mgmt = json.loads(data) if isinstance(data, str) else data
        if isinstance(mgmt, list):
            for m in mgmt:
                if "role" in m and "position" not in m:
                    m["position"] = m.pop("role")
                if "status" not in m:
                    m["status"] = "Current"  # Default for legacy data
            return mgmt
    except:
        return []
    return []

def normalize_corporate_events(raw_text):
    """Convert plain text or JSON events into a structured list."""
    events = []
    if not raw_text:
        return events

    # If already JSON, return it directly
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return parsed
    except:
        pass

    # Parse plain text format using regex for robust multi-line extraction
    pattern = r'- Event Description: (.*?)(?=\s*(?:Date:|Type:|Value:| - Event Description:)|$)'
    matches = re.findall(pattern, raw_text, re.DOTALL | re.IGNORECASE)
    
    for match in matches:
        if not match.strip():
            continue
        event_block = re.search(r'- Event Description: .*?(?=\s*- Event Description:|$)', raw_text, re.DOTALL | re.IGNORECASE)
        if not event_block:
            continue
        event_block = event_block.group(0)
        
        event = {"description": match.strip()}
        lines = event_block.split('\n')
        for line in lines:
            line = line.strip()
            if ':' in line and line.lower().startswith(('date:', 'type:', 'value:')):
                key, val = line.split(':', 1)
                key = key.strip().lower()
                val = val.strip()
                if key == 'date':
                    event["date"] = val
                elif key == 'type':
                    event["type"] = val
                elif key == 'value':
                    event["value"] = val
        if event.get("description"):
            events.append(event)
    
    return events

# ============================================================
# 🔹 Environment Setup
# ============================================================
load_dotenv()
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

# ============================================================
# 🔹 Streamlit Configuration
# ============================================================
st.set_page_config(
    page_title="SearXNG – AI Research Assistant",
    page_icon="🧭",
    layout="wide"
)
st.title("🧭 SearXNG – AI Research & Valuation Assistant")
st.markdown("#### Discover insights, analyze companies, and generate instant valuation reports powered by AI.")

# ============================================================
# 🔹 Helper Functions
# ============================================================

# ============================================
# FILE 2: display_events.py (or your UI file)
# ============================================

def show_corporate_events(corporate_events):
    """
    Displays corporate events with enhanced counterparty details including:
    - Company LinkedIn URLs
    - Press release URLs per counterparty
    - Individuals involved in the deal
    - Announcement date vs Closed date
    
    Args:
        corporate_events: List of event dictionaries or JSON string
    """
    import pandas as pd
    import streamlit as st
    import json
    
    if isinstance(corporate_events, str):
        try:
            corporate_events = json.loads(corporate_events)
        except:
            st.warning("Could not parse corporate events.")
            return

    if not corporate_events or not isinstance(corporate_events, list):
        st.info("No corporate events found.")
        return

    # Sort events by date (newest first) - prefer announcement date
    def parse_date(event):
        try:
            date_str = event.get("Announcement Date", event.get("Date", event.get("date", "")))
            if not date_str:
                date_str = event.get("Closed Date", "")
            return pd.to_datetime(date_str, errors="coerce")
        except:
            return pd.NaT
    
    sorted_events = sorted(corporate_events, key=parse_date, reverse=True)

    st.markdown("### 📊 Corporate Events Timeline")
    
    # Count total counterparties and individuals
    total_counterparties = sum(len(e.get("counterparties", [])) for e in sorted_events)
    total_individuals = sum(
        len(cp.get("individuals", [])) 
        for e in sorted_events 
        for cp in e.get("counterparties", [])
    )
    st.caption(f"📈 {len(sorted_events)} events • 👥 {total_counterparties} counterparties • 👤 {total_individuals} individuals")

    # Display each event with expandable counterparty details
    for i, event in enumerate(sorted_events):
        # Handle both new format (Announcement Date/Closed Date) and old format (Date/date)
        announcement_date = event.get("Announcement Date", event.get("announcement_date", ""))
        closed_date = event.get("Closed Date", event.get("closed_date", ""))
        # Fallback to old Date field if new fields are empty
        old_date = event.get("Date", event.get("date", ""))
        display_date = announcement_date or closed_date or old_date or "Unknown"
        
        event_short = event.get("Event (short)", event.get("event_short", "Unknown event"))
        deal_type = event.get("Deal Type", event.get("deal_type", event.get("Event type", event.get("event_type", "Unknown"))))
        deal_status = event.get("Deal Status", event.get("deal_status", ""))
        value = event.get("Event value (USD)", event.get("value_usd", "Undisclosed"))
        source_url = event.get("Source URL", event.get("source_url", ""))
        counterparties = event.get("counterparties", [])
        
        # Event card
        with st.container():
            col1, col2 = st.columns([1, 4])
            
            with col1:
                # Always show Date Announced and Date Closed explicitly
                st.markdown("**Date Announced:**")
                if announcement_date:
                    st.markdown(f"📢 {announcement_date}")
                else:
                    st.caption("_Not available_")
                
                st.markdown("**Date Closed:**")
                if closed_date:
                    st.markdown(f"✅ {closed_date}")
                elif announcement_date:
                    st.caption("⏳ _Pending_")
                else:
                    st.caption("_Not available_")
                
                # Deal type badge
                type_icons = {
                    "Acquisition": "🟢",
                    "Sale": "🔴",
                    "IPO": "🟡",
                    "MBO": "🟠",
                    "Investment": "🟣",
                    "Strategic Review": "🔍",
                    "Divestment": "📤",
                    "Restructuring": "🔄",
                    "Dual track": "⚡",
                    "Closing": "✅",
                    "Grant": "🎁",
                    "Debt financing": "💳",
                    "Bankruptcy": "⚠️",
                    "Reorganisation": "🔧",
                    "Employee tender offer": "👥",
                    "Rebrand": "🏷️",
                    "Partnership": "🤝"
                }
                type_badge = next((v for k, v in type_icons.items() if k.lower() in deal_type.lower()), "⚪")
                st.caption(f"{type_badge} {deal_type}")
                
                # Deal status badge
                if deal_status:
                    status_icons = {
                        "Completed": "✅",
                        "In Market": "📢",
                        "Not yet launched": "⏳",
                        "Strategic Review": "🔍",
                        "Deal Prep": "📋",
                        "In Exclusivity": "🔒"
                    }
                    status_badge = next((v for k, v in status_icons.items() if k.lower() in deal_status.lower()), "•")
                    st.caption(f"{status_badge} {deal_status}")
            
            with col2:
                st.markdown(f"**{event_short}**")
                
                # Display professional description if available
                description = event.get("Description", event.get("description", ""))
                if description:
                    st.markdown(f"<p style='color: #666; font-size: 0.9em; margin: 8px 0;'>{description}</p>", unsafe_allow_html=True)
                
                # Value and source URL on same line
                if source_url:
                    st.caption(f"💰 {value} • [📎 Source]({source_url})")
                else:
                    st.caption(f"💰 {value}")
                
                # Enhanced Counterparties Table
                if counterparties:
                    with st.expander(f"👥 View {len(counterparties)} Counterparties"):
                        for cp in counterparties:
                            cp_type = cp.get("type", "Unknown")
                            type_id = cp.get("type_id", 0)
                            company_name = cp.get("company_name", "Unknown")
                            role_desc = cp.get("role_description", "")
                            company_linkedin = cp.get("company_linkedin_url", "")
                            press_release = cp.get("press_release_url", "")
                            individuals = cp.get("individuals", [])
                            
                            # Role icons by type
                            role_icons = {
                                "Target": "🎯",
                                "Acquirer": "🏢",
                                "Seller": "📤",
                                "Investor": "💼",
                                "Joint Venture": "🤝"
                            }
                            icon = next((v for k, v in role_icons.items() if k.lower() in cp_type.lower()), "🏛️")
                            
                            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            # Counterparty Card
                            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            st.markdown(f"""
<div style="border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 12px; background: #1a1a2e;">
    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
        <div>
            <span style="font-size: 1.3em;">{icon}</span>
            <strong style="font-size: 1.1em; margin-left: 8px;">{company_name}</strong>
            {f'<a href="{company_linkedin}" target="_blank" style="margin-left: 8px; color: #0077B5;">🔗</a>' if company_linkedin else ''}
        </div>
        <span style="background: #2d2d44; padding: 4px 10px; border-radius: 12px; font-size: 0.85em;">
            {cp_type}
        </span>
    </div>
    <p style="color: #888; font-size: 0.9em; margin: 8px 0 0 0;">{role_desc}</p>
</div>
""", unsafe_allow_html=True)
                            
                            # Announcement URL
                            if press_release:
                                st.markdown(f"📄 **Announcement:** [{press_release[:60]}...]({press_release})" if len(press_release) > 60 else f"📄 **Announcement:** [{press_release}]({press_release})")
                            
                            # Key People Involved (Table format)
                            if individuals:
                                st.markdown("**👤 Key People Involved:**")
                                
                                # Build table data
                                people_data = []
                                for ind in individuals:
                                    ind_name = ind.get("name", "Unknown")
                                    ind_title = ind.get("title", "")
                                    ind_linkedin = ind.get("linkedin_url", "")
                                    
                                    # Format name with LinkedIn link if available
                                    if ind_linkedin:
                                        name_display = f"[{ind_name}]({ind_linkedin})"
                                    else:
                                        name_display = ind_name
                                    
                                    people_data.append({
                                        "Name": name_display,
                                        "Title": ind_title or "—"
                                    })
                                
                                # Display as formatted list (cleaner than table for small lists)
                                for person in people_data:
                                    st.markdown(f"  • **{person['Name']}** — {person['Title']}")
                            else:
                                st.caption("_No individuals identified_")
                            
                            st.markdown("")  # Spacing between counterparties
            
            st.divider()

def show_top_management(mgmt_data):
    """
    Renders top management information with enhanced details:
    - Current Leadership with LinkedIn, Location, and Bio
    - Past Leadership
    Handles JSON, legacy strings, or semicolon-delimited text gracefully.
    """
    # -------------------------
    # 1️⃣ Parse / Normalize Data
    # -------------------------
    if not mgmt_data:
        st.info("No top management data available.")
        return

    # Convert JSON strings → list
    if isinstance(mgmt_data, str):
        try:
            parsed = json.loads(mgmt_data)
            if isinstance(parsed, dict):
                # From new get_top_management() format
                mgmt_data = []
                for item in parsed.get("current", []):
                    item["status"] = "Current"
                    mgmt_data.append(item)
                for item in parsed.get("past", []):
                    item["status"] = "Past"
                    mgmt_data.append(item)
            elif isinstance(parsed, list):
                mgmt_data = parsed
            else:
                mgmt_data = []
        except Exception:
            # Try to parse plain string: "Name — Role (Status); ..."
            entries = re.split(r";\s*", mgmt_data.strip())
            mgmt_data = []
            for entry in entries:
                if not entry.strip():
                    continue
                match = re.match(r"(.+?)\s*[—-]\s*(.+?)(?:\s*\((Current|Past)\))?$", entry.strip())
                if match:
                    name, position, status = match.groups()
                    mgmt_data.append({
                        "name": name.strip(),
                        "position": position.strip(),
                        "status": status or "Current"
                    })
                else:
                    mgmt_data.append({"name": entry.strip(), "position": "", "status": "Current"})

    # Ensure it's a valid list
    if not isinstance(mgmt_data, list) or not mgmt_data:
        st.info("No top management data available.")
        return

    df = pd.DataFrame(mgmt_data)
    if not {"name", "position"}.issubset(df.columns):
        st.info("No valid management data found.")
        return

    # Clean & normalize column names
    column_mapping = {
        "name": "Name", 
        "position": "Position", 
        "status": "Status",
        "linkedin_url": "LinkedIn",
        "location": "Location",
        "bio": "Bio"
    }
    df = df.rename(columns=column_mapping)
    df["Status"] = df["Status"].fillna("Current").apply(lambda x: x.capitalize() if isinstance(x, str) else "Current")

    # -------------------------
    # 2️⃣ Split into Current & Past
    # -------------------------
    current_df = df[df["Status"] == "Current"].drop_duplicates(subset=["Name", "Position"]).fillna("")
    past_df = df[df["Status"] == "Past"].drop_duplicates(subset=["Name", "Position"]).fillna("")

    # -------------------------
    # 3️⃣ Display Current Leadership with Enhanced Cards
    # -------------------------
    if not current_df.empty:
        st.markdown("### 👤 Current Leadership")
        
        for idx, row in current_df.iterrows():
            name = row.get("Name", "")
            position = row.get("Position", "")
            linkedin = row.get("LinkedIn", "")
            location = row.get("Location", "")
            bio = row.get("Bio", "")
            
            with st.container():
                st.markdown("---")
                cols = st.columns([3, 2])
                
                with cols[0]:
                    st.markdown(f"#### {name}")
                    st.markdown(f"**{position}**")
                    
                    if location:
                        st.markdown(f"📍 {location}")
                    
                    if linkedin:
                        st.markdown(f"[🔗 LinkedIn Profile]({linkedin})")
                
                with cols[1]:
                    if bio:
                        st.markdown(f"*{bio}*")
        
        # Also show as compact table
        with st.expander("📊 View as Table"):
            display_cols = ["Name", "Position", "Location"]
            if "LinkedIn" in current_df.columns:
                display_cols.append("LinkedIn")
            available_cols = [c for c in display_cols if c in current_df.columns]
        st.dataframe(
                current_df[available_cols].reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "LinkedIn": st.column_config.LinkColumn("LinkedIn", display_text="View Profile")
                }
        )
    elif past_df.empty:
        st.info("No leadership data available.")

    # -------------------------
    # 4️⃣ Display Past Leadership
    # -------------------------
    if not past_df.empty:
        st.markdown("### 🕰️ Past Leadership")
        
        display_cols = ["Name", "Position"]
        if "Location" in past_df.columns:
            display_cols.append("Location")
        if "LinkedIn" in past_df.columns:
            display_cols.append("LinkedIn")
        
        available_cols = [c for c in display_cols if c in past_df.columns]
        
        st.dataframe(
            past_df[available_cols].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
            column_config={
                "LinkedIn": st.column_config.LinkColumn("LinkedIn", display_text="View Profile")
            }
        )

def show_subsidiaries(subsidiaries, context_label="main"):
    """
    Displays subsidiaries in a clean, readable layout.
    ✅ Shows full description (no expand button)
    ✅ Logos fit neatly in divs
    """
    if not subsidiaries:
        st.info("No subsidiaries found.")
        return

    st.markdown("### 🏢 Subsidiaries Overview")

    for i, sub in enumerate(subsidiaries):
        name = sub.get("name", "Unknown")
        logo = sub.get("logo", "")
        desc = sub.get("description", "No description available.")
        sector = sub.get("sector", "N/A")
        country = sub.get("country", "N/A")
        linkedin_members = sub.get("linkedin_members", 0)
        url = sub.get("url", "")

        with st.container():
            st.markdown("---")
            cols = st.columns([1, 6])

            with cols[0]:
                st.markdown(
                    f"""
                    <div style="
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        width: 80px;
                        height: 80px;
                        border-radius: 12px;
                        overflow: hidden;
                        background-color: #f5f5f5;
                        box-shadow: 0 1px 4px rgba(0,0,0,0.1);
                    ">
                        <img src="{logo}" style="max-width: 70px; max-height: 70px; object-fit: contain;" />
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            with cols[1]:
                st.markdown(f"### {name}")
                st.markdown(f"**Sector:** {sector}  |  **Country:** {country}  |  👥 {linkedin_members} members")
                if url:
                    st.markdown(f"[🌐 Visit Website]({url})")

                # ✅ Full description always visible
                st.markdown(f"<p style='text-align: justify;'>{desc}</p>", unsafe_allow_html=True)

# ============================================================
# 🔹 TEST API BUTTON (Standalone - no search needed)
# ============================================================
with st.expander("🧪 TEST: Xano API Connection", expanded=False):
    st.markdown("**Test the API without running a search**")
    
    import streamlit.components.v1 as components
    
    test_payload = {
        "title": "TEST EVENT - Delete Me",
        "announcement_date": "2024-01-01",
        "closed_date": None,
        "deal_type": "Acquisition",
        "deal_status": "Completed",
        "investment_amount": "",
        "currency_id": 15,
        "counterparties": [
            {
                "name": "Test Company A",
                "role": "Target",
                "announcement_url": "",
                "linkedin_url": "",
                "individuals": []
            }
        ]
    }
    
    test_payload_json = json.dumps(test_payload)
    test_endpoint = f"{XANO_BASE_URL}/api:617tZc8l/create_corporate_event"
    
    st.code(f"Endpoint: {test_endpoint}", language="text")
    st.json(test_payload)
    
    test_html = f'''
    <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 10px;">
        <button id="testApiBtn" onclick="testXanoApi()" 
            style="background: #228be6; 
                   color: white; 
                   border: none; 
                   padding: 15px 30px; 
                   border-radius: 8px; 
                   font-size: 18px; 
                   font-weight: bold;
                   cursor: pointer;">
            🧪 TEST API CALL
        </button>
        
        <div id="testLog" style="margin-top: 15px; 
                               padding: 15px; 
                               background: #1a1a2e; 
                               border-radius: 8px; 
                               font-family: monospace;
                               font-size: 14px;
                               min-height: 100px;
                               color: #fff;">
            Click the button to test the API...
        </div>
    </div>
    
    <script>
    function logTest(msg, color) {{
        const log = document.getElementById('testLog');
        const time = new Date().toLocaleTimeString();
        log.innerHTML += '<div style="color: ' + (color || '#69db7c') + '; margin: 5px 0;">[' + time + '] ' + msg + '</div>';
        log.scrollTop = log.scrollHeight;
    }}
    
    async function testXanoApi() {{
        const btn = document.getElementById('testApiBtn');
        const log = document.getElementById('testLog');
        
        log.innerHTML = '';  // Clear previous logs
        btn.disabled = true;
        btn.innerHTML = '⏳ Testing...';
        
        const payload = {test_payload_json};
        const endpoint = '{test_endpoint}';
        
        logTest('🔘 Starting API test...', '#74c0fc');
        logTest('📡 Endpoint: ' + endpoint, '#74c0fc');
        logTest('📦 Payload: ' + JSON.stringify(payload).substring(0, 100) + '...', '#74c0fc');
        logTest('⏳ Sending request...', '#ffd43b');
        
        try {{
            const response = await fetch(endpoint, {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify(payload)
            }});
            
            logTest('📡 HTTP Status: ' + response.status, '#74c0fc');
            
            const text = await response.text();
            logTest('📄 Raw Response: ' + text.substring(0, 500), '#74c0fc');
            
            try {{
                const result = JSON.parse(text);
                if (result.success) {{
                    logTest('✅ SUCCESS! Event ID: ' + result.event_id, '#69db7c');
                    btn.innerHTML = '✅ API Works!';
                    btn.style.background = '#40c057';
                }} else {{
                    logTest('⚠️ API returned: ' + JSON.stringify(result), '#ffd43b');
                    btn.innerHTML = '⚠️ Check Log';
                    btn.style.background = '#fab005';
                }}
            }} catch (e) {{
                logTest('📄 Response is not JSON: ' + text, '#ffd43b');
            }}
        }} catch (error) {{
            logTest('❌ FETCH ERROR: ' + error.message, '#ff6b6b');
            logTest('❌ This usually means CORS issue or network problem', '#ff6b6b');
            btn.innerHTML = '❌ Error';
            btn.style.background = '#fa5252';
        }}
        
        setTimeout(() => {{
            btn.disabled = false;
            btn.innerHTML = '🧪 TEST API CALL';
            btn.style.background = '#228be6';
        }}, 5000);
    }}
    </script>
    '''
    
    components.html(test_html, height=350)

st.markdown("---")

# ============================================================
# 🔹 Search Input
# ============================================================
search_query = st.text_input("🔎 Enter company/topic (or paste URL directly)", placeholder="Google, ChatGPT, or https://example.com")

# ============================================================
# 🔹 Fetch Search Results (DISABLED FOR TESTING - saves SerpAPI credits)
# ============================================================
if search_query.strip():
    st.info("⚡ TESTING MODE: Search results disabled to save credits. Click 'Analyze' to proceed.")

# ============================================================
# 🔹 Analyze Company
# ============================================================
if st.button("🚀 Analyze Company"):
    if not search_query.strip():
        st.warning("⚠️ Please enter a company name or URL")
    else:
        progress = st.progress(0)
        status = st.empty()

        # ============================================================
        # 🔹 STEP 1: Pre-check if company exists in Xano database
        # ============================================================
        status.text("🔍 Checking if company exists in database...")
        
        # Extract URL if provided, or use the search query as-is
        input_url = search_query.strip()
        existing_company = check_company_by_url(input_url)
        progress.progress(5)
        
        # Fetch database data if company exists
        db_company_data = None
        if existing_company and existing_company.get("id"):
            company_id = existing_company.get("id")
            status.text(f"✅ Company found in database (ID: {company_id}). Fetching database data...")
            progress.progress(10)
            db_company_data = get_company_by_id(company_id)
        
        # ============================================================
        # 🔹 STEP 2: Always run AI analysis for comparison
        # ============================================================
        if db_company_data:
            st.success(f"✅ Company exists in database (ID: {existing_company.get('id')}). Running AI analysis for comparison...")
        else:
            st.info("🆕 Company not found in database. Running full AI analysis...")
        
        summary, description, corporate_events, mgmt_list, subsidiaries = "", "", [], [], []
        try:
            # ============================================================
            # ⚡ TESTING MODE - Minimal API calls to save credits
            # ============================================================
            status.text("⚡ TESTING MODE - Skipping Wikipedia...")
            wiki_text = ""  # Skip Wikipedia to save credits
            progress.progress(20)

            status.text("⚡ TESTING MODE - Skipping summary...")
            summary = f"Testing mode - summary disabled for {search_query}"  # Skip AI summary
            progress.progress(40)

            status.text("⚡ TESTING MODE - Skipping description...")
            description = f"Testing mode - description disabled for {search_query}"  # Skip AI description
            progress.progress(60)

            status.text("📅 Fetching 1 corporate event (TEST MODE)...")
            corporate_events = generate_corporate_events(search_query, max_events=1)  # LIMITED TO 1 FOR TESTING
            progress.progress(80)

            mgmt_list = []  # Top management disabled
            progress.progress(90)

            status.text("⚡ TESTING MODE - Skipping subsidiaries...")
            subsidiaries = []  # Skip subsidiaries to save credits
            progress.progress(95)

            # Store report if new company
            if not db_company_data:
            store_report(
                search_query,
                summary,
                description,
                json.dumps(corporate_events),
                json.dumps(mgmt_list),
            )
            store_search(
                search_query,
                wiki_text,
                summary,
                description,
                json.dumps(corporate_events),
                json.dumps(mgmt_list),
            )
            
            progress.progress(100)
            status.text("✅ Done")

            # ============================================================
            # 🔹 DISPLAY: Side-by-side if company exists, single column if new
            # ============================================================
            if db_company_data:
                st.markdown("---")
                st.markdown("## 🔄 Comparison: AI Analysis vs Database")
                
                # Fetch corporate events from dedicated API
                company_id = existing_company.get("id")
                db_corporate_events = get_corporate_events_by_company_id(company_id) or []
                
                # ============================================================
                # 🔹 Compare events to find missing ones
                # ============================================================
                def normalize_text(text):
                    """Normalize text for comparison"""
                    if not text:
                        return ""
                    return text.lower().strip()
                
                def extract_keywords(text):
                    """Extract key words from event description"""
                    if not text:
                        return set()
                    # Remove common words and extract meaningful terms
                    stop_words = {'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in', 'for', 'with', 'by', 'from', 'its', 'as'}
                    words = set(normalize_text(text).split())
                    return words - stop_words
                
                def events_match(ai_event, db_event, threshold=0.4):
                    """Check if two events likely refer to the same deal"""
                    ai_name = ai_event.get("Event (short)", ai_event.get("event_short", ""))
                    db_name = db_event.get("description", "")
                    
                    ai_keywords = extract_keywords(ai_name)
                    db_keywords = extract_keywords(db_name)
                    
                    if not ai_keywords or not db_keywords:
                        return False
                    
                    # Check keyword overlap
                    overlap = len(ai_keywords & db_keywords)
                    max_len = max(len(ai_keywords), len(db_keywords))
                    similarity = overlap / max_len if max_len > 0 else 0
                    
                    return similarity >= threshold
                
                # Find AI events missing from database
                missing_events = []
                matched_events = []
                
                for ai_event in corporate_events:
                    is_matched = False
                    for db_event in db_corporate_events:
                        if events_match(ai_event, db_event):
                            is_matched = True
                            matched_events.append(ai_event)
                            break
                    if not is_matched:
                        missing_events.append(ai_event)
                
                col_ai, col_db = st.columns(2)
                
                # ========== LEFT COLUMN: AI Analysis ==========
                with col_ai:
                    st.markdown("### 🤖 AI Analysis (Live)")
                    st.markdown("---")
                    
                    # Company Overview - EDITABLE
                    st.markdown("#### 🏢 Company Overview ✏️")
                    
                    # Editable company name
                    edited_company_name = st.text_input(
                        "Company Name",
                        value=search_query.replace("https://", "").replace("http://", "").split("/")[0] if search_query.startswith("http") else search_query,
                        key="ai_company_name"
                    )
                    
                    # Editable location
                    col_loc1, col_loc2 = st.columns(2)
                    with col_loc1:
                        edited_city = st.text_input("📍 City", value="", key="ai_city", placeholder="e.g., London")
                    with col_loc2:
                        edited_country = st.text_input("Country", value="", key="ai_country", placeholder="e.g., United Kingdom")
                    
                    # Ownership type
                    ownership_options = ["", "Private Equity", "Public", "Private", "Family Owned", "Venture Backed", "Government"]
                    edited_ownership = st.selectbox("🏛️ Ownership Type", options=ownership_options, key="ai_ownership")
                    
                    # LinkedIn and Website
                    col_link1, col_link2 = st.columns(2)
                    with col_link1:
                        edited_linkedin = st.text_input("🔗 LinkedIn URL", value="", key="ai_linkedin", placeholder="https://linkedin.com/company/...")
                    with col_link2:
                        website_val = search_query if search_query.startswith("http") else ""
                        edited_website = st.text_input("🌐 Website", value=website_val, key="ai_website", placeholder="https://...")
                    
                    # Description - editable
                    st.markdown("---")
                    edited_description = st.text_area(
                        "📝 Description",
                        value=description if description else "",
                        key="ai_description",
                        height=150
                    )
                    
                    st.markdown("---")
                    
                    # Corporate Events - EDITABLE
                    st.markdown(f"#### 📅 Corporate Events ({len(corporate_events)} found) ✏️")
                    
                    # Show match stats
                    if corporate_events:
                        st.caption(f"✅ {len(matched_events)} matched | ⚠️ {len(missing_events)} potentially missing from DB")
                    
                    # Initialize session state for edited events if not exists
                    if 'edited_events' not in st.session_state:
                        st.session_state.edited_events = {}
                    
                    # Deal type and status options
                    deal_type_options = ["", "Acquisition", "Sale", "IPO", "MBO", "Investment", 
                                        "Strategic Review", "Divestment", "Restructuring", 
                                        "Dual track", "Closing", "Grant", "Debt financing", 
                                        "Bankruptcy", "Reorganisation", "Employee tender offer", 
                                        "Rebrand", "Partnership"]
                    deal_status_options = ["", "Completed", "In Market", "Not yet launched", 
                                          "Strategic Review", "Deal Prep", "In Exclusivity", "Pending"]
                    role_options = ["", "Target", "Acquirer", "Investor (majority)", "Investor (minority)", 
                                   "Investor (unknown size)", "Seller", "Divestor", "Advisor", "Partner"]
                    
                    if corporate_events:
                        for idx, event in enumerate(corporate_events[:10]):
                            event_key = f"event_{idx}"
                            
                            # Get current values
                            ann_date = event.get("Announcement Date", event.get("announcement_date", ""))
                            closed_date = event.get("Closed Date", event.get("closed_date", ""))
                            event_name = event.get("Event (short)", event.get("event_short", "Unknown"))
                            deal_type = event.get("Deal Type", event.get("deal_type", ""))
                            deal_status = event.get("Deal Status", event.get("deal_status", ""))
                            source_url = event.get("Source URL", event.get("source_url", ""))
                            description_text = event.get("Description", event.get("description", ""))
                            
                            # Check if this event is missing from DB
                            is_missing = event in missing_events
                            status_icon = "⚠️" if is_missing else "✅"
                            status_badge = " `MISSING FROM DB`" if is_missing else ""
                            
                            # Track if expander should stay open
                            expander_open_key = f"{event_key}_expander_open"
                            is_expanded = st.session_state.get(expander_open_key, False)
                            
                            with st.expander(f"{status_icon} Event #{idx+1}: {event_name[:50]}...{status_badge}", expanded=is_expanded):
                                # Event Name
                                new_name = st.text_input(
                                    "Event Name", 
                                    value=event_name, 
                                    key=f"{event_key}_name"
                                )
                                
                                # Dates
                                col_d1, col_d2 = st.columns(2)
                                with col_d1:
                                    new_ann_date = st.text_input(
                                        "📢 Announcement Date", 
                                        value=ann_date, 
                                        key=f"{event_key}_ann_date",
                                        placeholder="YYYY-MM-DD"
                                    )
                                with col_d2:
                                    new_closed_date = st.text_input(
                                        "✅ Closed Date", 
                                        value=closed_date, 
                                        key=f"{event_key}_closed_date",
                                        placeholder="YYYY-MM-DD"
                                    )
                                
                                # Deal Type and Status
                                col_t1, col_t2 = st.columns(2)
                                with col_t1:
                                    type_index = deal_type_options.index(deal_type) if deal_type in deal_type_options else 0
                                    new_deal_type = st.selectbox(
                                        "🏷️ Deal Type", 
                                        options=deal_type_options,
                                        index=type_index,
                                        key=f"{event_key}_type"
                                    )
                                with col_t2:
                                    status_index = deal_status_options.index(deal_status) if deal_status in deal_status_options else 0
                                    new_deal_status = st.selectbox(
                                        "📊 Deal Status", 
                                        options=deal_status_options,
                                        index=status_index,
                                        key=f"{event_key}_status"
                                    )
                                
                                # Source URL
                                new_source = st.text_input(
                                    "🔗 Source URL", 
                                    value=source_url, 
                                    key=f"{event_key}_source"
                                )
                                
                                # Description
                                new_description = st.text_area(
                                    "📝 Description", 
                                    value=description_text, 
                                    key=f"{event_key}_desc",
                                    height=80
                                )
                                
                                # Counterparties - Editable
                                st.markdown("**👥 Counterparties:**")
                                counterparties = event.get("counterparties", [])
                                
                                # Track number of additional counterparties for this event
                                extra_cp_key = f"{event_key}_extra_cp_count"
                                if extra_cp_key not in st.session_state:
                                    st.session_state[extra_cp_key] = 0
                                
                                # Display existing counterparties from AI
                                for cp_idx, cp in enumerate(counterparties[:5]):
                                    cp_key = f"{event_key}_cp_{cp_idx}"
                                    cp_name = cp.get("company_name", "Unknown")
                                    cp_role = cp.get("role", cp.get("role_description", ""))
                                    cp_linkedin = cp.get("company_linkedin_url", "")
                                    cp_press = cp.get("press_release_url", "")
                                    
                                    with st.container():
                                        st.markdown(f"**Counterparty #{cp_idx+1}**")
                                        cp_col1, cp_col2 = st.columns(2)
                                        with cp_col1:
                                            st.text_input("Company Name", value=cp_name, key=f"{cp_key}_name")
                                        with cp_col2:
                                            role_idx = role_options.index(cp_role) if cp_role in role_options else 0
                                            st.selectbox("Role", options=role_options, index=role_idx, key=f"{cp_key}_role")
                                        
                                        cp_col3, cp_col4 = st.columns(2)
                                        with cp_col3:
                                            st.text_input("LinkedIn URL", value=cp_linkedin, key=f"{cp_key}_linkedin", placeholder="https://linkedin.com/company/...")
                                        with cp_col4:
                                            st.text_input("Announcement URL", value=cp_press, key=f"{cp_key}_press", placeholder="https://...")
                                        
                                        # Individuals
                                        individuals = cp.get("individuals", [])
                                        if individuals:
                                            st.caption("👤 Key Individuals:")
                                            for ind_idx, ind in enumerate(individuals[:3]):
                                                ind_key = f"{cp_key}_ind_{ind_idx}"
                                                ind_col1, ind_col2 = st.columns(2)
                                                with ind_col1:
                                                    st.text_input("Name", value=ind.get("name", ""), key=f"{ind_key}_name", label_visibility="collapsed")
                                                with ind_col2:
                                                    st.text_input("Title", value=ind.get("title", ""), key=f"{ind_key}_title", label_visibility="collapsed")
                                        
                                        st.markdown("---")
                                
                                # Display additional counterparties (user-added)
                                base_cp_count = len(counterparties[:5])
                                for extra_idx in range(st.session_state[extra_cp_key]):
                                    cp_idx = base_cp_count + extra_idx
                                    cp_key = f"{event_key}_cp_{cp_idx}"
                                    
                                    with st.container():
                                        st.markdown(f"**Counterparty #{cp_idx+1}** `NEW`")
                                        cp_col1, cp_col2 = st.columns(2)
                                        with cp_col1:
                                            st.text_input("Company Name", value="", key=f"{cp_key}_name", placeholder="Enter company name")
                                        with cp_col2:
                                            st.selectbox("Role", options=role_options, index=0, key=f"{cp_key}_role")
                                        
                                        cp_col3, cp_col4 = st.columns(2)
                                        with cp_col3:
                                            st.text_input("LinkedIn URL", value="", key=f"{cp_key}_linkedin", placeholder="https://linkedin.com/company/...")
                                        with cp_col4:
                                            st.text_input("Announcement URL", value="", key=f"{cp_key}_press", placeholder="https://...")
                                        
                                        # Add individual fields for new counterparty
                                        st.caption("👤 Key Individual (optional):")
                                        ind_col1, ind_col2 = st.columns(2)
                                        with ind_col1:
                                            st.text_input("Name", value="", key=f"{cp_key}_ind_0_name", placeholder="Person name")
                                        with ind_col2:
                                            st.text_input("Title", value="", key=f"{cp_key}_ind_0_title", placeholder="Job title")
                                        
                                        st.markdown("---")
                                
                                # Add counterparty button - using callback to avoid rerun issues
                                def add_counterparty_callback(cp_count_key, expander_key):
                                    if cp_count_key not in st.session_state:
                                        st.session_state[cp_count_key] = 0
                                    st.session_state[cp_count_key] += 1
                                    # Keep expander open
                                    st.session_state[expander_key] = True
                                
                                st.button(
                                    f"➕ Add Counterparty", 
                                    key=f"{event_key}_add_cp",
                                    on_click=add_counterparty_callback,
                                    args=(extra_cp_key, expander_open_key)
                                )
                                
                                # Show count of counterparties
                                total_cp = len(counterparties[:5]) + st.session_state.get(extra_cp_key, 0)
                                st.caption(f"Total counterparties: {total_cp}")
                                
                                # ADD TO DATABASE button - for ALL events
                                st.markdown("---")
                                if is_missing:
                                    st.warning("⚠️ This event is not in the database")
            else:
                                    st.info("✅ This event appears to match a database record")
                                
                                # Check if this event was already added (persisted in session state)
                                added_key = f"{event_key}_added_to_db"
                                if st.session_state.get(added_key):
                                    st.success(f"✅ Already added to database! Event ID: {st.session_state.get(f'{event_key}_event_id', 'N/A')}")
                                else:
                                    # Build payload first (before button click)
                                    # Use None for empty dates (API expects null, not empty string)
                                    closed_date_val = st.session_state.get(f"{event_key}_closed_date", closed_date)
                                    if not closed_date_val or closed_date_val.strip() == "":
                                        closed_date_val = None
                                    
                                    event_payload = {
                                        "title": st.session_state.get(f"{event_key}_name", event_name) or "Untitled Event",
                                        "announcement_date": st.session_state.get(f"{event_key}_ann_date", ann_date) or "2024-01-01",
                                        "closed_date": closed_date_val,
                                        "deal_type": st.session_state.get(f"{event_key}_type", deal_type) or "Acquisition",
                                        "deal_status": st.session_state.get(f"{event_key}_status", deal_status) or "Completed",
                                        "investment_amount": "",
                                        "currency_id": 15,
                                        "counterparties": []
                                    }
                                    
                                    # Collect counterparty data from existing AI counterparties
                                    for cp_idx, cp in enumerate(counterparties[:5]):
                                        cp_key = f"{event_key}_cp_{cp_idx}"
                                        
                                        individuals_list = []
                                        orig_individuals = cp.get("individuals", [])
                                        for ind_idx, ind in enumerate(orig_individuals[:3]):
                                            ind_key = f"{cp_key}_ind_{ind_idx}"
                                            ind_name = st.session_state.get(f"{ind_key}_name", ind.get("name", ""))
                                            ind_title = st.session_state.get(f"{ind_key}_title", ind.get("title", ""))
                                            if ind_name:
                                                individuals_list.append({
                                                    "name": ind_name,
                                                    "job_title": ind_title
                                                })
                                        
                                        cp_data = {
                                            "name": st.session_state.get(f"{cp_key}_name", cp.get("company_name", "")),
                                            "role": st.session_state.get(f"{cp_key}_role", cp.get("role", "")),
                                            "announcement_url": st.session_state.get(f"{cp_key}_press", cp.get("press_release_url", "")),
                                            "linkedin_url": st.session_state.get(f"{cp_key}_linkedin", cp.get("company_linkedin_url", "")),
                                            "individuals": individuals_list
                                        }
                                        event_payload["counterparties"].append(cp_data)
                                    
                                    # Collect additional counterparties (user-added)
                                    base_cp_count = len(counterparties[:5])
                                    extra_count = st.session_state.get(f"{event_key}_extra_cp_count", 0)
                                    for extra_idx in range(extra_count):
                                        cp_idx = base_cp_count + extra_idx
                                        cp_key = f"{event_key}_cp_{cp_idx}"
                                        
                                        # Get the company name - skip if empty
                                        cp_name = st.session_state.get(f"{cp_key}_name", "")
                                        if not cp_name:
                                            continue
                                        
                                        # Get individual for this counterparty
                                        individuals_list = []
                                        ind_name = st.session_state.get(f"{cp_key}_ind_0_name", "")
                                        ind_title = st.session_state.get(f"{cp_key}_ind_0_title", "")
                                        if ind_name:
                                            individuals_list.append({
                                                "name": ind_name,
                                                "job_title": ind_title
                                            })
                                        
                                        cp_data = {
                                            "name": cp_name,
                                            "role": st.session_state.get(f"{cp_key}_role", ""),
                                            "announcement_url": st.session_state.get(f"{cp_key}_press", ""),
                                            "linkedin_url": st.session_state.get(f"{cp_key}_linkedin", ""),
                                            "individuals": individuals_list
                                        }
                                        event_payload["counterparties"].append(cp_data)
                                    
                                    # Show payload preview
                                    with st.expander("📋 Preview Payload (JSON)", expanded=False):
                                        st.json(event_payload)
                                    
                                    # JavaScript-based API call (no page refresh!)
                                    import streamlit.components.v1 as components
                                    
                                    # Convert payload to JSON string for JavaScript
                                    payload_json_str = json.dumps(json.dumps(event_payload))
                                    api_endpoint = f"{XANO_BASE_URL}/api:617tZc8l/create_corporate_event"
                                    
                                    # HTML/JS component for API call
                                    api_button_html = f'''
                                    <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
                                        <button type="button" id="addToDbBtn_{idx}" onclick="sendToXano_{idx}()" 
                                            style="background: linear-gradient(90deg, #ff4b4b, #ff6b6b); 
                                                   color: white; 
                                                   border: none; 
                                                   padding: 12px 24px; 
                                                   border-radius: 8px; 
                                                   font-size: 16px; 
                                                   font-weight: 600;
                                                   cursor: pointer;
                                                   transition: all 0.3s ease;
                                                   box-shadow: 0 2px 8px rgba(255,75,75,0.3);">
                                            🚀 Add to Database
                                        </button>
                                        
                                        <div id="log_{idx}" style="margin-top: 15px; 
                                                                   padding: 15px; 
                                                                   background: #1e1e1e; 
                                                                   border-radius: 8px; 
                                                                   font-family: 'Monaco', 'Consolas', monospace;
                                                                   font-size: 13px;
                                                                   max-height: 300px;
                                                                   overflow-y: auto;
                                                                   display: none;">
                                        </div>
                                    </div>
                                    
                                    <script>
                                    function log_{idx}(msg, type) {{
                                        const logDiv = document.getElementById('log_{idx}');
                                        logDiv.style.display = 'block';
                                        const time = new Date().toLocaleTimeString();
                                        let color = '#69db7c';  // green
                                        if (type === 'error') color = '#ff6b6b';
                                        if (type === 'info') color = '#74c0fc';
                                        if (type === 'warn') color = '#ffd43b';
                                        logDiv.innerHTML += '<div style="color: ' + color + '; margin: 5px 0;">[' + time + '] ' + msg + '</div>';
                                        logDiv.scrollTop = logDiv.scrollHeight;
                                    }}
                                    
                                    async function sendToXano_{idx}() {{
                                        const btn = document.getElementById('addToDbBtn_{idx}');
                                        btn.disabled = true;
                                        btn.innerHTML = '⏳ Sending...';
                                        btn.style.background = '#666';
                                        
                                        const payload = JSON.parse({payload_json_str});
                                        const endpoint = '{api_endpoint}';
                                        
                                        log_{idx}('🔘 Button clicked - preparing API call...', 'info');
                                        log_{idx}('📡 Endpoint: ' + endpoint, 'info');
                                        log_{idx}('📋 Title: ' + payload.title, 'info');
                                        log_{idx}('👥 Counterparties: ' + payload.counterparties.length, 'info');
                                        log_{idx}('📦 Sending request...', 'warn');
                                        
                                        try {{
                                            const response = await fetch(endpoint, {{
                                                method: 'POST',
                                                headers: {{
                                                    'Content-Type': 'application/json',
                                                }},
                                                body: JSON.stringify(payload)
                                            }});
                                            
                                            log_{idx}('📡 Response Status: ' + response.status, 'info');
                                            
                                            const result = await response.json();
                                            
                                            if (result.success) {{
                                                log_{idx}('✅ SUCCESS! Event ID: ' + result.event_id, 'success');
                                                log_{idx}('🎉 Event: ' + result.event_description, 'success');
                                                log_{idx}('👥 Counterparties created: ' + result.total_counterparties, 'success');
                                                
                                                btn.innerHTML = '✅ Added!';
                                                btn.style.background = '#40c057';
                                                
                                                // Show counterparty details
                                                if (result.counterparties_created) {{
                                                    result.counterparties_created.forEach(cp => {{
                                                        log_{idx}('  → ' + cp.company_name + ' (' + cp.role + ') - ID: ' + cp.counterparty_id, 'success');
                                                    }});
                                                }}
                                            }} else {{
                                                log_{idx}('❌ API returned error: ' + JSON.stringify(result), 'error');
                                                btn.innerHTML = '❌ Failed';
                                                btn.style.background = '#fa5252';
                                                setTimeout(() => {{
                                                    btn.disabled = false;
                                                    btn.innerHTML = '🚀 Retry';
                                                    btn.style.background = 'linear-gradient(90deg, #ff4b4b, #ff6b6b)';
                                                }}, 3000);
                                            }}
                                        }} catch (error) {{
                                            log_{idx}('❌ Network Error: ' + error.message, 'error');
                                            btn.innerHTML = '❌ Error';
                                            btn.style.background = '#fa5252';
                                            setTimeout(() => {{
                                                btn.disabled = false;
                                                btn.innerHTML = '🚀 Retry';
                                                btn.style.background = 'linear-gradient(90deg, #ff4b4b, #ff6b6b)';
                                            }}, 3000);
                                        }}
                                    }}
                                    </script>
                                    '''
                                    
                                    components.html(api_button_html, height=400)
                        
                        if len(corporate_events) > 10:
                            st.caption(f"... and {len(corporate_events) - 10} more events")
                        
                        # Save button
                        st.markdown("---")
                        if st.button("💾 Save All Changes", key="save_ai_events", type="primary"):
                            st.success("✅ Changes saved to session! (Ready to push to database)")
                    else:
                        st.info("No events found")
                
                # ========== RIGHT COLUMN: Database ==========
                with col_db:
                    st.markdown("### 🗄️ Database (Xano)")
                    st.markdown("---")
                    
                    # Get nested Company data
                    company_info = db_company_data.get("Company", db_company_data)
                    
                    # Company Overview
                    st.markdown("#### 🏢 Company Overview")
                    
                    # Company name and basic info
                    company_name = company_info.get("name", "Unknown")
                    st.markdown(f"**{company_name}**")
                    
                    # Location
                    location_data = company_info.get("_locations", {})
                    if location_data:
                        city = location_data.get("City", "")
                        country = location_data.get("Country", "")
                        if city or country:
                            st.caption(f"📍 {city}, {country}" if city and country else f"📍 {city or country}")
                    
                    # Ownership type
                    ownership = company_info.get("_ownership_type", {})
                    if ownership:
                        st.caption(f"🏛️ {ownership.get('ownership', 'Unknown')}")
                    
                    # LinkedIn
                    linkedin_data = company_info.get("linkedin_data", {})
                    if linkedin_data:
                        linkedin_url = linkedin_data.get("LinkedIn_URL", "")
                        linkedin_emp = linkedin_data.get("LinkedIn_Employee", 0)
                        if linkedin_url:
                            st.markdown(f"[🔗 LinkedIn]({linkedin_url}) ({linkedin_emp} employees)")
                    
                    # Website
                    website = company_info.get("url", "")
                    if website:
                        st.markdown(f"[🌐 Website]({website})")
                    
                    # Description
                    db_desc = company_info.get("description", "")
                    if db_desc:
                        st.markdown("---")
                        st.markdown(db_desc)
                    
                    st.markdown("---")
                    
                    # Corporate Events from dedicated API
                    st.markdown(f"#### 📅 Corporate Events ({len(db_corporate_events)} found)")
                    if db_corporate_events:
                        for event in db_corporate_events[:10]:
                            event_desc = event.get("description", "Unknown event")
                            ann_date = event.get("announcement_date", "")
                            closed_date = event.get("closed_date", "")
                            deal_type = event.get("deal_type", "")
                            deal_status = event.get("deal_status", "")
                            ev_display = event.get("ev_display", "")
                            
                            st.markdown(f"**{event_desc}**")
                            col_date1, col_date2 = st.columns(2)
                            with col_date1:
                                if ann_date:
                                    st.caption(f"📢 {ann_date}")
                            with col_date2:
                                if closed_date:
                                    st.caption(f"✅ {closed_date}")
                            
                            status_str = f"🏷️ {deal_type}"
                            if deal_status:
                                status_str += f" | {deal_status}"
                            if ev_display:
                                status_str += f" | 💰{ev_display}m"
                            st.caption(status_str)
                            
                            # Target company
                            target = event.get("target_company")
                            if target and target.get("name"):
                                target_url = target.get("counterparty_announcement_url", "")
                                if target_url:
                                    st.markdown(f"  • **{target.get('name')}** `Target` [📄]({target_url})")
                                else:
                                    st.markdown(f"  • **{target.get('name')}** `Target`")
                            
                            # Other counterparties (investors, sellers, etc.)
                            other_cps = event.get("other_counterparties", [])
                            for cp in other_cps[:4]:
                                cp_name = cp.get("name", "Unknown")
                                cp_status = cp.get("counterparty_status", "")
                                cp_url = cp.get("counterparty_announcement_url", "")
                                if cp_url:
                                    st.markdown(f"  • **{cp_name}** `{cp_status}` [📄]({cp_url})")
                                else:
                                    st.markdown(f"  • **{cp_name}** `{cp_status}`")

                            # Advisors
                            advisors = event.get("advisors", [])
                            if advisors:
                                advisor_names = [a.get("advisor_company", {}).get("name", "") for a in advisors if a.get("advisor_company")]
                                if advisor_names:
                                    st.caption(f"  🎯 Advisors: {', '.join(advisor_names[:3])}")
                            
                            st.markdown("---")
                        if len(db_corporate_events) > 10:
                            st.caption(f"... and {len(db_corporate_events) - 10} more events")
                    else:
                        st.info("No events in database")
                    
                    # Investors
                    st.markdown("#### 💰 Investors")
                    db_investors = company_info.get("_companies_investors", [])
                    if db_investors:
                        for inv in db_investors:
                            inv_name = inv.get('company_name', 'Unknown')
                            inv_id = inv.get('original_new_company_id', '')
                            st.markdown(f"• **{inv_name}**")
                    else:
                        st.info("No investors listed")
                
                # ============================================================
                # 🔹 GAP ANALYSIS: Events missing from database
                # ============================================================
                if missing_events:
                    st.markdown("---")
                    st.markdown("## ⚠️ Gap Analysis: Events Potentially Missing from Database")
                    st.warning(f"**{len(missing_events)} events** found by AI but not matched in your database")
                    
                    for idx, event in enumerate(missing_events, 1):
                        gap_key = f"gap_event_{idx}"
                        event_name = event.get("Event (short)", event.get("event_short", "Unknown"))
                        ann_date = event.get("Announcement Date", event.get("announcement_date", "N/A"))
                        closed_date = event.get("Closed Date", event.get("closed_date", ""))
                        deal_type = event.get("Deal Type", event.get("deal_type", ""))
                        deal_status = event.get("Deal Status", event.get("deal_status", ""))
                        source_url = event.get("Source URL", event.get("source_url", ""))
                        counterparties = event.get("counterparties", [])
                        
                        with st.container():
                            cols = st.columns([1, 5, 2])
                            with cols[0]:
                                st.markdown(f"### #{idx}")
                            with cols[1]:
                                st.markdown(f"**{event_name}**")
                                
                                date_info = f"📢 Announced: {ann_date}"
                                if closed_date:
                                    date_info += f" | ✅ Closed: {closed_date}"
                                st.caption(date_info)
                                
                                st.caption(f"🏷️ Type: {deal_type}")
                                
                                # Show counterparties for context
                                if counterparties:
                                    cp_names = [f"{cp.get('company_name', 'Unknown')} ({cp.get('role', '')})" for cp in counterparties[:4]]
                                    st.caption(f"👥 Parties: {', '.join(cp_names)}")
                                
                                if source_url:
                                    st.markdown(f"[📎 Source]({source_url})")
                            
                            with cols[2]:
                                # Build payload for this event
                                closed_date_val = closed_date if closed_date and closed_date.strip() else None
                                gap_payload = {
                                    "title": event_name,
                                    "announcement_date": ann_date if ann_date != "N/A" else "2024-01-01",
                                    "closed_date": closed_date_val,
                                    "deal_type": deal_type or "Acquisition",
                                    "deal_status": deal_status or "Completed",
                                    "investment_amount": "",
                                    "currency_id": 15,
                                    "counterparties": []
                                }
                                
                                # Add counterparties to payload
                                for cp in counterparties:
                                    individuals_list = []
                                    for ind in cp.get("individuals", []):
                                        if ind.get("name"):
                                            individuals_list.append({
                                                "name": ind.get("name", ""),
                                                "job_title": ind.get("title", "")
                                            })
                                    
                                    gap_payload["counterparties"].append({
                                        "name": cp.get("company_name", ""),
                                        "role": cp.get("role", cp.get("role_description", "")) or "Target",
                                        "announcement_url": cp.get("press_release_url", ""),
                                        "linkedin_url": cp.get("company_linkedin_url", ""),
                                        "individuals": individuals_list
                                    })
                                
                                # JavaScript button (no page refresh)
                                gap_payload_json_str = json.dumps(json.dumps(gap_payload))
                                gap_endpoint = f"{XANO_BASE_URL}/api:617tZc8l/create_corporate_event"
                                
                                gap_btn_html = f'''
                                <button type="button" id="gapBtn_{idx}" onclick="addGapEvent_{idx}()" 
                                    style="background: linear-gradient(90deg, #ff4b4b, #ff6b6b); 
                                           color: white; border: none; padding: 8px 16px; 
                                           border-radius: 6px; font-size: 14px; font-weight: 600;
                                           cursor: pointer; width: 100%;">
                                    🚀 Add to DB
                                </button>
                                <div id="gapStatus_{idx}" style="margin-top: 8px; font-size: 12px;"></div>
                                
                                <script>
                                async function addGapEvent_{idx}() {{
                                    const btn = document.getElementById('gapBtn_{idx}');
                                    const status = document.getElementById('gapStatus_{idx}');
                                    btn.disabled = true;
                                    btn.innerHTML = '⏳...';
                                    status.innerHTML = '<span style="color: #74c0fc;">Sending...</span>';
                                    
                                    try {{
                                        const response = await fetch('{gap_endpoint}', {{
                                            method: 'POST',
                                            headers: {{'Content-Type': 'application/json'}},
                                            body: JSON.stringify(JSON.parse({gap_payload_json_str}))
                                        }});
                                        const result = await response.json();
                                        if (result.success) {{
                                            btn.innerHTML = '✅ Added!';
                                            btn.style.background = '#40c057';
                                            status.innerHTML = '<span style="color: #69db7c;">ID: ' + result.event_id + '</span>';
                                        }} else {{
                                            btn.innerHTML = '❌ Error';
                                            btn.style.background = '#fa5252';
                                            status.innerHTML = '<span style="color: #ff6b6b;">' + (result.message || 'Failed') + '</span>';
                                        }}
                                    }} catch (e) {{
                                        btn.innerHTML = '❌ Error';
                                        status.innerHTML = '<span style="color: #ff6b6b;">' + e.message + '</span>';
                                    }}
                                }}
                                </script>
                                '''
                                components.html(gap_btn_html, height=80)
                            
                            st.markdown("---")
                    
                    st.info("💡 **Tip:** Edit events in the AI Analysis section above before adding, or use quick add buttons here.")
                else:
                    st.markdown("---")
                    st.success("✅ **All AI-found events matched with database records!**")
                
                # Full database record in expander
                st.markdown("---")
                with st.expander("📋 View Full Database Record (JSON)", expanded=False):
                    st.json(db_company_data)
                
                with st.expander("📋 View Database Corporate Events (JSON)", expanded=False):
                    st.json(db_corporate_events)
            
            else:
                # ========== SINGLE COLUMN: New company (no database match) ==========
                st.success("✅ Company data successfully fetched!")

                st.subheader("🏢 Company Overview")
                st.markdown(description if description else "_No description available_")

                st.subheader("📅 Corporate Events")
                show_corporate_events(corporate_events)

        except Exception as e:
            status.text("")
            st.error(f"⚠️ Error during analysis: {e}")

# ============================================================
# 🔹 Previous Valuation Reports
# ============================================================
st.divider()
st.subheader("🗂️ Previous Valuation Reports")

reports = get_reports()
if reports:
    for idx, r in enumerate(reports):
        with st.expander(f"📊 {r.get('company', 'Unknown Company')}"):
            
            # Company Overview
            st.subheader("🏢 Company Overview")
            st.write(r.get('description', 'No description available.'))

            # Corporate Events
            st.subheader("📅 Corporate Events")
            corp_data_raw = r.get("corporate_events")

            corp_data = []
            if isinstance(corp_data_raw, str):
                try:
                    corp_data = json.loads(corp_data_raw)
                    if isinstance(corp_data, str):  
                        corp_data = json.loads(corp_data)
                except:
                    corp_data = []
            elif isinstance(corp_data_raw, list):
                corp_data = corp_data_raw

            show_corporate_events(corp_data)
