# Privacy

Decision Journal is local-first. Your decision journal lives on your machine unless you deliberately configure a third-party service.

## What stays on your machine

- **Decision records (unreleased source)** — SQLite at `~/.decision-journal/decision_journal.db` (WAL mode), separate from the installation. `DECISION_JOURNAL_DB_PATH` or `DECISION_JOURNAL_DATA_DIR` can override it. Published v0.10.1 still uses the installation-local `backend/decision_journal.db`; see [migration and recovery](docs/DATA_STORAGE.md).
- **Settings** — SQLite settings plus `~/.decision-journal/config.json` when the CLI is used.
- **Backend log** — `~/.decision-journal/backend.log`, request lines only; chat questions and TTS text are sent in request bodies, not URLs.
- **SQLite backups** — explicit `data backup`, or automatic safety snapshots before an existing-data merge/replacement, stored locally next to the active database under `backups/` by default. They may contain SQLite-saved API keys and are not encrypted. Separate configuration files and environment variables are not included.
- **Exports** — `.brief.md` / `.json` files are written wherever you redirect them.

This project has no analytics, telemetry, crash reporting, or remote account system.

## What can leave your machine

Only services you configure or trigger:

| Service | Data | When |
| --- | --- | --- |
| OpenAI-compatible LLM | your question, optional image, mode prompt, relevant preferences | when you submit a real (non-Demo) request |
| Vision model | the image you attach | when you submit an image with a real request |
| Amap / weather API | city, weather API key | when nature mode reads live weather |
| Edge TTS | the brief text you ask to read aloud | only when you press read aloud; auto-speak is off by default |

Demo mode never calls an LLM. Without a weather key, nature mode uses clearly labelled simulated weather.

## Credentials

API keys are stored in the local SQLite settings or supplied through environment variables. They are masked in CLI/API responses and must not be committed to git. See [SECURITY.md](SECURITY.md).

## Deleting data

- Delete a single record: archive view → delete (double confirmation), or `python scripts/decision_journal.py --action decision --id <id> --delete`.
- Delete everything: inspect the active path with `data status`, then remove that database (and `-wal` / `-shm`) while the backend is stopped. For complete erasure, also remove local backups, retained legacy database copies, exports and any separate configuration file. Retaining an unmigrated package database may allow first-start adoption again.
- Clear keys: settings → AI configuration, `--action config-api --delete`, or remove the relevant environment variables.

Record deletion takes effect in the active database. Previously created local backups and retained migration sources are not automatically erased; this project keeps no server-side backups.

## Sensitive material

Avoid sending ID documents, medical records, contracts, children's photos, or other sensitive images to third-party vision models. For high-stakes legal, medical, financial, or employment decisions, use the app only as a thinking framework and consult a qualified professional.
