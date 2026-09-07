from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel

# ---------- Categorization request / response ----------
class CategorizationRequest(BaseModel):
    description: str
    amount: Decimal
    date: Optional[datetime] = None
    category_id: Optional[UUID] = None

class CategorizationResponse(BaseModel):
    category_id: Optional[UUID]
    category_name: str
    confidence: float
    reason: str

# ---------- Budget advisor request / response ----------
class AdvisorRequest(BaseModel):
    question: Optional[str] = None

class AdvisorResponse(BaseModel):
    advice: str
    recommended_actions: List[str] = []
    warnings: List[str] = []
