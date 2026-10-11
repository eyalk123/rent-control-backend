import logging

from app.services.firebase_app import get_firebase_app

logger = logging.getLogger(__name__)


def delete_auth_user(uid: str) -> None:
    """Delete the Firebase Auth user — the sign-in itself, not our data.

    Done here rather than by the client's `user.delete()`, because Firebase only lets a
    client delete a user who signed in within the last few minutes, and asking for a fresh
    sign-in on top of typing DELETE was a step we chose not to keep. The Admin SDK has no
    such rule.

    A user who is already gone counts as deleted, so a retry after a partial failure
    succeeds. Any other failure raises: the caller must not report the account deleted
    while its sign-in still works.
    """
    from firebase_admin import auth

    app = get_firebase_app()
    if app is None:
        logger.info("FIREBASE_SERVICE_ACCOUNT_JSON not set — skipping Auth user deletion for %s", uid)
        return
    try:
        auth.delete_user(uid, app=app)
    except auth.UserNotFoundError:
        pass
