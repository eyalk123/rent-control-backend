import json


def get_firebase_app():
    """The process's firebase_admin app, initialised on first use from the service account.
    None when FIREBASE_SERVICE_ACCOUNT_JSON is not set (local dev, tests).

    Shared by Storage and Auth, so it carries no per-service options: Storage passes its
    bucket name explicitly instead of relying on a `storageBucket` default here.
    """
    import firebase_admin
    from firebase_admin import credentials
    from app.config import settings

    sa_json = settings.FIREBASE_SERVICE_ACCOUNT_JSON
    if not sa_json:
        return None

    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app(credentials.Certificate(json.loads(sa_json)))
