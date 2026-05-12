# Helpers for sharded corporate-events extraction (Serp snippets → parallel LLM → merge/dedupe).

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# Strict C-suite for individuals / management (must match extraction rules + LinkedIn filter).
C_SUITE_TITLE_ABBREV = frozenset({"CEO", "CFO", "COO", "CTO", "CRO", "CISO", "CPO", "CMO", "CLO"})

# Deal-type buckets mirror index.html isFundingLikeDealType / isDealTermsHeavyDealType helpers.
_FUNDING_DEAL_TYPES = frozenset({
    "investment", "grant", "debt financing", "accelerator", "award",
    "employee tender offer",
})

def _is_funding_deal_type(deal_type: str) -> bool:
    """True for investment/funding-style deals (source goes into investment_data)."""
    s = (deal_type or "").lower().strip()
    if any(s.startswith(t) or s == t for t in _FUNDING_DEAL_TYPES):
        return True
    return "series " in s or s.startswith("series")

def _is_deal_terms_deal_type(deal_type: str) -> bool:
    """True for M&A/exit-style deals (source goes into deal_terms_data)."""
    s = (deal_type or "").lower().strip()
    _DT = (
        "acquisition", "sale", "merger", "divestment", "mbo", "ipo",
        "closing", "reorganisation", "reorganization", "bankruptcy",
        "dual track", "strategic review", "rebrand", "partnership",
    )
    return any(s.startswith(k) or k in s for k in _DT)

# Investor-side titles treated as deal principals for VC/PE counterparties.
_INVESTOR_TITLES = frozenset(
    {
        "GP",
        "MD",
        "PARTNER",
        "GENERALPARTNER",
        "MANAGINGPARTNER",
        "MANAGINGDIRECTOR",
        "PRINCIPALPARTNER",
    }
)


def title_is_strict_csuite(title: str) -> bool:
    """True if title is Chief…, C-level token, or investor-equivalent senior title at funds/PE."""
    if not title or not str(title).strip():
        return False
    t = str(title).strip()
    if t.lower().startswith("chief"):
        return True
    upper = t.upper()
    parts = re.split(r"[\s,;/|\-\(\)\[\]&]+", upper)
    parts = {p.strip("., ") for p in parts if p.strip("., ")}
    if C_SUITE_TITLE_ABBREV & parts:
        return True
    normalized = upper.replace(" ", "").replace("-", "")
    return any(inv in normalized for inv in _INVESTOR_TITLES)

import requests


def corporate_events_shard_size() -> int:
    """Queries per LLM shard. Set CORPORATE_EVENTS_SHARD_SIZE=0 to disable sharding (legacy single LLM call)."""
    raw = os.getenv("CORPORATE_EVENTS_SHARD_SIZE", "3").strip()
    try:
        n = int(raw)
        return n
    except ValueError:
        return 3


def format_ce_journal_from_hits(hits: List[dict]) -> str:
    """Numbered journal string from Serp hit dicts (title, snippet, link, published_date)."""
    lines = []
    for i, result in enumerate(hits, 1):
        pd = (result.get("published_date") or "").strip()
        pd_line = f"Search result date: {pd}\n" if pd else ""
        lines.append(
            f"[{i}] {result.get('title', '')}\n{result.get('snippet', '')}\n"
            f"{pd_line}"
            f"Source: {result.get('link', '')}\n\n"
        )
    return "".join(lines)


