
from pydantic import BaseModel, ConfigDict

from app.schemas.stored_file import StoredFilePath
from app.schemas.utc import UtcDatetime


class PropertyFileCreate(BaseModel):
    url: StoredFilePath
    label: str


class PropertyFileRead(BaseModel):
    id: int
    property_id: int
    url: str
    label: str
    created_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)
