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
   `AGENTS.md` with the approved changes.
8. Only then continue with the next plan step.
9. A task is complete only when all plan steps are APPROVED and documented
   in AGENTS.md.

Do not skip the planner or reviewer for non-trivial implementation tasks.

## Overview

NexusManager es una aplicación web de gestión de redes sociales orientada a
pequeñas empresas y agencias: publicación multicanal, campañas de pago y
flujos de video automatizados. La interfaz se concibe como un "cockpit"
operativo de alta densidad con alta legibilidad de datos.

Stack: frontend React + TypeScript + Vite y backend FastAPI. La identidad
visual la define el sistema de diseño "Unified Command System" (morado
`#511877`, coral `#FF5722`, tipografía Plus Jakarta Sans).

## Architecture

- **`frontend/`** – React 19 + TypeScript + Vite. Scaffolded con el template
  oficial de Vite. Habilita el React Compiler vía `@rolldown/plugin-babel`
  (`frontend/vite.config.ts`) y lint con oxlint (`frontend/.oxlintrc.json`).
  Entrada: `frontend/src/main.tsx` → `frontend/src/App.tsx`.
- **`backend/`** – FastAPI + uvicorn. Entrypoint `backend/main.py` con la app y
  el endpoint `GET /hello`. Dependencias en `backend/requirements.txt`.
  Entorno virtual en `backend/.venv` (ignorado por `backend/.gitignore`, junto
  a `.env`).
- **`design/`** – Mockups estáticos por pantalla. Cada vista tiene `DESIGN.md`
  (tokens del sistema "Unified Command System": colores, tipografía, spacing,
  componentes), `code.html` (preview HTML/Tailwind) y `screen.png`.
  Vistas: `design/login/`, `design/hero_section/` (bienvenida) y
  `design/dashboard/`.
- **`.opencode/`** – Configuración de opencode: `opencode.json` (carga
  `AGENTS.md` como contexto) y `agent/` con los agentes `planner`,
  `executor` y `reviewer`.
- **Raíz** – `AGENTS.md` (contexto y memoria del proyecto), `README.md`
  (resumen de arranque), `.gitignore` (`node_modules/`, `__pycache__/`).

## Conventions

- **Backend**: activar `backend/.venv` y correr uvicorn desde `backend/`
  (`uvicorn main:app --reload`). Añadir dependencias a
  `backend/requirements.txt`.
- **Frontend**: `npm install` + `npm run dev` en `frontend/`. Verificar con
  `npm run build` y `npm run lint` (oxlint).
- **Diseño**: seguir los tokens de "Unified Command System" definidos en
  `design/*/DESIGN.md` (colores, tipografía Plus Jakarta Sans, spacing y
  componentes).
- **Trabajo con agentes**: seguir el workflow de la sección "Agent Workflow"
  (planner → executor → reviewer). Cada cambio aprobado se registra en
  `AGENTS.md` bajo `## Updates`.

## Current State

- **Backend**: FastAPI funcional. `GET /hello` responde
  `{"message": "Hello, World!"}` (verificado end‑to‑end).
- **Frontend**: scaffolded con el template oficial de Vite (React 19 +
  TypeScript + Vite 8) con React Compiler y oxlint habilitados. `App.tsx`
  conserva la demo por defecto del template; aún no hay pantallas de producto.
- **Diseño**: mockups listos en `design/`: `login/`, `hero_section/`
  (bienvenida) y `dashboard/`. Cada uno con tokens de "Unified Command System"
  (`DESIGN.md`), preview (`code.html`) y captura (`screen.png`). No integrados
  aún en el frontend.
- **Agentes**: el workflow planner/executor/reviewer está configurado en
  `.opencode/agent/`; `AGENTS.md` es el archivo de contexto/memoria del
  proyecto (cargado vía `.opencode/opencode.json`) y se actualiza tras cada
  cambio aprobado.

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
- **2026-09-17**: Frontend re‑scaffolded con el template oficial de Vite (React 19
  + TypeScript + Vite 8). Habilitado el React Compiler vía
  `@rolldown/plugin-babel` (`frontend/vite.config.ts`) y lint con oxlint
  (`frontend/.oxlintrc.json`, script `npm run lint`).
- **2026-09-17**: Añadida carpeta `design/` con mockups estáticos:
  `login/`, `hero_section/` (bienvenida) y `dashboard/`. Cada vista incluye
  `DESIGN.md` (sistema de diseño "Unified Command System": tokens de color,
  tipografía Plus Jakarta Sans, spacing, componentes), `code.html` (preview
  HTML/Tailwind) y `screen.png`.
- **2026-09-17**: Backend verificado end‑to‑end – `GET /hello` responde
  `{"message": "Hello, World!"}`.
- **2026-09-17**: `AGENTS.md` documenta la memoria actual del proyecto
  (Overview, Architecture, Conventions, Current State, Updates y Pending Work)
  y es el archivo de contexto/memoria que se actualiza tras cada cambio
  aprobado.

## Pending Work

- Portar los mockups de `design/` al frontend React (login, hero/bienvenida,
  dashboard) aplicando los tokens de "Unified Command System" y reemplazando
  la demo por defecto del template de Vite.
- Definir navegación entre las pantallas del "primer vistazo":
  hero section (bienvenida) → login → dashboard.
- Conectar el frontend con el backend (endpoints de autenticación/login y
  datos de redes sociales para el dashboard).
- Ampliar el backend según las necesidades de las pantallas (auth, CRUD de
  publicaciones, etc.).

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
│ REVIEWER │──────► tests + código + AGENTS.md
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
AGENTS.md
