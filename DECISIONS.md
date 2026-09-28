# Architecture Decision Records (ADR)

## 2026-09-17 — Unified Command System Design Architecture

### Context

The application needs a consistent visual language that supports high data density and fast operational scanning for social media management.

### Decision

Adopt the "Unified Command System" visual language:

- **Primary Accent**: Purple `#511877`
- **Secondary Accent**: Coral `#FF5722`
- **Typography**: Plus Jakarta Sans

### Reason

Provides high legibility for dense analytics dashboards while maintaining a modern, agency-grade aesthetic.

---

## 2026-09-17 — Frontend Stack Selection (React 19 + Vite 8 + Oxlint + React Compiler)

### Context

Need a fast, type-safe, and future-proof frontend build setup.

### Decision

- Use **React 19** with **Vite 8**.
- Enable **React Compiler** via `@rolldown/plugin-babel` to eliminate manual memoization (`useMemo`/`useCallback`).
- Use **Oxlint** for high-speed JavaScript/TypeScript linting.

### Trade-offs

Oxlint and React Compiler are modern tools; build configurations must be explicitly locked and verified.

---

## 2026-09-16 — Strict Three-Agent Execution Workflow

**Status**: Superseded on 2026-09-20 by the OpenCode agent configuration in `.opencode/opencode.json` (`primary` orchestrator + specialized subagents: `backend-architect`, `frontend-developer`, `database-admin`, `tester-senior`, `code-reviewer`).

### Context

To prevent context drift, hallucinated changes, and broken builds when using LLM agents.

### Decision

Mandate a 3-agent cyclic workflow: `planner` → `executor` → `reviewer`.

### Rules

- No code execution without an explicit plan.
- No code commit/approval without `reviewer` sign-off.
- Every approved step must be recorded in project state logs.

---

## 2026-09-20 — Autenticación JWT: access token en memoria + refresh token en cookie httpOnly

### Context

NexusManager es una SPA (Vite/React) con API FastAPI. Necesitábamos auth por usuario/contraseña sin vulnerar el balance seguridad/UX: proteger contra XSS (robo de token), CSRF (abuso de cookie) y escalada de sesión.

### Decision

Doble token con separación de responsabilidades:

- **Access token (JWT, HS256, 15 min)**: vive **solo en memoria** dentro del módulo `frontend/src/lib/api.ts` (variable de módulo → nunca `localStorage`/`sessionStorage`). Inyectado por la app en el header `Authorization`, por tanto invisible a XSS persistente y no persiste tras recargar.
- **Refresh token (opaco, 256-bit aleatorio)**: viaja en **cookie `httpOnly` + `SameSite=Lax` + `Secure` (en prod)**, con `Path=/api/v1/auth` (solo la envía el router de auth) y **rotación en cada refresh** + detección de reuso (un refresh usado dos veces revoca **todas** las sesiones del usuario → mitigación de robo de cookie). `remember me` controla `SameSite/expiración` (sesión de navegador vs 30 días).

### Consequences

- + Mitiga sustancialmente el vector más común de robo de JWT (XSS → localStorage).
- + Mitiga CSRF: la cookie es `SameSite=Lax` y el cliente no puede leer el refresh (double-submit nativo vía SameSite). Mutaciones de `/auth` protegidas además por guard de Origin.
- + Rate limiting propio en memoria por ruta (`register/login/refresh/logout/forgot/reset`) evita fuerza bruta; se descartó `slowapi` por incompatibilidad con Starlette 1.6.
- − El access token se pierde al recargar → el frontend reintenta un `refresh` silencioso antes de declarar al usuario como invitado (flujo `bootstrap` ya verificado E2E).
- − Sustituir el `secret_key` por defecto de `app/config.py` es **obligatorio** en despliegue (fail-fast en prod valida `CLAVE_INVALIDA` de entorno).

### Verification

24 tests backend (pytest), `build`+`lint` frontend limpios, y **E2E de navegador real (Playwright)**: registro→dashboard(vía `/me`)→logout→login→login-fallido(no redirige+banner) — todo PASS sin errores JS.
