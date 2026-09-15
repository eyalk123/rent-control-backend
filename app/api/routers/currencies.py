from fastapi import APIRouter, HTTPException, status

from app.countries import currencies
from app.schemas.country import CurrencyRead

router = APIRouter()


@router.get("", response_model=list[CurrencyRead])
def list_currencies():
    """The whole currency table, name-ordered — the signup picker and both formatters.

    Unauthenticated for the same reason ``/countries`` is: static reference data with
    nothing owner-specific in it, needed by a gate that runs before there is anything
    useful to authenticate against. Clients fetch it once at start-up and cache it.
    """
    return currencies.all_currencies()


@router.get("/{code}", response_model=CurrencyRead)
def get_currency(code: str):
    """One currency. 404 on an unknown code rather than a plausible-looking fallback.

    Reads inside the app resolve an unknown currency to the country's own, because a
    property page must render. This endpoint is a lookup, and quietly answering a typo
    would hide the mistake while it is still cheap to fix.
    """
    currency = currencies.get(code)
    if currency is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown currency code"
        )
    return currency
