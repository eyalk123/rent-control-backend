import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utc_now_naive
from app.models.tax_value import TaxDocument, TaxValue


class TaxValueRepository:
    def __init__(self, session: Session):
        self.session = session

    def document(self, country: str, tax_year: int) -> TaxDocument | None:
        stmt = select(TaxDocument).where(
            TaxDocument.country == country, TaxDocument.tax_year == tax_year
        )
        return self.session.scalar(stmt)

    def mark_checked(self, document: TaxDocument, when: datetime | None = None) -> None:
        document.checked_at = when or utc_now_naive()
        self.session.commit()

    def save_document(
        self,
        country: str,
        tax_year: int,
        url: str,
        sha256: str,
        values: dict,
        when: datetime | None = None,
    ) -> None:
        """Store a newly read publication and the figures read from it, in one commit, so
        the values and the fingerprint they came from can never disagree."""
        when = when or utc_now_naive()
        document = self.document(country, tax_year)
        if document is None:
            document = TaxDocument(country=country, tax_year=tax_year)
            self.session.add(document)
        document.url, document.sha256 = url, sha256
        document.fetched_at = document.checked_at = when
        for key, value in values.items():
            self._put(country, tax_year, key, value, "document", url, None, sha256, when)
        self.session.commit()

    def sync_law_values(self, country: str, tax_year: int, entries: list) -> int:
        """Make the stored law figures for a year match ``entries`` (``LawValue``s). Returns
        how many rows were added or changed — normally none."""
        changed = 0
        for entry in entries:
            if self._put(
                country, tax_year, entry.key, entry.value, "law", entry.url, entry.reference
            ):
                changed += 1
        if changed:
            self.session.commit()
        return changed

    def values_for(self, country: str, tax_year: int) -> dict:
        """Every stored figure for a year, decoded, keyed by name."""
        stmt = select(TaxValue).where(TaxValue.country == country, TaxValue.tax_year == tax_year)
        return {row.key: json.loads(row.value) for row in self.session.scalars(stmt)}

    def entries_for(self, country: str, tax_year: int) -> dict[str, TaxValue]:
        """Every stored figure for a year with its source row, keyed by name."""
        stmt = select(TaxValue).where(TaxValue.country == country, TaxValue.tax_year == tax_year)
        return {row.key: row for row in self.session.scalars(stmt)}

    def _put(
        self,
        country: str,
        tax_year: int,
        key: str,
        value,
        source: str,
        url: str,
        reference: str | None,
        sha256: str | None = None,
        when: datetime | None = None,
    ) -> bool:
        encoded = json.dumps(value)
        stmt = select(TaxValue).where(
            TaxValue.country == country, TaxValue.tax_year == tax_year, TaxValue.key == key
        )
        row = self.session.scalar(stmt)
        if row is None:
            row = TaxValue(country=country, tax_year=tax_year, key=key)
            self.session.add(row)
        elif (row.value, row.source, row.url, row.reference, row.sha256) == (
            encoded, source, url, reference, sha256
        ):
            return False
        row.value, row.source, row.url = encoded, source, url
        row.reference, row.sha256 = reference, sha256
        row.updated_at = when or utc_now_naive()
        return True
