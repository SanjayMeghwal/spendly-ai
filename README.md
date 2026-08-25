# Spendly AI

An AI-powered personal finance platform — track income and expenses, set budgets
and goals, and get financial insight from a locally-hosted LLM.

Built incrementally with a spec-driven workflow, as a production-oriented
portfolio project.

> **Status: early development.** Milestones 1 (walking skeleton), 2
> (authentication), and 3 (transactions) are complete — the API, database,
> migrations, tests, and CI pipeline all run end to end, the auth flow issues,
> rotates, and revokes tokens, and every user can record, list, edit, and
> delete their own transactions.
> The feature table below marks what actually exists today, not what is planned.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic |
| Database | PostgreSQL 17 + pgvector (Docker) |
| Frontend | React, TypeScript, Vite, Tailwind CSS *(planned)* |
| AI | Ollama, open-source LLMs, LangChain / LangGraph *(planned)* |
| Tooling | uv, Docker Compose, pytest, ruff, mypy, GitHub Actions |

Every dependency is open-source and free to run locally. No paid APIs.

---

## Feature status

| Feature | Status |
|---|---|
| Project scaffolding & tooling | ✅ Done |
| PostgreSQL + pgvector container | ✅ Done |
| FastAPI app + liveness/readiness probes | ✅ Done |
| Database migrations (Alembic) | ✅ Done |
| CI pipeline (lint, types, migrations, tests) | ✅ Done |
| Authentication & user profiles | ✅ Done |
| Income & expense tracking | ✅ Done |
| Categories, budgets, goals | 🚧 Next |
| Dashboard & analytics | ⬜ Planned |
| CSV import & reports | ⬜ Planned |
| AI financial assistant (RAG) | ⬜ Planned |
| Multi-agent orchestration | ⬜ Planned |

---

## Getting started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- [uv](https://docs.astral.sh/uv/)
- Python 3.12

### Setup

```bash
git clone https://github.com/<your-username>/spendly-ai.git
cd spendly-ai

# 1. Configure environment
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
#    then edit .env and set a local password

# 2. Start the database
docker compose up -d
docker compose ps             # wait for status: healthy

# 3. Install backend dependencies
cd backend
uv sync
```

### Running

```bash
cd backend
uv run uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs

### Testing

```bash
cd backend
uv run pytest                 # tests
uv run ruff check .           # lint
uv run mypy .                 # type check
```

---

## API

Interactive docs at `/docs` when `ENVIRONMENT` is not `production`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/auth/register` | — | Create an account |
| `POST` | `/api/v1/auth/login` | — | Exchange credentials for an access + refresh pair |
| `POST` | `/api/v1/auth/refresh` | refresh token | Rotate the pair; reuse of a spent token kills the family |
| `POST` | `/api/v1/auth/logout` | refresh token | End one session |
| `POST` | `/api/v1/auth/logout-all` | access token | End every session on every device |
| `GET` | `/api/v1/auth/me` | access token | The caller's own account |
| `POST` | `/api/v1/transactions` | access token | Record a transaction |
| `GET` | `/api/v1/transactions` | access token | List the caller's transactions, paginated |
| `GET` | `/api/v1/transactions/{id}` | access token | Get one of the caller's transactions |
| `PATCH` | `/api/v1/transactions/{id}` | access token | Partially update one of the caller's transactions |
| `DELETE` | `/api/v1/transactions/{id}` | access token | Delete one of the caller's transactions |
| `GET` | `/health` | — | Liveness — process is up |
| `GET` | `/health/ready` | — | Readiness — database answers |

**Token model.** Access tokens are short-lived JWTs. Verification checks the
signature and then loads the user to compare a `ver` claim against the stored
`token_version` — the deliberate cost of making revocation immediate rather
than waiting for expiry. Refresh tokens are long-lived, stored by `jti`,
and rotated on every use: presenting an already-rotated token is treated as
theft and revokes the whole family. `logout-all` bumps a per-user
`token_version` carried as a JWT claim, so every outstanding access token
becomes invalid at its next use without any blacklist.

**Transactions.** Every transaction belongs to exactly one user, and every
query is scoped by that ownership — a transaction id that exists but belongs
to someone else answers 404, identical to an id that does not exist at all.
Money is a signed `NUMERIC(12,2)`: negative is money out, positive is money
in, so a balance is a single `SUM(amount)`. Updates are partial (`PATCH`):
a field left out of the request body is untouched, while a nullable field
(`category`, `notes`) sent explicitly as `null` is cleared.

---

## Notes

**Why port 5433?** The database container publishes on host port `5433`
instead of the usual `5432`, so it will not collide with a PostgreSQL
instance already installed on your machine.

**Data safety.** `docker compose down` stops the containers and keeps your
data. `docker compose down -v` **permanently deletes** the database volume.

---

## Project structure

```
.
├── backend/          FastAPI application
│   ├── app/          application code (api, core, db, models, schemas, services)
│   └── tests/
├── frontend/         React app (planned)
├── docs/             specs and architecture decision records
├── docker-compose.yml
├── CLAUDE.md         AI assistant instructions and project conventions
└── .env.example      required environment variables (template)
```

---

## License

MIT
