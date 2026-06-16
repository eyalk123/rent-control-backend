from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import (
    get_current_user,
    get_notification_preferences_service,
)
from app.schemas.notification import (
    NotificationRuleCreate,
    NotificationRuleRead,
    NotificationRuleUpdate,
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    PreferencesRead,
    RulePreviewRequest,
    RulePreviewResponse,
)
from app.services.notification_preferences_service import NotificationPreferencesService

router = APIRouter()

Service = Annotated[NotificationPreferencesService, Depends(get_notification_preferences_service)]
User = Annotated[dict, Depends(get_current_user)]


@router.get("/notification-preferences", response_model=PreferencesRead)
def get_preferences(current_user: User, service: Service):
    """The full preferences bundle: global settings + the user's rules."""
    owner_id = current_user["user_id"]
    return {
        "settings": service.get_settings(owner_id),
        "rules": service.list_rules(owner_id),
    }


@router.put("/notification-preferences/settings", response_model=NotificationSettingsRead)
def update_settings(data: NotificationSettingsUpdate, current_user: User, service: Service):
    return service.update_settings(current_user["user_id"], data.model_dump(exclude_unset=True))


@router.post("/notification-rules", response_model=NotificationRuleRead, status_code=201)
def create_rule(data: NotificationRuleCreate, current_user: User, service: Service):
    return service.create_rule(current_user["user_id"], data.model_dump())


@router.post("/notification-rules/preview", response_model=RulePreviewResponse)
def preview_rule(data: RulePreviewRequest, current_user: User, service: Service):
    return service.preview(
        owner_id=current_user["user_id"],
        event_type=data.event_type,
        offsets=data.offsets,
        property_ids=data.scope_property_ids,
        property_owners=data.scope_property_owners,
        renter_ids=data.scope_renter_ids,
    )


@router.patch("/notification-rules/{rule_id}", response_model=NotificationRuleRead)
def update_rule(
    rule_id: int, data: NotificationRuleUpdate, current_user: User, service: Service
):
    rule = service.update_rule(
        rule_id, current_user["user_id"], data.model_dump(exclude_unset=True)
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.delete("/notification-rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, current_user: User, service: Service):
    if not service.delete_rule(rule_id, current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Rule not found")
    return None
