# HADES agent (nightly job)

Runs through HADES items that need data entry, asks the Search Engine for the data and saves the
result as a **draft on the HADES item**. It never writes to the main database: a person reviews the
draft in the CMS and pushes it.

Flow:
1. `GET hades_list?status=Data entry required` (and `Surfaced via Corporate Event Checker`), the same endpoint the CMS HADES tab uses.
2. Keeps Data type `Company*` (-> company profile) and `Corporate Event` (-> event). Skips items that already have a draft.
3. Order: items the team marked `agent_requested`, then priority (1 first), due date, oldest first. Takes `MAX_ITEMS_PER_RUN` (the cost limit).
4. Company: double duplicate gate first, before any research: (1) website domain via Xano `get_company_by_url`, (2) exact company name via the CMS company search (normalised: no 'The', Inc/Ltd/GmbH/Co/Company). Either hit counts, and the draft records `matched_by`. Several same-name matches are not auto-linked; they are listed as `possible_duplicates`. An existing company is linked and flagged and no research is run (set `RESEARCH_EXISTING=1` to still run it). A new company goes to `POST /analyze` from the Related URL. Event: `POST /smart_enrich_event` from URL + description for the item's target company, then a duplicate check against that company's events (same type, date within 90 days, shared party).
5. Parties (counterparties, investors, advisors) are checked with `/counterparty_company_lookup` and flagged as in the DB or new.
6. `PATCH hades_update` with `draft`, `draft_kind`, `draft_at` and status `Completed by agent – ready for review`.

Where the data is stored: `HADES_Requests.draft` (json) on the item. See `draft` shape in `main.py`.

Each item is processed once:
- Before any search/AI work the job writes a `processing` marker into `draft` (a "claim").
- Any draft (ready, blocked, error or an interrupted `processing`) takes the item out of every later run.
- Items that finish as `ready` also leave the `Data entry required` list because their status changes.
- Nothing is retried automatically. A person puts an item back with `agent_requested` (set from the CMS), and the next run picks it up once.

Safety: hard cap of 100 items per run, a runtime limit, and a stop after 3 failures in a row.

Auth: logs in to Xano with SERVICE_EMAIL/SERVICE_PASSWORD and uses the authToken for Xano and the Search Engine.

Dry run (calls the Search Engine, saves nothing):
    DRY_RUN=1 SERVICE_EMAIL=... SERVICE_PASSWORD=... python main.py

One item:
    ITEM_IDS=5479 DRY_RUN=1 SERVICE_EMAIL=... SERVICE_PASSWORD=... python main.py

Schedule and hosting: its own Fly app `hades-agent` (worker, no HTTP): one small machine that stays up, sleeps until
03:00 Europe/Berlin (`RUN_AT`, `SCHEDULE_TZ`, daylight saving handled), runs once and waits for the next night.
A deploy never triggers a run; it only waits for the next 03:00. Fly's `--schedule daily` cannot pin a time of day,
which is why the machine runs the clock itself.

Deploy / change:
    fly apps create hades-agent
    fly secrets set SERVICE_EMAIL=... SERVICE_PASSWORD=... -a hades-agent
    fly deploy -a hades-agent --ha=false
Logs: `fly logs -a hades-agent` (look for "next run at ...").
Run now by hand (same code, real run of 5): MAX_ITEMS_PER_RUN=5 SERVICE_EMAIL=... SERVICE_PASSWORD=... SEARCH_ENGINE_URL=... python main.py
Local one-shot runs do not use the scheduler (LOOP is only set on Fly).