def _ce_keyword_set(text: str) -> set:
    stop = {
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
    words = set((text or "").lower().split())
    return words - stop


def ce_rows_jaccard_similar(a: Dict[str, Any], b: Dict[str, Any], threshold: float = 0.42) -> bool:
    """Same spirit as server.py events_match — overlap / max(|ka|,|kb|)."""
    blob_a = " ".join(
        [
            str(a.get("Event (short)", a.get("event_short", ""))),
            str(a.get("Deal Type", "")),
            str(a.get("Announcement Date", "")),
        ]
    )
    blob_b = " ".join(
        [
            str(b.get("Event (short)", b.get("event_short", ""))),
            str(b.get("Deal Type", "")),
            str(b.get("Announcement Date", "")),
        ]
    )
    ka = _ce_keyword_set(blob_a)
    kb = _ce_keyword_set(blob_b)
    if not ka or not kb:
        return False
    overlap = len(ka & kb)
    mx = max(len(ka), len(kb))
    return (overlap / mx) >= threshold if mx else False


def ce_row_richness(ev: Dict[str, Any]) -> float:
    desc = len(ev.get("Description") or "")
    url = len(ev.get("Source URL") or "")
    val = str(ev.get("Event value (USD)", "") or "")
    amt_bonus = 55.0 if val and "undisclosed" not in val.lower() else 0.0
    cp_bonus = sum(len(str(cp.get("company_name", ""))) for cp in ev.get("counterparties") or []) * 0.05
    return float(desc + url + amt_bonus + cp_bonus)


def merge_dedupe_ce_rows(rows: List[dict], max_events: int, threshold: float = 0.42) -> List[dict]:
    """Prefer richer rows when collapsing duplicates (same deal from multiple shards)."""
    if not rows:
        return []
    ordered = sorted(rows, key=ce_row_richness, reverse=True)
    kept: List[dict] = []
    for ev in ordered:
        if any(ce_rows_jaccard_similar(ev, k, threshold) for k in kept):
            continue
        kept.append(ev)
        if len(kept) >= max(max_events * 4, 80):
            break
    return kept[:max_events]


def normalize_llm_ce_events_to_rows(
    events: Optional[List[dict]],
    company_name: str,
    max_take: int,
) -> List[dict]:
    """Map raw Claude JSON objects to UI / server event dicts (same shape as legacy loop)."""
    if not events or not isinstance(events, list):
        return []

    result: List[dict] = []
    legal_keywords = ["legal", "law", "counsel", "attorney", "solicitor", "llp", "lawyers"]
    known_law_firms = [
        "skadden",
        "sullivan & cromwell",
        "sullivan cromwell",
        "wachtell",
        "kirkland",
        "simpson thacher",
        "latham",
        "davis polk",
        "freshfields",
        "clifford chance",
        "allen & overy",
        "linklaters",
        "white & case",
        "cleary gottlieb",
        "cravath",
        "debevoise",
        "paul weiss",
        "weil gotshal",
        "milbank",
        "gibson dunn",
        "sidley",
        "jones day",
        "baker mckenzie",
        "hogan lovells",
        "norton rose",
        "dla piper",
        "herbert smith",
        "ashurst",
        "slaughter and may",
        "cooley",
    ]

    for e in events[:max_take]:
        if not isinstance(e, dict):
            continue
        counterparties = []
        raw_counterparties = e.get("counterparties", []) or []
        for cp in raw_counterparties:
            if not isinstance(cp, dict):
                continue
            individuals = []
            for ind in cp.get("individuals", []) or []:
                if isinstance(ind, dict):
                    nm = str(ind.get("name", "")).strip()
                    tl = str(ind.get("title", "")).strip()
                    if not nm or not tl or not title_is_strict_csuite(tl):
                        continue
                    individuals.append(
                        {
                            "name": nm,
                            "title": tl,
                            "linkedin_url": str(ind.get("linkedin_url", "")).strip(),
                        }
                    )
            counterparties.append(
                {
                    "company_name": str(cp.get("company_name", "")).strip(),
                    "type_id": int(cp.get("type_id", 0)),
                    "type": str(cp.get("type", "Unknown")).strip(),
                    "role_description": str(cp.get("role_description", "")).strip(),
                    "company_linkedin_url": str(cp.get("company_linkedin_url", "")).strip(),
                    "press_release_url": str(cp.get("press_release_url", "")).strip(),
                    "individuals": individuals,
                }
            )

        announcement_date = str(e.get("announcement_date", e.get("date", ""))).strip()
        closed_date = str(e.get("closed_date", "")).strip()
        display_date = announcement_date if announcement_date else closed_date
        if not display_date:
            display_date = "Unknown"

        deal_type = str(e.get("deal_type", e.get("event_type", "Unknown"))).strip()
        deal_status = str(e.get("deal_status", "")).strip()
        if not deal_status and closed_date:
            deal_status = "Completed"
        elif not deal_status:
            deal_status = "Unknown"

        advisors = []
        for adv in e.get("advisors", []) or []:
            if not adv or not isinstance(adv, dict):
                continue
            adv_name = str(adv.get("advisor_name", "")).strip()
            adv_type = str(adv.get("advisor_type", "")).strip()
            adv_name_lower = adv_name.lower()
            adv_type_lower = adv_type.lower()
            if "legal" in adv_type_lower:
                continue
            if any(law_firm in adv_name_lower for law_firm in known_law_firms):
                continue
            has_legal_keyword = any(kw in adv_name_lower for kw in legal_keywords)
            if has_legal_keyword and "financial" not in adv_type_lower:
                continue
            advisors.append(
                {
                    "advisor_name": adv_name,
                    "advisor_type": adv_type,
                    "advised_party": str(adv.get("advised_party", "")).strip(),
                    "announcement_url": str(adv.get("announcement_url", "")).strip(),
                }
            )

        source_url = str(e.get("source_url", "")).strip()

        # ── New optional LLM fields ──────────────────────────────────────────
        raw_amount_m = e.get("investment_amount_m")
        try:
            inv_amount_m: Optional[float] = float(raw_amount_m) if raw_amount_m not in (None, "", "null") else None
        except (TypeError, ValueError):
            inv_amount_m = None

        inv_currency = str(e.get("investment_currency", "")).strip()
        funding_stage = str(e.get("funding_stage", "")).strip()
        deal_terms_text = str(e.get("deal_terms", "")).strip()

        # ── Route source URL to the correct sub-object ──────────────────────
        is_funding = _is_funding_deal_type(deal_type)
        is_deal = _is_deal_terms_deal_type(deal_type)

        investment_data: Dict[str, Any] = {
            "investment_amount_source": source_url if is_funding else "",
            "investment_amount_m": inv_amount_m,
            "currency_id": 0,          # ID lookup happens on the Xano side
            "Funding_stage": funding_stage,
            "investment_currency_code": inv_currency,  # ISO code extracted by LLM; UI maps to currency_id
        }
        deal_terms_data: Dict[str, Any] = {
            "deal_terms": deal_terms_text,
            "deal_terms_source": source_url if is_deal else "",
        }
        # Fallback: if type is neither funding nor deal-terms, put source in both so
        # the UI can show whichever field is relevant after the user sets the type.
        if not is_funding and not is_deal and source_url:
            investment_data["investment_amount_source"] = source_url
            deal_terms_data["deal_terms_source"] = source_url

        ev_data: Dict[str, Any] = {
            "ev_source": "",
            "EV_source_type": "",
            "enterprise_value_m": "",
            "currency_id": 0,
            "ev_band": "",
        }

        result.append(
            {
                "Announcement Date": announcement_date,
                "Closed Date": closed_date,
                "Date": display_date,
                "Event (short)": str(e.get("event_short", e.get("event", "Unknown event"))).strip(),
                "Description": str(e.get("description", "")).strip(),
                "Deal Type": deal_type,
                "Deal Status": deal_status,
                "Event type": deal_type,
                "Event value (USD)": str(e.get("value_usd", e.get("value", "Undisclosed"))).strip(),
                "Source URL": source_url,
                "investment_data": investment_data,
                "deal_terms_data": deal_terms_data,
                "ev_data": ev_data,
                "True_EV_and_Revs_or_EBITDA_disclosed": False,
                "counterparties": counterparties,
                "advisors": advisors,
            }
        )

    _ = company_name  # reserved for future logging
    return result


def openrouter_extract_ce_events_json(
    prompt: str,
    openrouter_key: str,
    *,
    max_tokens: int = 32000,
    timeout: int = 180,
    max_retries: int = 2,
    connect_timeout: Optional[float] = None,
) -> List[dict]:
    """POST to OpenRouter; parse JSON array from reply; retry on failure."""
    if not openrouter_key:
        return []
    if connect_timeout is None:
        try:
            connect_timeout = float(os.getenv("CORPORATE_EVENTS_OPENROUTER_CONNECT_TIMEOUT", "30"))
        except ValueError:
            connect_timeout = 30.0
    read_timeout = float(timeout)
    timeouts = (max(5.0, connect_timeout), max(30.0, read_timeout))
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "anthropic/claude-sonnet-4.6",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
                timeout=timeouts,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON array in model response")
            parsed = json.loads(raw[start:end])
            if isinstance(parsed, list) and len(parsed) == 0:
                print(
                    f"   ⚠️ OpenRouter CE returned empty events array "
                    f"(preview: {raw[:160].replace(chr(10), ' ')!r})",
                    flush=True,
                )
            return parsed
        except Exception as ex:
            last_err = ex
            if attempt < max_retries:
                time.sleep(1.2 * (attempt + 1))
            else:
                print(f"   ⚠️ OpenRouter CE extract failed after retries: {last_err}")
    return []
