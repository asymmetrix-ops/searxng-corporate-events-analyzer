# Profile completeness daily job

Scans Xano `profile_completeness/scan`, generates missing AI cards via Search Engine,
saves them to `profile_card_drafts` (never publishes).

Card -> generator:
- description -> POST /generate_description
- product_type, revenue_model, data_collection -> POST /api/company-classification/classify (one call)
- core_products, users_use_cases -> POST /api/products-users/generate (+ polling)

Auth: logs in to Xano with SERVICE_EMAIL/SERVICE_PASSWORD, uses the authToken for Xano and Search Engine.

Dry run (generates, saves nothing):
    DRY_RUN=1 SEARCH_ENGINE_URL=... SERVICE_EMAIL=... SERVICE_PASSWORD=... python main.py

Deploy (daily scheduled machine; Fly picks the time within the day):
    fly apps create profile-completeness
    fly secrets set SERVICE_EMAIL=... SERVICE_PASSWORD=...
    fly machine run . --schedule daily --restart no --app profile-completeness
