"""Sends push notifications through the Expo Push Service.

Expo relays to FCM (Android) / APNs (iOS) for us, so the backend only POSTs JSON
to a single endpoint. Sends are best-effort: any failure is swallowed and logged
so a push problem never breaks the request that triggered it.
"""
import logging

import requests

from app.config import settings
from app.repositories.device_token_repository import DeviceTokenRepository

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# Expo accepts up to 100 messages per request.
_BATCH_SIZE = 100
_TIMEOUT_SECONDS = 15


class PushService:
    def __init__(self, device_token_repository: DeviceTokenRepository):
        self.device_token_repository = device_token_repository

    def send_push(self, owner_id: str, title: str, body: str, data: dict | None = None) -> int:
        """Push to every device registered for an owner. Returns the number of
        messages Expo accepted. Tokens Expo reports as dead are pruned."""
        tokens = [t.token for t in self.device_token_repository.list_by_owner(owner_id)]
        if not tokens:
            return 0

        accepted = 0
        for start in range(0, len(tokens), _BATCH_SIZE):
            batch = tokens[start:start + _BATCH_SIZE]
            messages = [
                {"to": token, "title": title, "body": body, "data": data or {}}
                for token in batch
            ]
            accepted += self._post_batch(batch, messages)
        return accepted

    def _post_batch(self, tokens: list[str], messages: list[dict]) -> int:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if settings.EXPO_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {settings.EXPO_ACCESS_TOKEN}"

        try:
            response = requests.post(
                EXPO_PUSH_URL, json=messages, headers=headers, timeout=_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            tickets = response.json().get("data", [])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Expo push request failed: %s", exc)
            return 0

        accepted = 0
        # Tickets come back in the same order as the messages we sent.
        for token, ticket in zip(tokens, tickets):
            if ticket.get("status") == "ok":
                accepted += 1
                continue
            error = (ticket.get("details") or {}).get("error")
            logger.warning("Expo push ticket error for a token: %s", ticket.get("message"))
            if error == "DeviceNotRegistered":
                self.device_token_repository.delete_token(token)
        return accepted
