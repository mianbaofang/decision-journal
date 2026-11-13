# Privacy

Decision Brief is local-first. Your decision journal lives on your machine unless you deliberately configure a third-party service.

## What stays on your machine

- **Decision records** — SQLite database at `backend/choice.db` (WAL mode). The installable Skill package keeps its own copy under `skills/choice-assistant/backend/choice.db`.
- **Settings** — SQLite settings plus `~/.choice/config.json` when the CLI is used.
- **Backend log** — `~/.choice/backend.log`, request lines only; chat questions and TTS text are sent in request bodies, not URLs.
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

- Delete a single record: archive view → delete (double confirmation), or `python scripts/choice_assistant.py --action decision --id <id> --delete`.
- Delete everything: remove `backend/choice.db` (and `-wal` / `-shm`) while the backend is stopped.
- Clear keys: settings → AI configuration, `--action config-api --delete`, or remove the relevant environment variables.

Deletion is immediate; this project does not keep server-side backups.

## Sensitive material

Avoid sending ID documents, medical records, contracts, children's photos, or other sensitive images to third-party vision models. For high-stakes legal, medical, financial, or employment decisions, use the app only as a thinking framework and consult a qualified professional.
