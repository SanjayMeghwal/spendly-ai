# Spendly AI

An AI-powered personal finance platform — track income and expenses, set budgets
and goals, and get financial insight from a locally-hosted LLM.

Built incrementally with a spec-driven workflow, as a production-oriented
portfolio project.

> **Status: early development.** Milestones 1 (walking skeleton), 2
> (authentication), 3 (transactions), 4 (budgets), 5 (categories), and 6
> (goals) are complete — the API, database, migrations, tests, and CI
> pipeline all run end to end, the auth flow issues, rotates, and revokes
> tokens, every user can record, list, edit, and delete their own
> transactions, every user can set a monthly spending limit per category
> and see how their actual spend compares, categories are a real resource
> — renameable, with transactions moved out of one before it's deleted —
> rather than free text, and every user can set a savings target per
> category and see live progress toward it.
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
| Budgets | ✅ Done |
| Categories | ✅ Done |
| Goals | ✅ Done |
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
| `POST` | `/api/v1/budgets` | access token | Set a monthly spending limit for a category |
| `GET` | `/api/v1/budgets` | access token | List the caller's budgets, each with this month's spend |
| `GET` | `/api/v1/budgets/{id}` | access token | Get one of the caller's budgets, with a month's spend |
| `PATCH` | `/api/v1/budgets/{id}` | access token | Partially update one of the caller's budgets |
| `DELETE` | `/api/v1/budgets/{id}` | access token | Delete one of the caller's budgets |
| `POST` | `/api/v1/categories` | access token | Create a category |
| `GET` | `/api/v1/categories` | access token | List the caller's categories |
| `GET` | `/api/v1/categories/{id}` | access token | Get one of the caller's categories |
| `PATCH` | `/api/v1/categories/{id}` | access token | Rename one of the caller's categories |
| `DELETE` | `/api/v1/categories/{id}` | access token | Delete a category (`?reassign_to=` moves its transactions first) |
| `POST` | `/api/v1/goals` | access token | Create a savings goal for a category |
| `GET` | `/api/v1/goals` | access token | List the caller's goals, each with live progress |
| `GET` | `/api/v1/goals/{id}` | access token | Get one of the caller's goals, with live progress |
| `PATCH` | `/api/v1/goals/{id}` | access token | Partially update one of the caller's goals |
| `DELETE` | `/api/v1/goals/{id}` | access token | Delete one of the caller's goals |
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
(`category_id`, `notes`) sent explicitly as `null` is cleared. Reads include
a `category_name` alongside `category_id` — resolved from `categories`, not
stored on the row, with one bulk lookup per page rather than a query per
transaction.

**Budgets.** A budget is a monthly spending limit for one category,
referenced by `category_id`. A user can have at most one budget per
category; switching a budget to a category that already has one is
rejected as `409`. `spent` is not a stored column — it is computed on every
read as the net signed sum of that category's transactions for the
requested month (defaulting to the current UTC month, or `?month=YYYY-MM`
on either `GET`), so a refund automatically offsets spend rather than being
ignored.

**Categories.** A real, renameable resource — `Transaction.category_id`,
`Budget.category_id`, and `Goal.category_id` are all foreign keys into it,
not free text, so a rename is a single-row update rather than a bulk
rewrite. Names are unique per user, case-insensitively. Deleting a category
is deliberately not a plain cascade: a category with an **active budget or
goal** always blocks deletion — merging two budgets' limits, or two goals'
progress, isn't something to do automatically, so the budget/goal has to be
deleted or repointed first. A category with **transactions** blocks
deletion unless the request supplies `?reassign_to=<category_id>`, which
moves every matching transaction to the target category in the same
operation as the delete.

**Goals.** A savings target for one category, referenced by `category_id`.
A user can have at most one goal per category. `progress` is not a stored
column — it is computed on every read as the net signed sum of that
category's transactions, using the same sign convention as a budget's
`spent` (money flowing in reads positive), but cumulatively rather than
per calendar month: there is no `?month=` parameter here at all, since a
goal has no reset period. `remaining` (`target_amount - progress`) is
shown uncapped when a goal is overshot rather than clamped at zero.
`target_date` is optional and, unlike `category_id`/`target_amount`, can be
cleared by sending it explicitly as `null` in a `PATCH`.

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
