from fastapi import APIRouter, HTTPException, status

from app.schemas.country import CountryRead
from app.services import country_service

router = APIRouter()


@router.get("", response_model=list[CountryRead])
def list_countries():
    """The whole country table, name-ordered — the signup picker and both formatters.

    Unauthenticated on purpose: it is static reference data with nothing owner-specific in
    it, and the signup country gate runs before there is anything useful to authenticate
    against. Clients fetch it once at start-up and cache it.
    """
    return country_service.all_countries()


@router.get("/{country_code}", response_model=CountryRead)
def get_country(country_code: str):
    """One country. 404 on an unknown code rather than the neutral fallback.

    Reads inside the app resolve an unknown code to safe defaults, because a property page
    must render. This endpoint is a lookup, and silently answering a typo with a plausible
    config would hide the mistake at exactly the point someone could still fix it.
    """
    if not country_service.is_known(country_code):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown country code"
        )
    return country_service.config_for(country_code)
