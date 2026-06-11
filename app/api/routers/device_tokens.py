from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_current_user, get_device_token_service
from app.schemas.device_token import DeviceTokenCreate, DeviceTokenRead
from app.services.device_token_service import DeviceTokenService

router = APIRouter()


@router.post("", response_model=DeviceTokenRead, status_code=201)
def register_device_token(
    data: DeviceTokenCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    device_token_service: Annotated[DeviceTokenService, Depends(get_device_token_service)],
):
    """Register (or refresh) this device's Expo push token for the current user."""
    return device_token_service.register(data, owner_id=current_user["user_id"])


@router.delete("", status_code=204)
def unregister_device_token(
    token: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    device_token_service: Annotated[DeviceTokenService, Depends(get_device_token_service)],
):
    """Unregister this device's push token (called on sign-out)."""
    device_token_service.unregister(token, owner_id=current_user["user_id"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
