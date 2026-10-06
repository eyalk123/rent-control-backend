from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.stored_file import StoredFilePath


class PropertyFileCreate(BaseModel):
    url: StoredFilePath
    label: str


class PropertyFileRead(BaseModel):
    id: int
    property_id: int
    url: str
    label: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
