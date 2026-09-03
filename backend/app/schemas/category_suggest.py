from decimal import Decimal
from pydantic import BaseModel

class CategorySuggestRequest(BaseModel):
    """Payload for a category suggestion request.

    The API only needs a transaction description and amount – no
    existing ``category_id`` because no category has been chosen yet.
    """
    description: str
    amount: Decimal


class CategorySuggestResponse(BaseModel):
    """Response containing the suggested category name.
    ``None`` means the agent could not produce a recommendation.
    """
    suggested_category: str | None
