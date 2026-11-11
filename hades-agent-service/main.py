"""Nightly HADES agent.

Takes HADES items that still need data entry, runs the Search Engine for each one and saves the result
as a draft on the item (HADES_Requests.draft). Nothing is written to the main database here; a person
reviews the draft in the CMS and pushes it.

draft (json) shape, version 1:
  common:   {version, kind: "company"|"event", status: "processing"|"ready"|"blocked"|"error", ran_at, duration_s, query, requested}
  processing: {version, kind, status, ran_at}  (written before the work starts; stays if the run dies)
  company:  {duplicate: bool, existing_company: {id,name,url}|null,
             summary: {events_found, events_new, individuals, parties_new: [names]},
             parties: [{name, role, in_db, company_id}], analysis: <raw /analyze response>}
  event:    {target: {id,name}, event: <enriched event>, duplicates: [{event_id, announcement_date, deal_type, reason}],
             parties: [{name, role, in_db, company_id}]}
  blocked:  {blocker: "target_not_set"|"no_related_url", message}
  error:    {error}
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

XANO_BASE_URL = os.getenv("XANO_BASE_URL", "https://xdil-abvj-o7rq.e2.xano.io").rstrip("/")
XANO_AUTH_PATH = os.getenv("XANO_AUTH_PATH", "/api:vnXelut6/auth/login")
XANO_HADES_GROUP = os.getenv("XANO_HADES_GROUP", "/api:hadesCMS1")
XANO_SEARCH_GROUP = os.getenv("XANO_SEARCH_GROUP", "/api:8Bv5PK4I")
XANO_CMS_GROUP = os.getenv("XANO_CMS_GROUP", "/api:VP2Spm80")
SEARCH_ENGINE_URL = os.environ["SEARCH_ENGINE_URL"].rstrip("/")
SERVICE_EMAIL = os.environ["SERVICE_EMAIL"]
SERVICE_PASSWORD = os.environ["SERVICE_PASSWORD"]

HARD_CAP = 100
MAX_ITEMS_PER_RUN = min(int(os.getenv("MAX_ITEMS_PER_RUN", "25")), HARD_CAP)  # the cost limit
MAX_RUNTIME_MIN = int(os.getenv("MAX_RUNTIME_MIN", "240"))
FAIL_STREAK_LIMIT = int(os.getenv("FAIL_STREAK_LIMIT", "3"))
DUP_WINDOW_DAYS = int(os.getenv("DUP_WINDOW_DAYS", "90"))
ANALYZE_TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", "1800"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))
MAX_DRAFT_BYTES = int(os.getenv("MAX_DRAFT_BYTES", str(1_500_000)))
RESEARCH_EXISTING = os.getenv("RESEARCH_EXISTING", "0") == "1"  # 1 = still run the AI research on companies already in the DB
SOCIAL_HOSTS = ("linkedin.com", "facebook.com", "twitter.com", "x.com", "youtube.com", "instagram.com", "share.google", "crunchbase.com")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
# The repo is public, so GitHub Actions logs are public: no item titles, URLs, company names or error bodies there.
PUBLIC_LOGS = os.getenv("GITHUB_ACTIONS") == "true"
ITEM_IDS = {int(x) for x in os.getenv("ITEM_IDS", "").split(",") if x.strip().isdigit()}

STATUS_TODO = ("Data entry required", "Surfaced via Corporate Event Checker")
STATUS_READY = "Completed by agent – ready for review"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("hades_agent")

_session = requests.Session()
_token = ""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def login(force=False):
    global _token
    if _token and not force:
        return _token
    resp = _session.post(f"{XANO_BASE_URL}{XANO_AUTH_PATH}",
                         json={"email": SERVICE_EMAIL, "password": SERVICE_PASSWORD}, timeout=30)
    resp.raise_for_status()
    _token = resp.json()["authToken"]
    return _token


def call(method, url, timeout=REQUEST_TIMEOUT, **kw):
    """HTTP call with the bearer token; one re-login on 401/403 and a short retry on 5xx/network errors."""
    last = None
    for attempt in range(3):
        try:
            headers = {"Authorization": f"Bearer {login()}"}
            resp = _session.request(method, url, headers=headers, timeout=timeout, **kw)
            if resp.status_code in (401, 403) and attempt == 0:
                login(force=True)
                continue
            if resp.status_code >= 500 and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            if not resp.ok:
                raise RuntimeError(f"{method} {url} -> {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        except requests.RequestException as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{method} {url} failed: {last}")


# ---------------------------------------------------------------------------- selection

def fetch_todo():
    items = []
    for status in STATUS_TODO:
        page, got = 1, 0
        while page <= 20:
            data = call("GET", f"{XANO_BASE_URL}{XANO_HADES_GROUP}/hades_list",
                        params={"status": status, "page": page, "per_page": 500})
            rows = data.get("items") or []
            items.extend(rows)
            got += len(rows)
            if not rows or got >= int(data.get("total") or 0):
                break
            page += 1
    return items


def kind_of(item):
    dt = (item.get("data_type") or "").strip()
    if dt.startswith("Company"):
        return "company"
    if dt == "Corporate Event":
        return "event"
    return None


def parse_draft(item):
    d = item.get("draft")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = None
    return d if isinstance(d, dict) and d else None


def eligible(item):
    """An item is processed once. Any draft (ready, blocked, error or an interrupted run) takes it out of every later
    run. Only a person can put it back: the CMS sets agent_requested, which makes the next run pick it up once."""
    if ITEM_IDS and item["id"] not in ITEM_IDS:
        return False
    if not kind_of(item):
        return False
    return not parse_draft(item) or bool(item.get("agent_requested"))


def sort_key(item):
    return (0 if item.get("agent_requested") else 1,
            item.get("priority") or 9,
            str(item.get("due_date") or "9999"),
            item.get("date_added") or 0)


# ---------------------------------------------------------------------------- engine calls

def search_post(path, payload):
    return call("POST", f"{SEARCH_ENGINE_URL}{path}", timeout=ANALYZE_TIMEOUT, json=payload)


def party_check(name, cache):
    """Is this counterparty/investor/advisor already a company in the DB? Two gates: the Search Engine's lookup,
    then an exact (normalised) name match in the CMS company search, because the first one misses real companies."""
    key = name.strip().lower()
    if key in cache:
        return cache[key]
    out = {"in_db": None, "company_id": None, "matched_by": None}
    try:
        r = call("GET", f"{SEARCH_ENGINE_URL}/counterparty_company_lookup", params={"company_name": name})
        if r.get("found"):
            out = {"in_db": True, "company_id": r.get("id"), "matched_by": "engine_lookup"}
        else:
            found, possible = check_by_name([name])
            if found:
                out = {"in_db": True, "company_id": found.get("id"), "matched_by": "name"}
            else:
                out = {"in_db": False, "company_id": None, "matched_by": None}
                if possible:
                    out["possible"] = [{"id": c.get("id"), "name": c.get("name")} for c in possible]
    except Exception as exc:  # a failed lookup must not fail the item, and must not read as "new"
        log.warning("party lookup failed (%s)", type(exc).__name__ if PUBLIC_LOGS else f"{name!r}: {exc}")
    cache[key] = out
    return out


def parties_of(event_like):
    """Flat list of {name, role} from counterparties / parties / advisors of an event dict."""
    out = []
    for key in ("counterparties", "parties", "advisors"):
        for p in (event_like.get(key) or []):
            if not isinstance(p, dict):
                continue
            name = (p.get("company_name") or p.get("name") or p.get("advisor_name") or "").strip()
            if name:
                out.append({"name": name, "role": (p.get("type") or p.get("role") or p.get("advisor_type") or "").strip()})
    return out


def checked_parties(events, cache, limit=40):
    seen, out = set(), []
    for ev in events:
        for p in parties_of(ev):
            k = p["name"].lower()
            if k in seen or len(out) >= limit:
                continue
            seen.add(k)
            out.append({**p, **party_check(p["name"], cache)})
    return out


# ---------------------------------------------------------------------------- processors

NAME_NOISE = {"the", "inc", "ltd", "limited", "llc", "gmbh", "plc", "corp", "corporation", "co", "company", "ag", "sa", "bv", "ab"}


def norm_name(value):
    """Lower-case, letters and digits only, without 'the' / legal-form words (so 'The Langston Co' = 'Langston Company')."""
    text = str(value or "").lower().strip()
    for tld in (".com", ".io", ".ai", ".co", ".net", ".org", ".app"):
        if text.endswith(tld) and len(text) > len(tld) + 1:
            text = text[: -len(tld)]
            break
    words = re.sub(r"[^a-z0-9 ]", " ", text.replace("&", " and ")).split()
    words = [w for w in words if w not in NAME_NOISE]
    return "".join(words)


def name_candidates(item):
    """A company name taken from the item title ('Acme | Project X' -> 'Acme'); long titles are sentences, not names."""
    name = (item.get("description") or "").split(" | ")[0].strip()
    return [name] if name and len(name) <= 60 and len(name.split()) <= 6 else []


def check_by_url(url):
    """Gate 1: website domain, the same Xano lookup the Search Engine runs first."""
    if not url.lower().startswith("http") or any(h in url.lower() for h in SOCIAL_HOSTS):
        return None
    data = call("GET", f"{XANO_BASE_URL}{XANO_SEARCH_GROUP}/get_company_by_url", params={"website_url": url})
    row = data[0] if isinstance(data, list) and data else None
    return row if isinstance(row, dict) and row.get("id") else None


def check_by_name(names):
    """Gate 2: exact name (after normalising) in the CMS company search. Returns (match, possible_duplicates)."""
    for name in names:
        key = norm_name(name)
        if len(key) < 3:
            continue
        data = call("GET", f"{XANO_BASE_URL}{XANO_CMS_GROUP}/cms_companies_typeahead", params={"search": name, "limit": 15})
        hits = [c for c in (data.get("items") or []) if norm_name(c.get("name")) == key]
        if len(hits) == 1:
            return hits[0], []
        if len(hits) > 1:
            return None, hits[:5]  # ambiguous: do not auto-link, show to the reviewer
    return None, []


def find_existing_company(item):
    """Double gate before any research: website first, then name. Returns (company, matched_by, possible_duplicates)."""
    found = check_by_url((item.get("related_url") or "").strip())
    if found:
        return found, "website", []
    found, possible = check_by_name(name_candidates(item))
    if found:
        return found, "name", []
    return None, None, possible


_NOT_A_NAME = ("map", "providers", "list", "companies", "ecosystem", "stack", "landscape", "dataset", "index", "directory", "report")


def looks_like_company_name(text):
    """Without a Related URL the title is searched as a company name; market maps and lists are not."""
    head = (text or "").split(" | ")[0].strip()
    words = head.lower().replace("-", " ").split()
    return bool(head) and len(words) <= 4 and not any(w.strip(",.:") in _NOT_A_NAME for w in words)


def process_company(item):
    url = (item.get("related_url") or "").strip()
    query = url or (item.get("description") or "").strip()
    if not query or (not url and not looks_like_company_name(query)):
        return {"status": "blocked", "blocker": "no_related_url",
                "message": "No Related URL, and the title is not a single company name (market map or list)."}
    # Double gate FIRST (website, then name): /analyze looks the company up but then researches it anyway.
    # An existing company is linked and flagged, and no search/AI credits are spent on it.
    found, matched_by, possible = find_existing_company(item)
    if found and not RESEARCH_EXISTING:
        draft = {
            "status": "ready", "query": query, "duplicate": True, "matched_by": matched_by,
            "existing_company": {"id": found.get("id"), "name": found.get("name"), "url": found.get("url")},
            "research_skipped": True,
            "note": "Company already in the database (matched by %s); linked instead of creating it again." % matched_by,
            "summary": {}, "parties": [],
        }
        link = {} if item.get("target_company_id") else {"target_company_id": found["id"], "target_company_name": found.get("name") or ""}
        return draft, link
    opts = {"include_overview": True, "include_events": True, "include_individuals": True, "include_counterparties": True}
    res = search_post("/analyze", {"query": query, "query_version": "v2", "options": opts, **opts})
    existing = res.get("existing_company") or None
    ai_events = res.get("ai_events") or []
    new_events = res.get("missing_events") or []
    people = res.get("top_management") or res.get("individuals") or []
    cache = {}
    parties = checked_parties(ai_events, cache)
    if existing and existing.get("id"):
        # The analysed company is already known from the duplicate check; a name lookup can miss it.
        ex_name = (existing.get("name") or "").strip().lower()
        for p in parties:
            if p["name"].strip().lower() == ex_name:
                p["in_db"], p["company_id"] = True, existing["id"]
    draft = {
        "status": "ready", "query": query,
        "duplicate": bool(existing),
        "possible_duplicates": [{"id": c.get("id"), "name": c.get("name")} for c in possible],
        "existing_company": ({"id": existing.get("id"), "name": existing.get("name"), "url": existing.get("url")}
                             if existing else None),
        "summary": {"events_found": len(ai_events), "events_new": len(new_events), "individuals": len(people),
                    "parties_new": [p["name"] for p in parties if p["in_db"] is False]},
        "parties": parties,
        "analysis": res,
    }
    link = {}
    if existing and existing.get("id") and not item.get("target_company_id"):
        link = {"target_company_id": existing["id"], "target_company_name": existing.get("name") or ""}
    return draft, link


def as_list(value):
    """Xano can return json columns as a JSON string; always give back a list of dicts."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _date(s):
    try:
        return datetime.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def find_duplicates(target_id, event):
    """Existing events of the target with the same deal type, date within DUP_WINDOW_DAYS and a shared party."""
    existing = call("GET", f"{XANO_BASE_URL}{XANO_SEARCH_GROUP}/company_events", params={"company_id": target_id})
    existing = as_list(existing if isinstance(existing, list) else (existing.get("items") or []))
    new_date = _date(event.get("announcement_date"))
    new_type = (event.get("deal_type") or "").strip().lower()
    new_parties = {p["name"].lower() for p in parties_of(event)}
    out = []
    for ex in existing:
        if new_type and (ex.get("deal_type") or "").strip().lower() != new_type:
            continue
        ex_date = _date(ex.get("announcement_date"))
        if new_date and ex_date and abs((new_date - ex_date).days) > DUP_WINDOW_DAYS:
            continue
        ex_parties = {(p.get("company_name") or "").strip().lower() for p in as_list(ex.get("counterparties"))}
        shared = sorted((new_parties & ex_parties) - {""})
        if new_parties and ex_parties and not shared:
            continue
        out.append({"event_id": ex.get("event_id"), "announcement_date": ex.get("announcement_date"),
                    "deal_type": ex.get("deal_type"),
                    "reason": "same type, date within %d days%s" % (DUP_WINDOW_DAYS, ", shared party: " + ", ".join(shared) if shared else "")})
    return out


