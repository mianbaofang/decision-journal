# Changelog

## Unreleased (post-v0.10.0 review, version unchanged)

### Fixed

- Legacy database migration now uses the SQLite backup API for a brand-new target and a single `BEGIN IMMEDIATE` merge for an existing one: uncheckpointed WAL rows survive, an already-populated target is never overwritten, `decisions` merge by primary key with `INSERT OR IGNORE`, and `config` only fills keys the new database is missing. A corrupt legacy file, or an unrelated SQLite file without a `decisions` table, is left untouched; concurrent starts serialize; a failed marker rename stays idempotent.
- `--action stop` only touches a loopback backend whose PID command line is this project's absolute `backend/main.py`. Windows no longer relies on `-State Listen` (some systems report an empty state) or a `backend/main.py` substring; POSIX resolves the listening PID via `lsof`/`ss` and verifies port ownership plus absolute path. A remote or non-loopback `--base-url` never auto-starts a local backend, and stop no longer reports success when nothing was stopped.
- Local auto-start now passes the parsed host/port to `backend/main.py`, so a non-default `--base-url` port no longer starts a stray 8010 process; `/api/health` checks the backend JSON identity before treating a port as ready.
- API keys are no longer echoed back in 422 validation errors, and upstream chat failures return a generic 502 instead of the provider's message; CLI HTTP error output redacts known keys and bearer tokens.
- Export Markdown includes all six score fields, including the heuristic `评分信心`.
- The installable package ships `frontend/` so the console script serves the web UI; `__init__.py` files are tracked again (`/_*.py` is root-anchored), and `scripts/sync_skill_package.py` copies them.
- `demoMode` can be set per request without mutating the saved preference; CLI `--demo` therefore no longer turns every later decision into mock data, and `demo/cli-demo.tape` runs without a real key.
- Tests no longer read a real user API key, never default to a live backend, and run the UI suite against an isolated temporary DB/config on a random free port; live integration tests require an explicit `DECISION_JOURNAL_TEST_BASE_URL`.
- The recent-decisions list uses native buttons, so each entry is Tab-focusable and Enter/Space activates it; the hero sample is localized in all six languages and hidden below 1100 px; a `prefers-contrast: more` block strengthens secondary text, lines, and focus rings.

## v0.10.0 - 2026-11-13

### Breaking changes (identity unification)

- The installable Skill, CLI command, Python project, and package all rename from `choice-assistant` / `choice-skill` to **`decision-journal`**; the user-facing brand stays 别纠结 (Decision Journal). The skill directory is now `skills/decision-journal/`.
- CLI entry: `python scripts/decision_journal.py` (was `scripts/choice_assistant.py`); installed console script is `decision-journal` (was `choice-assistant`).
- Environment variables rename `CHOICE_*` → `DECISION_JOURNAL_*` (e.g. `DECISION_JOURNAL_LLM_API_KEY`).
- Local config paths rename `~/.choice/config.json` → `~/.decision-journal/config.json`, backend log → `~/.decision-journal/backend.log`, SQLite file `choice.db` → `decision_journal.db`. Existing users: re-enter keys once or copy the old config file to the new path.
- Why: one name everywhere — repo `mianbaofang/decision-journal`, Skill ID, CLI, and docs now match, so search, install, and issue reports all point to the same identity.

### Added

- Decision brief export: `GET /api/decision/{id}/export?format=md|json` and CLI `--action export`.
- A restrained motion layer: hero and seal entrances, brief sheen and section stagger, animated score bars, archive stagger, and stat count-ups, all disabled under `prefers-reduced-motion`.
- A three-step empty state that explains "say it / break it down / keep it" and hides once a conversation starts.
- `PRIVACY.md` describing local storage, third-party boundaries, and deletion.
- Package-sync test that fails when the installable Skill drifts from `backend/` and `frontend/`.
- README FAQ for decision journal, decision log, pros-and-cons templates, wrong choices, and data storage.

