from typing import Optional

from pydantic import BaseModel, ConfigDict


class SupplierRead(BaseModel):
    id: int
    category_id: int
    name: str
    is_active: bool
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
