# NexusManager — Global Context & Agent Rules

## Overview

NexusManager is a social media management web application targeted at small businesses and agencies. Key features include multi-channel publishing, paid advertising campaigns, and automated video workflows. The user interface is designed as a high-density operational "cockpit" prioritizing data legibility.

- **Visual Identity**: "Unified Command System" design system (Primary Purple `#511877`, Accent Coral `#FF5722`, Typography: Plus Jakarta Sans).
- **Core Tech Stack**: React 19 + TypeScript + Vite (Frontend), FastAPI + Uvicorn (Backend).

---

## Architecture & Repository Structure

- `frontend/` — React 19 + TypeScript + Vite (scaffolded via official Vite template).
  - React Compiler enabled via `@rolldown/plugin-babel` (`frontend/vite.config.ts`).
  - Linter: Oxlint (`frontend/.oxlintrc.json`).
  - Entry point: `frontend/src/main.tsx` → `frontend/src/App.tsx`.
- `backend/` — FastAPI application.
  - Entry point: `backend/main.py` (`GET /hello`).
  - Dependencies: `backend/requirements.txt`.
  - Virtual Environment: `backend/.venv` (ignored in `.gitignore`).
- `design/` — Static mockups per view (`login/`, `hero_section/`, `dashboard/`).
  - Each view includes `DESIGN.md` (design system tokens), `code.html` (HTML/Tailwind preview), and `screen.png`.
- `.opencode/` — OpenCode configuration (`opencode.json` and `.opencode/agents/` subagents).
- Root files — `AGENTS.md` (global rules and context), `PROGRESS.md` (project state), `DECISIONS.md` (architecture decision records), `README.md`, `.gitignore`.

---

## Development Conventions

- **Backend**: Activate `backend/.venv` and run `uvicorn main:app --reload` inside `backend/`. Append new packages to `backend/requirements.txt`.
- **Frontend**: Run `npm install` and `npm run dev` inside `frontend/`. Validate code with `npm run build` and `npm run lint`.
- **Design Alignment**: Adhere strictly to the "Unified Command System" tokens defined in `design/*/DESIGN.md`.

---

## Agent Execution Workflow

For every non-trivial implementation task, agents MUST strictly follow this sequence:
