# CLAUDE.md — Spendly AI

Instructions for Claude Code working in this repository.
Keep this file SHORT. It loads into context every session. Record what would
otherwise be **guessed wrong** — not what can be read from the code.

---

## Project

**Spendly AI** — an AI-powered personal finance platform. Portfolio-grade and
production-oriented, built incrementally.

This repo is also a **learning environment**. The owner is an early-career
engineer who wants to understand every decision, not receive finished code.
See "Working style" below — it is not optional.

**Repository is PUBLIC.** Assume anything committed is permanently world-readable.

---

## Current state

| | |
|---|---|
| Milestone | **10 — AI: embeddings + retrieval** — complete |
| Done (M1) | git hygiene · uv deps · Postgres+pgvector container · validated config · async engine + session · FastAPI app · liveness/readiness probes · Alembic (baseline) · CI |
| Done (M2) | User + RefreshToken models · 3 migrations · Argon2id hashing · JWT access tokens · refresh rotation with reuse detection · logout · logout-all via `token_version` · `/me` · tests (210, 99%) |
| Done (M3) | Transaction model (signed `NUMERIC(12,2)`) · full CRUD, every query scoped by `user_id` · pagination · partial updates via `exclude_unset` |
| Done (M4) | Budget model (positive `NUMERIC(12,2)` limit) · full CRUD · spend-vs-limit computed live from `transactions`, net signed sum, per calendar month |
| Done (M5) | Category model (case-insensitive unique name per user) · full CRUD · `Transaction`/`Budget` cut over from free-text `category` to a real `category_id` FK via a 4-migration expand/contract sequence · reads denormalize `category_name` via a bulk lookup (no N+1) · `DELETE /categories/{id}` reassigns transactions (`?reassign_to=`) but always blocks on an active budget |
| Done (M6) | Goal model (positive `NUMERIC(12,2)` target, optional `Date` deadline) · full CRUD · progress computed live from `transactions`, same sign convention as Budget's `spent` but cumulative, no month window · `remaining` shown uncapped when overshot · `delete_category` extended to block on an active goal, same as it already did for budgets · tests (471, 99%) |
| Done (M7) | Reporting API — no model/migration, pure aggregation over `transactions`/`categories` · `GET /reports/spend-by-category` (net spend per category, one calendar month, largest first, synthetic "Uncategorized" bucket) · `GET /reports/monthly-summary` (income/expenses/net for the last N months, default 6, zero-filled for quiet months) · no dedicated balance-trend endpoint — client derives it via cumulative sum · tests (492, 99%) |
| Done (M8) | CSV import — no model/migration, ordinary `Transaction` rows · `POST /transactions/import` (fixed `date,amount,description,category` schema, `python-multipart` dependency) · best-effort per-row validation (invalid rows reported back, never block the rest of the file) · de-dup on `(occurred_at, amount, description)` against the database and within the same file · category matched case-insensitively against the caller's existing categories only, nothing auto-created · tests (508, 99%) |
| Done (M9) | Frontend MVP — `frontend/`, React + TypeScript + Vite + Tailwind · CORS added on the backend for the Vite dev origin · TanStack Query + a thin `apiRequest` fetch wrapper with transparent access-token refresh-on-401 (concurrent refreshes deduped) · tokens in `localStorage`, not an httpOnly cookie — a deliberate, documented, revisitable trade-off · auth screens (register auto-logs-in, login, protected routes) · full CRUD screens for transactions, categories, budgets, goals · a reporting dashboard on M7's endpoints (KPI tiles, income/expenses trend chart, spend-by-category ranked bars), colors chosen and validated per the dataviz skill · every screen browser-verified by hand, not just build/lint-checked · CSV import has no frontend UI yet — deferred, not missed |
| Done (M10) | AI: embeddings + retrieval — `pgvector` (Python) + `httpx` deps · nullable `Vector(768)` `embedding` column on `transactions` (Alembic migration hand-fixed: autogenerate can't emit `CREATE EXTENSION` or see the `pgvector` import it needs) · `OLLAMA_BASE_URL`/`OLLAMA_EMBEDDING_MODEL` config, `nomic-embed-text` pulled locally · `services/embedding.py`'s `embed_text` wraps Ollama's HTTP API · embeddings generated synchronously on `POST /transactions` and CSV import via `embed_transaction_or_none`, deliberately **fail-soft** (logs and returns `None`, never blocks the write) since Ollama runs as a bare local process outside `docker-compose.yml` and can be down without anyone noticing · `scripts/backfill_embeddings.py` sweeps rows still missing one (run as `python -m scripts.backfill_embeddings`, not by path) · `GET /transactions/search?q=` embeds the query and ranks by pgvector cosine distance, scoped by `user_id`, excluding un-embedded rows · search itself is NOT fail-soft — no query vector means nothing to rank, so `EmbeddingError` becomes a 503 · every piece verified against the real local Ollama, not just mocks · tests (535, 99%) mock Ollama via an autouse `httpx.MockTransport` stub, the same tier as any other external/LLM call |
| Endpoints | `POST /register` `/login` `/refresh` `/logout` `/logout-all` · `GET /me` · `POST/GET /transactions` `GET/PATCH/DELETE /transactions/{id}` `POST /transactions/import` `GET /transactions/search` · `POST/GET /budgets` `GET/PATCH/DELETE /budgets/{id}` · `POST/GET /categories` `GET/PATCH/DELETE /categories/{id}` · `POST/GET /goals` `GET/PATCH/DELETE /goals/{id}` · `GET /reports/spend-by-category` `GET /reports/monthly-summary` · health probes |
| Next | **Milestone 11 — AI: RAG chat**: natural-language Q&A over a user's finances — backend endpoint + chat UI, built on M10's embeddings/retrieval |

---

## Roadmap (M10+)

Sketched 2026-08-31, not yet started past M10. Sized to match M1–M8's own
granularity — one coherent resource or capability per milestone. Revisit
before starting each one; this is a plan, not a commitment.

| # | Milestone | Covers |
|---|---|---|
| 9 | Frontend MVP | React/TS/Vite/Tailwind — auth + CRUD screens for every resource so far + dashboard on M7's endpoints. First demoable product. |
| 10 | AI: embeddings + retrieval | Ollama + pgvector (provisioned since M1, unused until now) — embed a user's financial data, build retrieval over it |
| 11 | AI: RAG chat | Natural-language Q&A over a user's finances — backend endpoint + chat UI |
| 12 | Multi-agent orchestration | LangGraph agents on M10/M11 — e.g. auto-categorization suggestions, a budget-advisor agent |

One judgment call baked into this order, worth revisiting if priorities
change:
- **Frontend is one MVP milestone, not split per-resource** — a frontend
  with auth but no way to see your data isn't a usable product. The
  alternative is mirroring the backend's own history (`frontend-auth`,
  `frontend-transactions`, ...), trading a much bigger milestone for
  smaller, easier-to-review ones.

---

## Tech stack

**Backend:** Python 3.12 · FastAPI · SQLAlchemy 2.x · Alembic · psycopg 3 ·
pydantic-settings · uv
**Database:** PostgreSQL 17 + pgvector 0.8.6, in Docker
**Frontend:** React + TypeScript + Vite + Tailwind, in `frontend/` (Milestone 9)
**AI:** Ollama (`nomic-embed-text`, local) for embeddings, since M10 · LangChain/LangGraph *(later milestones)*
**Quality:** pytest · ruff · mypy (strict) · GitHub Actions

---

## Decisions already made — do not silently revisit

| Decision | Reason |
|---|---|
| **Postgres in Docker**, not the host's native install | pgvector ships precompiled; building it on Windows needs VS Build Tools |
| **Host port 5433**, not 5432 | The host's native PostgreSQL already binds 5432 |
| **uv + `pyproject.toml` + `uv.lock`**, not pip + requirements.txt | Reproducible installs; lock file pins all transitive deps |
| **asyncpg** driver, not psycopg | psycopg's async mode refuses to run on Windows' `ProactorEventLoop`, which uvicorn installs itself — overriding any policy the app sets. asyncpg works on every platform. See gotchas. |
| Line endings normalized to **LF** via `.gitattributes` | CRLF breaks scripts inside Linux containers |
| `models/` (SQLAlchemy) kept separate from `schemas/` (Pydantic) | What we store ≠ what we expose; prevents leaking password hashes |
| **Async SQLAlchemy**, not sync | The AI milestones are I/O-bound (LLM calls take seconds); sync→async migration later would touch every DB file |
| Frontend tokens in **localStorage**, not an httpOnly cookie | Unblocks M9 without backend changes to an already-complete, tested auth system. Known cost: XSS-reachable. Moving to Secure/httpOnly/SameSite cookies + CSRF is a deliberate future security-hardening milestone, not an oversight — see `backend/app/schemas/auth.py`'s `TokenResponse` docstring, which flags this exact trade-off. |
| Embedding a transaction is **fail-soft** (create/import never blocks on Ollama); embedding a search **query** is **fail-hard** (503) | A transaction row is fully valid with no embedding — it just won't surface in search until a backfill catches it up. A search has nothing to rank without a query vector, so there is no equivalent fallback. Ollama runs as a bare local process outside `docker-compose.yml`, so it can be down without anyone noticing — this asymmetry is deliberate, not inconsistent. See `services/embedding.py`. |

