"""Daily profile-completeness job.

Scans Xano for Dashboard companies with missing AI cards, runs the matching
Search Engine generator, and saves the result to Xano as a draft
(Profile_Card_Drafts). Drafts are never published here; a human reviews them.
"""

import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

XANO_BASE_URL = os.getenv("XANO_BASE_URL", "https://xdil-abvj-o7rq.e2.xano.io").rstrip("/")
XANO_AUTH_PATH = os.getenv("XANO_AUTH_PATH", "/api:vnXelut6/auth/login")
XANO_SCAN_GROUP = os.getenv("XANO_SCAN_GROUP", "/api:VP2Spm80")
XANO_DETAILS_GROUP = os.getenv("XANO_DETAILS_GROUP", "/api:1HMAPNSY")
SEARCH_ENGINE_URL = os.environ["SEARCH_ENGINE_URL"].rstrip("/")
SERVICE_EMAIL = os.environ["SERVICE_EMAIL"]
SERVICE_PASSWORD = os.environ["SERVICE_PASSWORD"]

PROMPT_VERSION = os.getenv("PROMPT_VERSION", "search-engine@v1")
REJECT_COOLDOWN_DAYS = int(os.getenv("REJECT_COOLDOWN_DAYS", "14"))
ATTEMPT_MAX_FAILS = int(os.getenv("ATTEMPT_MAX_FAILS", "1"))
ATTEMPT_COOLDOWN_DAYS = int(os.getenv("ATTEMPT_COOLDOWN_DAYS", "3"))
MAX_COMPANIES_PER_RUN = int(os.getenv("MAX_COMPANIES_PER_RUN", "100"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "180"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
POLL_TIMEOUT = int(os.getenv("POLL_TIMEOUT", "600"))
RETRIES = int(os.getenv("RETRIES", "3"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
PAYLOAD_AS_STRING = os.getenv("PAYLOAD_AS_STRING", "0") == "1"

CLASSIFICATION_KEYS = {"product_type", "revenue_model", "data_collection"}
PRODUCTS_USERS_KEYS = {"core_products", "users_use_cases"}
KNOWN_KEYS = CLASSIFICATION_KEYS | PRODUCTS_USERS_KEYS | {"description"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("profile_cards_daily")

_session = requests.Session()
_token = ""
_token_lock = threading.Lock()


def login(force=False):
    global _token
    with _token_lock:
        if _token and not force:
            return _token
        r = requests.post(
            XANO_BASE_URL + XANO_AUTH_PATH,
            json={"email": SERVICE_EMAIL, "password": SERVICE_PASSWORD},
            timeout=30,
        )
        r.raise_for_status()
        _token = (r.json().get("authToken") or "").strip()
        if not _token:
            raise RuntimeError("login returned no authToken")
        return _token


def call(method, url, **kwargs):
    """HTTP call with bearer auth, one re-login on 401, retries on 5xx/429/network."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    relogged = False
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            headers = {"Authorization": f"Bearer {login()}"}
            r = _session.request(method, url, headers=headers, **kwargs)
            if r.status_code == 401 and not relogged:
                relogged = True
                login(force=True)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}", response=r)
            return r
        except requests.RequestException as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise last


def parse_ts(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def needs_generation(card, now):
    """Generate only for missing cards; rejected ones after a cooldown.
    draft (already waiting for review) and done are never regenerated."""
    if card["card_key"] not in KNOWN_KEYS:
        return False
    state = card.get("state")
    if state == "rejected":
        decided = parse_ts(card.get("last_decided_at"))
        if decided is not None and now - decided < timedelta(days=REJECT_COOLDOWN_DAYS):
            return False
    elif state != "missing":
        return False
    # The generator already came back empty or failed for this card: do not
    # burn tokens on it every day.
    if int(card.get("attempts") or 0) >= ATTEMPT_MAX_FAILS:
        return False
    last_try = parse_ts(card.get("last_attempt_at"))
    if last_try is not None and now - last_try < timedelta(days=ATTEMPT_COOLDOWN_DAYS):
        return False
    return True


def fetch_scan():
    r = call(
        "GET",
        f"{XANO_BASE_URL}{XANO_SCAN_GROUP}/profile_completeness/scan",
        params={"no_paging": "true"},
    )
    r.raise_for_status()
    data = r.json()
    return data["items"] if isinstance(data, dict) else data


def as_list(value):
    """Xano returns some scan fields (cards, reasons) as JSON-encoded strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def build_jobs(scan):
    now = datetime.now(timezone.utc)
    jobs = []
    for company in scan:
        cards = [c for c in as_list(company.get("cards")) if isinstance(c, dict)]
        keys = {c["card_key"] for c in cards if needs_generation(c, now)}
        if keys:
            jobs.append(
                {
                    "company_id": company["company_id"],
                    "company_name": company.get("company_name"),
                    "keys": keys,
                    "attempts": {
                        c["card_key"]: int(c.get("attempts") or 0) for c in cards
                    },
                }
            )
    return jobs


def get_details(company_id):
    r = call(
        "GET",
        f"{XANO_BASE_URL}{XANO_DETAILS_GROUP}/get_company_details",
        params={"company_id": company_id},
    )
    r.raise_for_status()
    rows = r.json()
    if isinstance(rows, list):
        return rows[0] if rows else None
    return rows or None


def flat_sectors(sectors):
    if isinstance(sectors, str):
        try:
            sectors = json.loads(sectors)
        except ValueError:
            return []
    if not isinstance(sectors, dict):
        return []
    return [
        s["sector_name"]
        for s in sectors.get("primary_sectors") or []
        if isinstance(s, dict) and s.get("sector_name")
    ]


def description_payload(company_id, d):
    fm = {
        "revenue_m": d.get("Revenue_m"),
        "ebitda_m": d.get("EBITDA_m"),
        "ev_m": d.get("EV"),
        "arr_m": d.get("ARR_m"),
    }
    body = {
        "company_id": company_id,
        "name": d.get("name"),
        "website": d.get("url"),
        "city": d.get("city"),
        "country": d.get("country"),
        "year_founded": d.get("year_founded"),
        "ownership_type": d.get("ownership_type"),
        "linkedin_employee": d.get("linkedin_employee"),
        "sectors": flat_sectors(d.get("sectors")),
    }
    if any(v is not None for v in fm.values()):
        body["financial_metrics"] = fm
    return body


def is_empty(payload):
    if payload is None:
        return True
    if isinstance(payload, str):
        return payload.strip() == ""
    if isinstance(payload, (list, dict)):
        return len(payload) == 0
    return False


def gen_description(job, details):
    r = call(
        "POST",
        f"{SEARCH_ENGINE_URL}/generate_description",
        json=description_payload(job["company_id"], details),
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error") and not data.get("description"):
        raise RuntimeError(f"description: {data['error']}")
    return {"description": data.get("description")}


def gen_classification(job):
    r = call(
        "POST",
        f"{SEARCH_ENGINE_URL}/api/company-classification/classify",
        json={"company_id": job["company_id"]},
    )
    r.raise_for_status()
    data = r.json()
    if data.get("mode") == "AMBIGUOUS":
        raise RuntimeError("classification: ambiguous company")
    pt = data.get("product_type") or []
    rm = data.get("revenue_model") or []
    dc = data.get("data_collection_method") or []
    empty_note = None
    if not (pt or rm or dc):
        flags = data.get("flags") or []
        empty_note = str(flags[0])[:300] if flags else "classifier returned no values"
        log.info(
            "company %s: classifier returned nothing; flags=%s notes=%s",
            job["company_id"],
            flags,
            (data.get("notes") or "")[:200],
        )

    def share(v):
        return str(int(v)) if isinstance(v, (int, float)) else ""

    # Xano's draft table expects the DB column format, not the classifier's.
    # Data collection has no predominance in the classifier output: first
    # (most confident) value is marked Main, the rest Minor; reviewer can edit.
    out = {
        "product_type": [
            {"Product_Type": p["value"], "pc_of_revenues": share(p.get("revenue_share_pc"))}
            for p in pt
        ],
        "revenue_model": [
            {
                "Revenue_Model_": r["value"],
                "Predominance": "Main" if r.get("is_main") else "Minor",
            }
            for r in rm
        ],
        "data_collection": [
            {
                "Data_Collection_Method": d["value"],
                "Predominance": "Main" if i == 0 else "Minor",
            }
            for i, d in enumerate(dc)
        ],
    }
    if empty_note:
        out["_note"] = empty_note
    return out


def gen_products_users(job, details):
    r = call(
        "POST",
        f"{SEARCH_ENGINE_URL}/api/products-users/generate",
        json={
            "company_id": job["company_id"],
            "company_name": details.get("name") or job["company_name"],
            "website": details.get("url"),
            "force_continue": True,
        },
    )
    r.raise_for_status()
    job_id = r.json().get("job_id")
    if not job_id:
        raise RuntimeError(f"products-users: no job_id in {r.text[:200]}")

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        p = call("GET", f"{SEARCH_ENGINE_URL}/api/products-users/generate/{job_id}")
        p.raise_for_status()
        s = p.json()
        if s.get("status") == "done":
            res = s.get("result") or {}
            return {
                "core_products": res.get("products"),
                "users_use_cases": res.get("users"),
            }
        if s.get("status") in ("error", "discarded"):
            raise RuntimeError(f"products-users: {s.get('error') or s.get('status')}")
    raise RuntimeError("products-users: poll timeout")


def save_draft(company_id, card_key, payload):
    body = payload if not PAYLOAD_AS_STRING else json.dumps(payload, ensure_ascii=False)
    r = call(
        "POST",
        f"{XANO_BASE_URL}{XANO_SCAN_GROUP}/profile_card_drafts",
        json={
            "company_id": company_id,
            "card_key": card_key,
            "payload": body,
            "prompt_version": PROMPT_VERSION,
            "trigger_source": "daily_scan",
        },
    )
    if r.status_code == 409:
        log.info("company %s card %s: 409 %s", company_id, card_key, r.text[:200])
        return "duplicate"
    if not r.ok:
        raise RuntimeError(f"save {card_key}: {r.status_code} {r.text[:300]}")
    try:
        created = r.json().get("created", True)
    except ValueError:
        return "saved"
    if not created:
        log.info("company %s card %s: created=false %s", company_id, card_key, r.text[:200])
    return "saved" if created else "duplicate"


def record_attempt(company_id, card_key, result, note=""):
    """Tell Xano a card came back empty/failed (result) or was written (saved,
    which resets the counter). Best effort: never breaks the run."""
    try:
        r = call(
            "POST",
            f"{XANO_BASE_URL}{XANO_SCAN_GROUP}/profile_card_attempts",
            json={
                "company_id": company_id,
                "card_key": card_key,
                "result": result,
                "note": (note or "")[:500],
            },
        )
        if not r.ok:
            log.warning(
                "company %s card %s: attempt not recorded: %s %s",
                company_id, card_key, r.status_code, r.text[:200],
            )
    except Exception as exc:
        log.warning("company %s card %s: attempt not recorded: %s", company_id, card_key, exc)


def process_company(job):
    """Run each needed generator once per company; returns {outcome: count}."""
    keys = job["keys"]
    results = {}
    errors = []
    need_details = "description" in keys or keys & PRODUCTS_USERS_KEYS
    details = get_details(job["company_id"]) if need_details else {}
    if need_details and not details:
        raise RuntimeError("company details not found")

    steps = []
    if "description" in keys:
        steps.append(({"description"}, lambda: gen_description(job, details)))
    if keys & CLASSIFICATION_KEYS:
        steps.append((CLASSIFICATION_KEYS, lambda: gen_classification(job)))
    if keys & PRODUCTS_USERS_KEYS:
        steps.append((PRODUCTS_USERS_KEYS, lambda: gen_products_users(job, details)))

    reasons = {}  # card_key -> why it has no content
    for group, step in steps:
        try:
            res = step()
            note = res.pop("_note", None)
            results.update(res)
            if note:
                reasons.update({k: note for k in group})
        except Exception as exc:
            errors.append(str(exc))
            reasons.update({k: str(exc) for k in group})
            log.error("company %s: %s", job["company_id"], exc)

    outcomes = {}
    for key in keys:
        payload = results.get(key)
        if DRY_RUN:
            log.info(
                "company %s (%s) card %s: %s",
                job["company_id"],
                job["company_name"],
                key,
                json.dumps(payload, ensure_ascii=False, default=str)[:400],
            )
        if is_empty(payload):
            outcome = "empty" if key in results else "failed"
            log.info(
                "company %s (%s) card %s: %s (%s)",
                job["company_id"],
                job["company_name"],
                key,
                outcome,
                reasons.get(key) or "generator returned nothing",
            )
            if not DRY_RUN:
                record_attempt(
                    job["company_id"],
                    key,
                    outcome,
                    reasons.get(key) or "generator returned nothing",
                )
        elif DRY_RUN:
            outcome = "dry_run"
        else:
            try:
                outcome = save_draft(job["company_id"], key, payload)
                if outcome == "saved" and job.get("attempts", {}).get(key):
                    record_attempt(job["company_id"], key, "saved")
            except Exception as exc:
                log.error("company %s card %s save: %s", job["company_id"], key, exc)
                outcome = "failed"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


def main():
    scan = fetch_scan()
    jobs = build_jobs(scan)
    log.info("companies in scan: %d, companies to generate: %d", len(scan), len(jobs))

    if len(jobs) > MAX_COMPANIES_PER_RUN:
        log.warning("capping companies %d -> %d", len(jobs), MAX_COMPANIES_PER_RUN)
        jobs = jobs[:MAX_COMPANIES_PER_RUN]

    for j in jobs:
        log.info(
            "selected company %s (%s): %s",
            j["company_id"],
            j["company_name"],
            sorted(j["keys"]),
        )
    if os.getenv("SCAN_ONLY", "0") == "1":
        log.info("SCAN_ONLY: nothing generated")
        return

    stats = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_company, j): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                outcomes = fut.result()
            except Exception as exc:
                log.error("company %s: %s", job["company_id"], exc)
                outcomes = {"failed": len(job["keys"])}
            for k, v in outcomes.items():
                stats[k] = stats.get(k, 0) + v

    log.info("done: %s", stats)
    total = sum(stats.values())
    if total and stats.get("failed", 0) == total:
        sys.exit(1)


if __name__ == "__main__":
    main()
