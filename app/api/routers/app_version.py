from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter()


class PlatformVersion(BaseModel):
    latest: str | None
    minimum: str | None


class AppVersionRead(BaseModel):
    ios: PlatformVersion
    android: PlatformVersion


@router.get("", response_model=AppVersionRead)
def get_app_version():
    """The store versions the mobile app compares itself against at start-up.

    Unauthenticated on purpose: an outdated build must be told to update even when it can
    no longer sign in. The comparison happens on the device; this only reports what the
    deployment is configured with, and `null` means "do not prompt".
    """
    return AppVersionRead(
        ios=PlatformVersion(
            latest=settings.MOBILE_LATEST_VERSION_IOS or None,
            minimum=settings.MOBILE_MIN_VERSION_IOS or None,
        ),
        android=PlatformVersion(
            latest=settings.MOBILE_LATEST_VERSION_ANDROID or None,
            minimum=settings.MOBILE_MIN_VERSION_ANDROID or None,
        ),
    )
