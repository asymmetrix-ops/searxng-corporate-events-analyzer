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
    Displays corporate events with counterparty details.
    
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

    # Sort events by date (newest first)
    def parse_date(event):
        try:
            date_str = event.get("Date", event.get("date", ""))
            return pd.to_datetime(date_str, errors="coerce")
        except:
            return pd.NaT
    
    sorted_events = sorted(corporate_events, key=parse_date, reverse=True)

    st.markdown("### 📊 Corporate Events Timeline")
    
    # Count total counterparties
    total_counterparties = sum(len(e.get("counterparties", [])) for e in sorted_events)
    st.caption(f"📈 {len(sorted_events)} events • 👥 {total_counterparties} counterparties")
    
    # Display each event with expandable counterparty details
    for i, event in enumerate(sorted_events):
        date = event.get("Date", event.get("date", "Unknown"))
        event_short = event.get("Event (short)", event.get("event_short", "Unknown event"))
        event_type = event.get("Event type", event.get("event_type", "Unknown"))
        value = event.get("Event value (USD)", event.get("value_usd", "Undisclosed"))
        source_url = event.get("Source URL", event.get("source_url", ""))
        counterparties = event.get("counterparties", [])
        
        # Event card
        with st.container():
            col1, col2 = st.columns([1, 4])
            
            with col1:
                st.markdown(f"**📅 {date}**")
                # Event type badge
                type_colors = {
                    "Acquisition": "🟢",
                    "Merger": "🔵",
                    "Divestiture": "🟠",
                    "Investment": "🟣",
                    "IPO": "🟡"
                }
                badge = next((v for k, v in type_colors.items() if k.lower() in event_type.lower()), "⚪")
                st.caption(f"{badge} {event_type}")
            
            with col2:
                st.markdown(f"**{event_short}**")
                # Value and source URL on same line
                if source_url:
                    st.caption(f"💰 {value} • [📎 Source]({source_url})")
                else:
                    st.caption(f"💰 {value}")
                
                # Counterparties section
                if counterparties:
                    with st.expander(f"👥 View {len(counterparties)} Counterparties"):
                        cp_data = []
                        for cp in counterparties:
                            cp_type = cp.get("type", "Unknown")
                            type_id = cp.get("type_id", 0)
                            
                            # Role icons
                            role_icons = {
                                "Target": "🎯",
                                "Acquirer": "🏢",
                                "Seller": "📤",
                                "Investor": "💼",
                                "Joint Venture": "🤝"
                            }
                            icon = next((v for k, v in role_icons.items() if k.lower() in cp_type.lower()), "👤")
                            
                            cp_data.append({
                                "Role": f"{icon} {cp_type}",
                                "Company": cp.get("company_name", "Unknown"),
                                "Type ID": f"CP-{type_id}",
                                "Description": cp.get("role_description", "-")
                            })
                        
                        st.dataframe(
                            pd.DataFrame(cp_data),
                            hide_index=True,
                            use_container_width=True
                        )
            
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
# 🔹 Search Input
# ============================================================
search_query = st.text_input("🔎 Enter company/topic (or paste URL directly)", placeholder="Google, ChatGPT, or https://example.com")

# ============================================================
# 🔹 Fetch Search Results
# ============================================================
if search_query.strip():
    st.subheader("🔗 Top Page 1 Search Results")
    try:
        params = {"q": search_query, "hl": "en", "gl": "us", "num": 10, "api_key": SERPAPI_KEY}
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        if results:
            for idx, res in enumerate(results):
                title = res.get("title") or res.get("link", "")
                link = res.get("link", "")
                st.markdown(f"{idx + 1}. [{title}]({link})")
        else:
            st.info("No search results found for this query.")
    except Exception as e:
        st.error(f"Error fetching search results: {e}")

# ============================================================
# 🔹 Analyze Company
# ============================================================
if st.button("🚀 Analyze Company"):
    if not search_query.strip():
        st.warning("⚠️ Please enter a company name or URL")
    else:
        progress = st.progress(0)
        status = st.empty()

        summary, description, corporate_events, mgmt_list, mgmt_text, subsidiaries = "", "", [], [], "", []
        try:
            status.text("📘 Reading company background...")
            wiki_text = get_wikipedia_summary(search_query)
            progress.progress(20)

            status.text("🧠 Extracting company structure...")
            summary = generate_summary(search_query, text=wiki_text)
            progress.progress(40)

            status.text("📝 Writing company profile...")
            description = generate_description(search_query, text=wiki_text, company_details=summary)
            progress.progress(60)

            status.text("📅 Fetching corporate events...")
            corporate_events = generate_corporate_events(search_query)
            progress.progress(75)

            status.text("👥 Fetching top management...")
            mgmt_list, mgmt_text = get_top_management(search_query, text=wiki_text)
            progress.progress(85)

            status.text("🏢 Fetching subsidiaries...")
            subsidiaries = get_subsidiaries(search_query) or generate_subsidiary_data(search_query)
            progress.progress(95)

            # Store report and search data
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
            

            st.success("✅ Company data successfully fetched!")
            progress.progress(100)
            status.text("✅ Done")

            st.subheader("📈 Valuation Summary Report")
            st.markdown(summary)

            st.subheader("🏢 Company Description")
            st.text(description)

            st.subheader("📅 Corporate Events")
            show_corporate_events(corporate_events)

            st.subheader("👥 Top Management")
            show_top_management(mgmt_list)

            st.subheader("🏢 Subsidiaries")
            if subsidiaries:
                show_subsidiaries(subsidiaries)
            else:
                st.info("No subsidiaries found for this company.")


            events_text = f"\n\nCorporate Events:\n{json.dumps(corporate_events)}" if corporate_events else ""
            # mgmt_text_pdf = f"\n\nTop Management:\n{mgmt_text}" if mgmt_text else ""
            # pdf_file = create_pdf_from_text(title=search_query, summary=f"{description}\n\n{summary}{events_text}{mgmt_text_pdf}")

            # st.download_button("📄 Download PDF", data=pdf_file, file_name=f"{search_query.replace(' ', '_')}.pdf", mime="application/pdf")

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
            
            # Summary
            st.subheader("📈 Valuation Summary Report")
            st.write(r.get('summary', 'No summary available.'))

            # Description
            st.subheader("🏢 Company Description")
            st.write(r.get('description', 'No description available.'))

            # Events
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

            # Management
            st.subheader("👥 Top Management")
            mgmt_list = normalize_top_management(r.get("top_management"))
            show_top_management(mgmt_list)

            # Subsidiaries
            st.subheader("🏢 Subsidiaries")
            subsidiaries_data = get_subsidiaries(r.get("company", ""))
            if subsidiaries_data:
                show_subsidiaries(subsidiaries_data, context_label=f"report_{idx}")
            else:
                st.info("No subsidiaries found for this company.")

            # PDF Download
            # pdf_file = create_pdf_from_text(
            #     title=r.get('company', 'Report'),
            #     summary=f"{r.get('description', '')}\n\n{r.get('summary', '')}"
            # )
            # st.download_button(
            #     "📄 Download PDF",
            #     data=pdf_file,
            #     file_name=f"{r.get('company', 'report').replace(' ', '_')}.pdf",
            #     mime="application/pdf",
            #     key=f"download_report_{idx}"
            # )

