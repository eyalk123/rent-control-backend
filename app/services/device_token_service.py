from app.models.device_token import DeviceToken
from app.repositories.device_token_repository import DeviceTokenRepository
from app.schemas.device_token import DeviceTokenCreate


class DeviceTokenService:
    def __init__(self, device_token_repository: DeviceTokenRepository):
        self.device_token_repository = device_token_repository

    def register(self, data: DeviceTokenCreate, owner_id: str) -> DeviceToken:
        return self.device_token_repository.upsert(
            token=data.token,
            owner_id=owner_id,
            platform=data.platform,
        )

    def unregister(self, token: str, owner_id: str) -> bool:
        return self.device_token_repository.delete_by_token(token, owner_id)
