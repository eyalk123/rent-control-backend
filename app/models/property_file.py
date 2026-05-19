from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class PropertyFile(Base):
    __tablename__ = "property_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(Text, nullable=False)
    label = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    property = relationship("Property", back_populates="files")
