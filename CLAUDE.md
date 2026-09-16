# Project Context

## Agent Workflow

For every non-trivial implementation task, follow this workflow:

1. Use the `planner` agent first.
2. The planner must produce an executable plan with verifiable success criteria.
3. Execute exactly one plan step at a time using the `executor` agent.
4. After each executor step, use the `reviewer` agent.
5. If the reviewer returns `REJECTED`, send the rejection feedback to the
   executor and have it correct only the reported problems.
6. Run the reviewer again after corrections.
7. If the reviewer returns `APPROVED`, instruct the executor to update
   `CLAUDE.md` with the approved changes.
8. Only then continue with the next plan step.
9. A task is complete only when all plan steps are APPROVED and documented
   in CLAUDE.md.

Do not skip the planner or reviewer for non-trivial implementation tasks.

## Overview

<!-- Brief description of the project and its purpose. -->

## Architecture

<!-- Important architectural decisions and project structure. -->

## Conventions

<!-- Coding conventions, patterns, commands, and project-specific rules. -->

## Current State

<!-- Current implementation status and important facts about the project. -->

## Updates

- **2026-09-16**: Frontend scaffolding – created `frontend/` directory and initialized npm (`npm init -y`).
- **2026-09-16**: Frontend dependencies installed – added React, ReactDOM, Vite, @vitejs/plugin-react, and TypeScript.
- **2026-09-16**: Added `frontend/vite.config.ts` with React plugin configuration.
- **2026-09-16**: Created `frontend/index.html` with root div and Vite script tag.
- **2026-09-16**: Added `frontend/src/App.tsx` component rendering "Hello from NexusManager".
- **2026-09-16**: Added `frontend/src/main.tsx` mounting App to `#root`.
- **2026-09-16**: Added npm `dev` and `build` scripts to `frontend/package.json`.
- **2026-09-16**: Ran `npm install` in frontend and created node_modules and package-lock.json.
- **2026-09-16**: Created Python virtual environment `.venv` at repository root.
- **2026-09-16**: Added `backend/requirements.txt` with FastAPI and Uvicorn dependencies.
- **2026-09-16**: Installed backend dependencies in `.venv` (fastapi, uvicorn).
- **2026-09-16**: Added `backend/main.py` defining FastAPI app with `/hello` endpoint.
- **2026-09-16**: Performed end‑to‑end verification – backend `/hello` returns expected JSON; frontend scaffold (scripts, config, source files) is ready.

## Pending Work

<!-- Known remaining work resulting from approved changes. -->

# FLUJO

┌─────────┐
│ PLANNER │
└────┬────┘
│
│ plan
▼
┌──────────┐
│ EXECUTOR │──────► implementa + tests
└────┬─────┘
│
│ resultado
▼
┌──────────┐
│ REVIEWER │──────► tests + código + CLAUDE.md
└────┬─────┘
│
├── REJECTED ──► Executor corrige
│ │
│ └────► Reviewer
│
└── APPROVED
│
▼
┌──────────┐
│ EXECUTOR │
└────┬─────┘
│
▼
actualiza
CLAUDE.md