_AFTER = (" completes the acquisition of ", " completes acquisition of ", " announces the acquisition of ", " announces acquisition of ",
          " to acquire ", " has acquired ", " acquires ", " acquired ", " buys ", " takes over ", " snaps up ", " invests in ", " backs ")
_BEFORE = (" raises ", " secures ", " lands ", " receives ", " closes ", " bags ", " nabs ", " attracts ", " gets ")
_PASSIVE = (" to be acquired by ", " is acquired by ", " was acquired by ", " has been acquired by ", " acquired by ", " to be bought by ", " sold to ")
_STOPS = (" to ", " for ", " in ", " and ", " from ", " with ", " by ", " - ", " | ", ",", ":", ";", "(", " as ", " at ", " on ", " after ", " via ", " amid ", " that ", " which ", " has ", " is ", " will ")


def _cut(text):
    low = " " + text.lower() + " "
    ends = [i - 1 for i in (low.find(st, 1) for st in _STOPS) if i > 0]
    out = text[: min(ends)] if ends else text
    out = out.strip()
    return out[4:].strip() if out.lower().startswith("the ") else out


def guess_target_name(title):
    """'X acquires Y' -> Y, 'X to be acquired by Y' -> X, 'X raises ...' -> X (same rules as the Xano backfill)."""
    low = " " + title.lower() + " "
    for verbs, side in ((_PASSIVE, "before"), (_AFTER, "after"), (_BEFORE, "before")):
        hits = [(low.find(v), v) for v in verbs if low.find(v) >= 0]
        if not hits:
            continue
        i, v = min(hits)
        cand = _cut(title[:i] if side == "before" else title[i - 1 + len(v):])
        if cand.lower().startswith("by "):  # "X acquired by Y" is the passive form, not "acquires Y"
            continue
        if 2 <= len(cand) <= 60 and len(cand.split()) <= 6:
            return cand
    return None


def process_event(item):
    target_id = item.get("target_company_id")
    target_name = item.get("target_company_name") or ""
    link = {}
    if not target_id:
        # No target on the item: take it from the title and confirm it with the name gate. Saved as a guess for a person to confirm.
        guess = guess_target_name(item.get("description") or "")
        found, _possible = check_by_name([guess]) if guess else (None, [])
        if found:
            target_id, target_name = found["id"], found.get("name") or guess
            link = {"target_company_id": target_id, "target_company_name": target_name, "target_match": "title_guess"}
    if not target_id:
        return {"status": "blocked", "blocker": "target_not_set",
                "message": "Target company not in the database or not set. Create a Company HADES item for it first."}
    url = (item.get("related_url") or "").strip()
    if not url:
        return {"status": "blocked", "blocker": "no_related_url", "message": "No Related URL to read the event from."}
    res = search_post("/smart_enrich_event", {"url": url, "event": {"title": item.get("description") or "", "company": target_name}})
    event = res.get("enriched_event") or {}
    dups = find_duplicates(target_id, event)
    draft = {
        "status": "ready", "query": url,
        "target": {"id": target_id, "name": target_name},
        "event": event, "duplicates": dups,
        "parties": checked_parties([event], {}),
    }
    if link:
        draft["target"]["guessed_from_title"] = True
    return draft, link


