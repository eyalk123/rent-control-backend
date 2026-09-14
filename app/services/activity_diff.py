"""Which fields an edit actually changed — the input to an `update` activity-log row.

`model_dump(exclude_unset=True)` is *what the client sent*, not what changed. A form that
resubmits every field it rendered would otherwise read as a twenty-field edit, which makes
the log useless for the question it exists to answer ("did this owner do any work?").

Only field *names* ever come out of here. The values are tenant personal data — a phone
number, an address, the notes on a transaction — and the log is read by analytics queries;
see the module docstring on `app.models.activity_log`.
"""


def changed_fields(entity, update_dict: dict, ignore: set[str] | None = None) -> list[str]:
    """Sorted names of the fields in `update_dict` that differ from `entity`'s values.

    Call it *before* the repository applies the update (so `entity` still holds the old
    values) and with the values already normalised the way they will be stored — enums
    resolved, JSON encoded. Comparing a list against its own stored JSON string reports a
    change on every single save.

    `ignore` drops fields the server derives rather than the owner editing them; those
    move on their own and are not evidence of anybody doing anything.
    """
    ignored = ignore or set()
    return sorted(
        field
        for field, new_value in update_dict.items()
        if field not in ignored and getattr(entity, field, None) != new_value
    )
