"""A record that an account existed and was deleted — with nothing identifying in it.

Account deletion is an erasure: the owner's rows go, including the activity log, because the
labels in it are renter names and addresses. That leaves no way to answer "how many accounts
churned, and how much did they have?", which is a legitimate thing to want to know.

So one row survives, holding a SHA-256 of the owner id and a few counts. The hash is not
reversible to a Firebase uid, and counts describe volume, not people. Nothing here is personal
data, which is the whole point — if a field would identify anyone, it does not belong in this
table.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.models.base import Base


class DeletedAccount(Base):
    __tablename__ = "deleted_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id_hash = Column(String, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    properties_count = Column(Integer, nullable=False, default=0)
    renters_count = Column(Integer, nullable=False, default=0)
    transactions_count = Column(Integer, nullable=False, default=0)