# ---------------------------------------------------------------------------- run

def run_item(item):
    started = time.time()
    kind = kind_of(item)
    try:
        result = process_company(item) if kind == "company" else process_event(item)
        draft, link = result if isinstance(result, tuple) else (result, {})
        ok = True
    except Exception as exc:
        log.error("item %s failed (%s)", item["id"], type(exc).__name__ if PUBLIC_LOGS else exc)
        draft, link, ok = {"status": "error", "error": str(exc)[:500]}, {}, False
    draft.update({"version": 1, "kind": kind, "ran_at": now_iso(), "duration_s": round(time.time() - started, 1),
                  "requested": bool(item.get("agent_requested"))})
    if len(json.dumps(draft, default=str)) > MAX_DRAFT_BYTES and "analysis" in draft:
        draft["analysis"] = {k: v for k, v in draft["analysis"].items() if k not in ("db_company", "db_events")}
        draft["truncated"] = True
    return ok, draft, link


def claim(item):
    """Mark the item as taken BEFORE any search/AI credits are spent. If the run dies half way, or the final save fails,
    the marker is still there, so the next run does not pick the same item up again."""
    marker = {"version": 1, "kind": kind_of(item), "status": "processing", "ran_at": now_iso()}
    call("PATCH", f"{XANO_BASE_URL}{XANO_HADES_GROUP}/hades_update",
         json={"hades_id": item["id"], "fields": {"draft": marker, "draft_kind": marker["kind"], "draft_at": marker["ran_at"], "agent_requested": False}})


