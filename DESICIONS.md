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

### Context

To prevent context drift, hallucinated changes, and broken builds when using LLM agents.

### Decision

Mandate a 3-agent cyclic workflow: `planner` → `executor` → `reviewer`.

### Rules

- No code execution without an explicit plan.
- No code commit/approval without `reviewer` sign-off.
- Every approved step must be recorded in project state logs.