⚠️ **Async consequence — always eager-load relationships.** Accessing an
unloaded relationship (`user.transactions`) outside an active async context
raises `MissingGreenlet`. Use `selectinload()` / `joinedload()` explicitly.
Lazy loading is not available to us.

---

## Commands

Backend commands run from `backend/`; frontend commands run from `frontend/`.

```bash
# Database
docker compose up -d          # start (from repo root)
docker compose ps             # status + health
docker compose logs -f db     # logs
docker compose down           # stop, KEEP data

# Backend
uv sync                       # install/refresh deps
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check . && uv run ruff format .
uv run mypy app/

# Migrations (from backend/)
uv run alembic current                        # what's applied
uv run alembic history                        # revision chain
uv run alembic revision --autogenerate -m "…" # draft from model diff — REVIEW IT
uv run alembic upgrade head                   # apply
uv run alembic downgrade -1                   # roll back one

# Frontend (needs the backend running at http://localhost:8000 — see VITE_API_URL in .env.local)
npm install
npm run dev      # http://localhost:5173
npm run build    # tsc -b && vite build
npm run lint     # oxlint
```

**Migration rules**
- Autogenerated migrations are a **first draft**. Always read before applying.
  Autogenerate cannot see renames (it emits drop+add, destroying data), data
  backfills, extensions, triggers, or views.
- A new model must be **imported in `alembic/env.py`** or autogenerate won't
  see it — and will generate a `DROP` for its table.
- Every `upgrade()` needs a working `downgrade()`.
- Generated files are auto-linted/formatted by Alembic post-write hooks.

⚠️ **`docker compose down -v` destroys the database volume permanently.**
Never run it as a "reset" without explicit confirmation from the user.

---

## Conventions

**Git**
- Branches: `feat/…`, `fix/…`, `chore/…`, `docs/…`, `test/…`
- Conventional Commits, imperative mood; body explains **why**, not what
- Atomic commits — one logical change each
- Never commit directly to `main`; always branch → PR

**Layering** (dependencies point one direction only)
```
api/       HTTP only — routing, status codes. No business logic.
services/  Business logic. Must not import FastAPI.
models/    SQLAlchemy ORM — database shape.
schemas/   Pydantic — API contract shape.
core/      Config, security primitives. No DB, no HTTP.
db/        Engine, session, base.
```