def save(item, draft, link):
    fields = {"draft": draft, "draft_kind": draft["kind"], "draft_at": draft["ran_at"], "agent_requested": False, **link}
    if draft["status"] == "ready":
        fields.update({"status": STATUS_READY, "last_run_at": draft["ran_at"]})
    call("PATCH", f"{XANO_BASE_URL}{XANO_HADES_GROUP}/hades_update", json={"hades_id": item["id"], "fields": fields})


def main():
    started = time.time()
    todo = sorted((i for i in fetch_todo() if eligible(i)), key=sort_key)
    log.info("%d eligible HADES items, running up to %d%s", len(todo), MAX_ITEMS_PER_RUN, " (DRY RUN)" if DRY_RUN else "")
    stats = {"ready": 0, "blocked": 0, "error": 0, "skipped_time": 0}
    streak = 0
    for item in todo[:MAX_ITEMS_PER_RUN]:
        if time.time() - started > MAX_RUNTIME_MIN * 60:
            stats["skipped_time"] += 1
            continue
        if streak >= FAIL_STREAK_LIMIT:
            log.error("%d failures in a row, stopping", streak)
            break
        if not DRY_RUN:
            try:
                claim(item)
            except Exception as exc:  # without the marker a repeat could not be ruled out, so skip it
                log.error("could not claim item %s, skipping (%s)", item["id"], type(exc).__name__ if PUBLIC_LOGS else exc)
                stats["error"] += 1
                streak += 1
                continue
        ok, draft, link = run_item(item)
        streak = 0 if ok else streak + 1
        stats[draft["status"]] += 1
        log.info("item %s (%s) -> %s in %ss", item["id"], draft["kind"], draft["status"], draft["duration_s"])
        if DRY_RUN and not PUBLIC_LOGS:
            print(json.dumps({"item": item["id"], "draft": {k: v for k, v in draft.items() if k != "analysis"}, "link": link}, default=str)[:2000])
        else:
            try:
                save(item, draft, link)
            except Exception as exc:
                log.error("could not save item %s (%s)", item["id"], type(exc).__name__ if PUBLIC_LOGS else exc)
    log.info("done in %.0fs: %s", time.time() - started, json.dumps(stats))
    return 1 if stats["error"] and not (stats["ready"] or stats["blocked"]) else 0


if __name__ == "__main__":
    sys.exit(main())