### Changed

- README and Pages covers reposition the product as a decision assistant that remembers: decision journal and review come first, not just one-shot advice.
- Rational mode now returns a checkable score plus a smallest next step; no confidence percentage is shown unless the result actually carries one.
- Human-readable export renders conclusion, perspectives, risks, next steps, score, and status.

### Fixed

- First-run Skill usage auto-starts the bundled backend and waits for `/api/health`; `--no-autostart` keeps the old manual behavior.
- Unknown `/api/*` paths now return JSON 404 instead of the SPA HTML, and SPA file serving is confined to `frontend/`.
- Privacy defaults: auto-speak is off; TTS text is sent in a POST body instead of a URL/query string.
- Fengshui results only cite locally verifiable pillars, carry a `baziAudit`, and ask for missing birth details instead of inventing a chart.
- Root `requirements.txt` restores `pip install -r requirements.txt`; the installable package is mirrored by `scripts/sync_skill_package.py`.

## v0.9.1 - 2026-08-17

### Changed

- Consolidated the installable Agent Skill under `skills/choice-assistant/` and removed the duplicate repository-root entry.
- Added a deterministic Skill package builder and validator, with a versioned ZIP and SHA-256 checksum as release assets.
- Aligned the README navigation, release status, Pages JSON-LD, sitemap, and `llms.txt` with the `v0.9.1` identity.
- Split backend tests from the Playwright desktop job; CI now starts the backend and installs Chromium before UI checks.
- Aligned the Yao manifest and interface across OpenAI, Claude, Generic, Agent Skills, and VS Code targets.

## v0.9.0 - 2026-07-26

### Added

- Four switchable interface styles: Heritage, Quiet Workbench, Decision Journal, and Modular Console.
- Six browser-rendered random effects: pointer draw, fortune sticks, 3D dice, six-card draw, ticket machine, and ink path.
- Richer Nature evidence including location, temperature, humidity, wind, daylight, moon phase, forecast, alerts, air information, and signal weights.
- Dialogue answer history persisted as question-and-answer pairs in `dialogueHistory`.
- User-managed Amap weather Key, Base URL, and city settings in both Web and CLI flows.
- Detailed Chinese and English feature maps and module-level project structure documentation.
- Updated Chinese and English intro animations with the Mini Program logo, four interface styles, and six random effects.

### Changed

- Reused the Mini Program logo in the open-source desktop interface.
- Renamed the user-facing Fengshui label to Traditional Culture while keeping the internal `fengshui` id for compatibility.
- Reworked the random result presentation and added a standalone effect preview page.
- Nature mode now uses live Amap data only when both Key and Base URL are configured; otherwise it clearly labels simulated weather.
- Removed the previously embedded weather credential and corrected public documentation that described weather as built in.
- Updated repository discovery metadata and added a downloadable source archive to the Release.

### Fixed

- Dialogue option clicks now save the selected answer instead of sending an unused completion flag.
- Weather settings no longer show the bare label "simulated data" without explaining when it is used.
- Static asset versions were bumped so updated settings and translations are not hidden by browser cache.

### Verification

- `87 passed, 15 skipped` in the full pytest suite.
- `15 passed` in the desktop Playwright suite.
- Browser console clean; desktop settings and random effect layouts checked without horizontal overflow.

## v0.8.4 - 2026-07-07

### Added

- Local-first decision archive with Web UI and CLI access.
- Six decision lenses: Auto, Rational, Random, Nature, Dialogue, and Fengshui.
- Multimodal input support for text, voice, and image-based decision context.
- Internationalized UI with Chinese and English README files.
- TTS, browser speech input, weather context, archive, statistics, and mock fallback mode.
- Public repository materials: disclaimer, acknowledgements, security policy, contribution guide, and GitHub templates.

### Notes

- This project is decision-support software, not professional advice.
- Users should read `DISCLAIMER.md` before using it for personal or high-stakes decisions.
