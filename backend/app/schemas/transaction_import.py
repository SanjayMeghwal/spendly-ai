"""Response schema for POST /transactions/import.

No request schema here - the request body is multipart/form-data (an
uploaded file), which FastAPI's UploadFile parameter type describes on its
own; there is no JSON body for Pydantic to validate.
"""

from pydantic import BaseModel, Field


class TransactionImportError(BaseModel):
    """One row that could not be imported."""

    row: int = Field(description="1-indexed data row, not counting the header.")
    reason: str


class TransactionImportResult(BaseModel):
    """The outcome of a CSV import.

    Always returned with a 200 - a partially-successful import (some rows
    imported, some skipped, some errored) is still a successful request.
    Only a structurally broken upload (bad encoding, missing required
    columns, too many rows) fails the whole request with a 422 instead of
    reaching this response at all.
    """

    imported: int
    skipped_duplicates: int
    errors: list[TransactionImportError]
