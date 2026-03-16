from pydantic import BaseModel, ConfigDict


class ExpenseCategoryRead(BaseModel):
    id: int
    key: str
    is_active: bool
    sort_order: int

    model_config = ConfigDict(from_attributes=True)