**Database**
- Every schema change goes through an Alembic migration — never edit a live schema by hand
- Review autogenerated migrations before applying; Alembic misses renames and constraints
- Money is `NUMERIC`, **never** `float` — binary floats cannot represent 0.10 exactly
- Timestamps are `TIMESTAMPTZ`, stored in UTC
- Index foreign keys and columns used in `WHERE` / `ORDER BY`
- Every user-owned table filters by `user_id` — a query without it is a data leak

**Testing**
- Tests run against the **real PostgreSQL container**, never SQLite or a mocked
  session. SQLite has no true `DECIMAL` (money would become floating point),
  no `TIMESTAMPTZ`/`JSONB`, and foreign keys off by default — a green suite
  proving code works against a database we don't deploy.
- Mock only what is slow, external, non-deterministic, or costs money
  (third-party APIs, email, LLM calls). Never our own database.
- `asyncio_mode = "auto"` — plain `async def test_*` works; no decorator needed.
- Mark tests needing a live database `@pytest.mark.integration`.
- **A new test must be seen to fail.** Break the code deliberately, confirm the
  right test fails, restore. A test that cannot fail is worse than no test —
  it grants false confidence.
- Coverage requires `concurrency = ["thread", "greenlet"]`; SQLAlchemy's async
  layer runs queries in greenlets and coverage silently under-reports without
  it. Treat a surprising coverage number as a possible tooling bug.

**API**
- Pydantic models for every request and response; never return an ORM object directly
- Correct status codes: 200/201/204, 400/401/403/404/409/422
- Errors must not leak stack traces, SQL, or internal paths

---

## Security — non-negotiable

- **Never** hardcode a secret. Config comes from env via `pydantic-settings`.
- `.env` is gitignored; `.env.example` documents keys with fake values.
- Passwords: hashed only (bcrypt/argon2). Never logged, never returned by an API.
- Never build SQL by string concatenation — use SQLAlchemy parameter binding.
- Every endpoint touching user data must verify ownership, not just authentication.
- Treat all LLM output as untrusted input; never execute it or interpolate it into SQL.
- If a security risk appears, **stop and explain it before continuing**.

---

## Working style — read this before writing code

The user is learning professional engineering, not collecting code.

1. **Teach before implementing.** Explain the problem, where it fits, what the
   alternatives are, and why we're choosing this one.
2. **Present real trade-offs** for architectural decisions and let the user
   decide. Recommend, don't dictate.
3. **Spec → design → plan → implement → test → review.** No jumping from a
   vague request straight to code.
4. **Small, reviewable changes.** No sweeping multi-file rewrites.
5. **Verify, don't assume.** Run the test, check the output, show the result.
   Report failures honestly.
6. **Debug by hypothesis**, never by rewriting until it works.
7. **Don't add a dependency** without justifying it.
8. **Don't use an AI agent** where deterministic code is correct and simpler.
9. After significant changes, state what changed, why, the risks, and what the
   user should verify manually.
10. End meaningful units of work with a few questions the user should be able
    to answer before moving on.

## Known gotchas (hit and solved — don't re-debug these)

**Windows + psycopg async — why we use asyncpg.** psycopg's async mode raises
`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'` on Windows,
buried under ~100 lines of SQLAlchemy pool internals. Setting
`WindowsSelectorEventLoopPolicy` does **not** fix it under uvicorn: uvicorn
installs its own Proactor loop *after* importing the app, overwriting the
policy. Verified empirically — the app imported with a Selector policy set and
still ran on `ProactorEventLoop`. Resolved by switching the driver to asyncpg,
which works on both loop types. **Do not reintroduce psycopg as the async
driver.**

**Reading long tracebacks.** Start at the bottom for *what* failed, then scan
up for the first line in our own code. The middle is library plumbing.

## Claude must NOT

- Run `docker compose down -v`, `git push --force`, or `git reset --hard` without explicit approval
- Commit to `main` directly
- Commit `.env`, credentials, or real financial data
- Hand-edit `uv.lock`
- Create empty folders for features that don't exist yet
- Add packages "for later"
- Mark work complete without running the tests
