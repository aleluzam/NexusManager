# NexusManager

Social media management web application for small businesses and agencies: multi-channel publishing, paid advertising campaigns, and automated video workflows.

## Visual Identity

"Unified Command System" design system — Primary Purple `#511877`, Accent Coral `#FF5722`, Typography: Plus Jakarta Sans. Full tokens per view in `design/*/DESIGN.md`.

## Stack

- **Frontend**: React 19 + TypeScript + Vite (React Compiler via `@rolldown/plugin-babel`, Oxlint as linter).
- **Backend**: FastAPI + Uvicorn.

> Project context, conventions, and agent workflow: see `AGENTS.md`. Current state: `PROGRESS.md`. Architecture decisions: `DECISIONS.md`.

## Repository Structure

- `frontend/` — React 19 + TypeScript + Vite application.
- `backend/` — FastAPI application (entry point `backend/main.py`, `GET /hello`).
- `ingestion-service/` — Standalone FastAPI + Dramatiq microservice: receives batches of videos, transcribes them and cuts them into per-phrase minivideos. See `TODO.md` for the state of the work.
- `design/` — Static mockups per view (`login/`, `hero_section/`, `dashboard/`) with `DESIGN.md`, `code.html`, and `screen.png`.
- `.opencode/` — OpenCode configuration and subagents.

## Ports

Everything runs on a single machine, so the ports are assigned explicitly:

| Service | Port | Notes |
|---|---|---|
| `backend` | 8000 | Also the target of the Vite dev proxy (`frontend/vite.config.ts`) |
| `ingestion-service` | 8001 | Published as `8001:8000`; the container itself listens on 8000 |
| `frontend` (dev) | 5173 | Also the only origin allowed by the backend CORS |

## Setup

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Validate with `npm run lint` and `npm run build`.

### Backend

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

Virtual environment lives at `backend/.venv`. Dependencies are listed in `backend/requirements.txt`; install new packages with `source .venv/bin/activate && pip install <pkg>` and append them to that file.

### Ingestion service

```bash
cd ingestion-service
cp .env.example .env      # then change API_KEY
docker compose up --build
```

Separate stack (Postgres + Redis + API + worker). Reads its configuration from its own
`.env`, which is git-ignored.