"""Tests for POST /api/v1/transactions/import.

Run end to end - HTTP request, CSV parsing, service, real PostgreSQL -
same reasoning as test_transactions_create.py: what matters here spans all
of those layers, including that the NUMERIC column actually holds what the
parser claims to have validated.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Transaction, User
from app.services.category import create_category
from app.services.user import create_user

URL = "/api/v1/transactions/import"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id, token_version=user.token_version)}"
    }


async def add_category(session: AsyncSession, user: User, *, name: str) -> Category:
    return await create_category(session, user_id=user.id, name=name)


async def add_transaction(
    session: AsyncSession,
    user: User,
    *,
    amount: str,
    description: str,
    occurred_at: datetime,
) -> None:
    session.add(
        Transaction(
            user_id=user.id,
            amount=Decimal(amount),
            description=description,
            occurred_at=occurred_at,
        )
    )
    await session.commit()


def csv_bytes(
    rows: list[dict[str, str]],
    *,
    columns: tuple[str, ...] = ("date", "amount", "description", "category"),
) -> bytes:
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(row.get(column, "") for column in columns))
    return "\n".join(lines).encode("utf-8")


def upload(
    content: bytes, *, filename: str = "transactions.csv"
) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, content, "text/csv")}


@pytest.mark.integration
class TestSuccessfulImport:
    async def test_imports_valid_rows_and_reports_counts(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes(
            [
                {"date": "2026-03-01", "amount": "-50.00", "description": "Groceries"},
                {"date": "2026-03-02", "amount": "2000.00", "description": "Paycheck"},
            ]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.status_code == 200
        body = response.json()
        assert body["imported"] == 2
        assert body["skipped_duplicates"] == 0
        assert body["errors"] == []

        result = await db_session.execute(select(Transaction).where(Transaction.user_id == user.id))
        transactions = result.scalars().all()
        assert len(transactions) == 2
        assert {t.amount for t in transactions} == {Decimal("-50.00"), Decimal("2000.00")}

    async def test_matches_category_case_insensitively(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        groceries = await add_category(db_session, user, name="Groceries")
        content = csv_bytes(
            [
                {
                    "date": "2026-03-01",
                    "amount": "-50.00",
                    "description": "Store",
                    "category": "GROCERIES",
                }
            ]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.json()["imported"] == 1
        result = await db_session.execute(select(Transaction).where(Transaction.user_id == user.id))
        transaction = result.scalar_one()
        assert transaction.category_id == groceries.id

    async def test_unmatched_category_becomes_uncategorized(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes(
            [
                {
                    "date": "2026-03-01",
                    "amount": "-50.00",
                    "description": "Store",
                    "category": "Nonexistent",
                }
            ]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.json()["imported"] == 1
        result = await db_session.execute(select(Transaction).where(Transaction.user_id == user.id))
        assert result.scalar_one().category_id is None

    async def test_a_blank_category_cell_becomes_uncategorized(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes(
            [{"date": "2026-03-01", "amount": "-50.00", "description": "Store", "category": ""}]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.json()["imported"] == 1


@pytest.mark.integration
class TestDeduplication:
    async def test_skips_a_row_matching_an_existing_transaction(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            description="Groceries",
            occurred_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        content = csv_bytes(
            [{"date": "2026-03-01", "amount": "-50.00", "description": "Groceries"}]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        body = response.json()
        assert body["imported"] == 0
        assert body["skipped_duplicates"] == 1

    async def test_skips_a_duplicate_within_the_same_file(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes(
            [
                {"date": "2026-03-01", "amount": "-50.00", "description": "Groceries"},
                {"date": "2026-03-01", "amount": "-50.00", "description": "Groceries"},
            ]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        body = response.json()
        assert body["imported"] == 1
        assert body["skipped_duplicates"] == 1

    async def test_a_different_amount_on_the_same_day_is_not_a_duplicate(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            description="Groceries",
            occurred_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        content = csv_bytes(
            [{"date": "2026-03-01", "amount": "-60.00", "description": "Groceries"}]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.json()["imported"] == 1

    async def test_only_the_callers_own_transactions_count_as_duplicates(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_transaction(
            db_session,
            grace,
            amount="-50.00",
            description="Groceries",
            occurred_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        content = csv_bytes(
            [{"date": "2026-03-01", "amount": "-50.00", "description": "Groceries"}]
        )

        response = await db_client.post(URL, headers=auth(ada), files=upload(content))

        assert response.json()["imported"] == 1

    async def test_only_the_callers_own_categories_are_matched(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_category(db_session, grace, name="Groceries")
        content = csv_bytes(
            [
                {
                    "date": "2026-03-01",
                    "amount": "-50.00",
                    "description": "Store",
                    "category": "Groceries",
                }
            ]
        )

        response = await db_client.post(URL, headers=auth(ada), files=upload(content))

        assert response.json()["imported"] == 1
        result = await db_session.execute(select(Transaction).where(Transaction.user_id == ada.id))
        assert result.scalar_one().category_id is None


@pytest.mark.integration
class TestRowValidation:
    async def test_an_invalid_amount_is_reported_and_does_not_block_other_rows(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes(
            [
                {"date": "2026-03-01", "amount": "not-a-number", "description": "Bad row"},
                {"date": "2026-03-02", "amount": "-10.00", "description": "Good row"},
            ]
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        body = response.json()
        assert response.status_code == 200
        assert body["imported"] == 1
        assert len(body["errors"]) == 1
        assert body["errors"][0]["row"] == 1

    async def test_a_malformed_date_is_reported(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes([{"date": "03/01/2026", "amount": "-10.00", "description": "Bad date"}])

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        body = response.json()
        assert body["imported"] == 0
        assert len(body["errors"]) == 1

    async def test_a_blank_description_is_reported(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes([{"date": "2026-03-01", "amount": "-10.00", "description": ""}])

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.json()["errors"] != []


@pytest.mark.integration
class TestStructuralErrors:
    async def test_rejects_a_file_missing_a_required_column(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        content = csv_bytes(
            [{"date": "2026-03-01", "description": "No amount column"}],
            columns=("date", "description"),
        )

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.status_code == 422

    async def test_rejects_a_file_that_is_not_valid_utf8(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            URL, headers=auth(user), files=upload(b"\xff\xfe\x00date,amount,description")
        )

        assert response.status_code == 422

    async def test_rejects_a_file_over_the_row_limit(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        rows = [
            {"date": "2026-03-01", "amount": "-1.00", "description": f"Row {i}"}
            for i in range(2001)
        ]
        content = csv_bytes(rows)

        response = await db_client.post(URL, headers=auth(user), files=upload(content))

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(URL, files=upload(csv_bytes([])))

        assert response.status_code == 401
