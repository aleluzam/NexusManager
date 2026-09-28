# Project Progress

## Current State

- **Backend**: Functional FastAPI application. `GET /hello` returns `{"message": "Hello, World!"}` (verified end-to-end).
- **Auth (completo y verificado)**: Registro, login, sesión, logout, refresh con rotación, recuperación de contraseña y rate limiting, de punta a punta.
  - **Backend**: 24 tests verdes (`pytest`). Endpoints: `register`, `login`, `refresh`, `logout`, `me`, `forgot-password`, `reset-password`.
  - **Frontend**: `npm run build` + `npm run lint` limpios. `AuthContext` + `api.ts` (token JWT **solo en memoria**, refresh token en cookie `httpOnly` `SameSite=Lax`, rotación con detección de reuso).
  - **E2E navegador real (Playwright)**: registro → `/dashboard` autenticado → logout → login → login fallido (sin redirección + banner) — **100% PASS**, sin errores JS en consola.
- **Frontend**: React 19 + TypeScript + Vite 8 with React Compiler and Oxlint. Landing page now ports the Stitch screen "NexusManager - Hero Principal" (`Navbar`, `HeroSection`, `Footer` components) with the dark purple "Unified Command System" styling and bundled logo/avatar assets. `npm run build` and `npm run lint` pass; dev server verified.
- **Design**: Mockups completed under `design/` (`login/`, `hero_section/`, `dashboard/`) containing design tokens, previews, and screenshots.
- **Agents Workflow**: OpenCode configured with `primary` orchestrator agent plus 5 specialized agents (`backend-architect`, `frontend-developer`, `database-admin`, `tester-senior`, `code-reviewer`) under `.opencode/agents/`, wired in `.opencode/opencode.json`.

---

## Completed Updates

### 2026-09-21

- Reset de datos de la BD local (`backend/nexus.db`, SQLite): `users` 4→0, `refresh_tokens` 9→0, `password_reset_tokens` 0. No quedan usuarios registrados ni sesiones/tokens de reseteo. Esquema intacto (`integrity_check` ok, sin huérfanos de FK); verificado además a través de la sesión async del propio backend.

### 2026-09-20 (2)

- Ported the Stitch screen "NexusManager - Hero Principal" into the React frontend: new `components/Navbar.tsx`, `HeroSection.tsx`, `Footer.tsx` (fixed dark navbar, badge, headline, CTAs, demo frame + ROAS card, platform strip), rewritten `App.tsx`, `index.css` (design tokens) and `App.css` (component styles) translating the exact Tailwind classes of the source HTML.
- Bundled logo (`navbar-logo.png`) and avatar (`profile-avatar.png`) assets locally; added Google Fonts (Plus Jakarta Sans + Material Symbols Outlined) to `index.html`; added `src/vite-env.d.ts`; removed unused Vite starter assets/fixtures.

### 2026-09-20

- Audited project state against root documentation (`AGENTS.md`, `PROGRESS.md`, `DECISIONS.md`, `README.md`). Verified backend end-to-end (`GET /hello` → 200) and frontend `lint`/`build` passing.
- Renamed `DESICIONS.md` → `DECISIONS.md` and `PROGRESS.MD` → `PROGRESS.md` to match documented names.
- Updated `README.md` (correct venv path `backend/.venv`), aligned `.opencode/agent/` → `.opencode/agents/` references, and expanded root `.gitignore` (`.DS_Store`, `.venv`, `.env`).

### 2026-09-17

- Re-scaffolded frontend using official Vite template (React 19, TypeScript, Vite 8). Enabled React Compiler (`@rolldown/plugin-babel`) and Oxlint (`npm run lint`).
- Added `design/` folder containing static mockups (`login/`, `hero_section/`, `dashboard/`) with `DESIGN.md`, `code.html`, and `screen.png`.
- Verified backend end-to-end (`GET /hello` returning `{"message": "Hello, World!"}`).

### 2026-09-16

- Initialized `frontend/` directory, package configuration, React/Vite dependencies, and entry scripts (`main.tsx`, `App.tsx`).
- Created Python virtual environment (`.venv`) and installed FastAPI / Uvicorn.
- Implemented `backend/main.py` with basic `/hello` endpoint.

---

## Pending Work

- [x] Port static mockups from `design/` into React frontend components using "Unified Command System" tokens (Hero landing: navbar + hero + footer done; `login` and `dashboard` views pending).
- [ ] Port remaining views (`login`, `dashboard`) from Stitch/`design/`.
- [ ] Implement initial view routing: Hero/Welcome → Login → Dashboard.
- [ ] Connect React frontend to FastAPI backend (auth endpoints and dashboard metrics).
- [ ] Expand backend APIs (authentication, social media posts CRUD, automated video triggers).
