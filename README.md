# Spendly AI

An AI-powered personal finance platform — track income and expenses, set budgets
and goals, and get financial insight from a locally-hosted LLM.

Built incrementally with a spec-driven workflow, as a production-oriented
portfolio project.

> **Status: early development.** Milestones 1 and 2 are complete — the API,
> database, migrations, tests, and CI run end to end, and accounts can register,
> log in, and call an authenticated endpoint.
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
| Income & expense tracking | 🚧 Next |
| Categories, budgets, goals | ⬜ Planned |
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
#    then edit .env: set a local database password, and generate a SECRET_KEY:
#    python -c "import secrets; print(secrets.token_urlsafe(48))"
#    (the app refuses to start if SECRET_KEY is shorter than 32 characters)

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
uv run pytest                 # tests (needs the database running)
uv run ruff check .           # lint
uv run mypy app/              # type check
```

Tests run against the real PostgreSQL container. Each one executes inside a
transaction that is always rolled back, so the suite leaves no rows behind.

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | — | Liveness. Checks no dependencies. |
| `GET` | `/health/ready` | — | Readiness. 503 when the database is unreachable. |
| `POST` | `/api/v1/auth/register` | — | Create an account. 409 if the email is taken. |
| `POST` | `/api/v1/auth/login` | — | Exchange credentials for a bearer token. |
| `GET` | `/api/v1/users/me` | Bearer | The authenticated user's profile. |

Login takes `application/x-www-form-urlencoded` with `username` and `password`
fields — the names RFC 6749 specifies, which is what makes the **Authorize**
button in `/docs` work. Send `username` as the email address.

Access tokens last 30 minutes and **cannot be revoked before they expire**;
refresh tokens and real logout are a later slice.

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
