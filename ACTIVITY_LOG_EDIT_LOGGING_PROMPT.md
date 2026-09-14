# Task prompt: log edits to `activity_log`

*Paste everything below the line into a fresh Claude Code session started in
`rent-control-backend/`. It is self-contained.*

---

## Goal

`activity_log` currently records deletions and two renter lifecycle events. It does **not**
record edits, which leaves a real gap: an owner who spends a week correcting rent amounts and
fixing phone numbers is invisible to any "was this user active?" query, because the only
evidence of their work is `updated_at`, which is overwritten in place and keeps just the most
recent edit.

Add an `update` action so edits leave an append-only trace, the same way deletions already do.

This is wanted for a forthcoming internal analytics dashboard (see `ANALYTICS_FEASIBILITY.md`,
the "Weekly active owners" row), but it is not analytics-specific — the table's own docstring
says it was shaped generically so it could grow into a full activity log.

## What exists today

`app/models/activity_log.py` — `owner_id`, `action`, `entity_type`, `entity_id`, `label`,
`details` (JSON), `created_at`. Indexed on `owner_id` and `created_at`.

`app/repositories/activity_log_repository.py` exposes **`record_action(owner_id, action,
entity_type, entity_id, label=None, details=None)`** — already generic, so **no new
repository method and no migration are needed**. It deliberately does not commit: the caller
does, so the log entry lands in the same transaction as the change it describes and the two
can never disagree. Preserve that property.

Current writers:

| Action | Where |
|---|---|
| `delete` | `property_service.delete_property`, `renter_service.delete_renter`, `transaction_service.delete_transaction` |
| `terminate` | `renter_service.terminate_lease` |
| `reopen` | `renter_service.reopen_lease` |

## Scope — do exactly these four

All four already receive `activity_log_repository` in their constructor, so no dependency
wiring changes are required:

1. `app/services/property_service.py` → `update_property` (~line 94)
2. `app/services/renter_service.py` → `update_renter` (~line 280)
3. `app/services/transaction_service.py` → `update_revenue` (~line 242)
4. `app/services/transaction_service.py` → `update_expense` (~line 293)

`supplier_service.update_supplier` is **out of scope** — it has no `activity_log_repository`
and would need new DI wiring for a low-value record. Mention it in your summary; don't do it.

**Do not add a `create` action.** Creations are already discoverable from each row's own
`created_at`, so logging them would double the write volume to record something already known.

## How to implement it

In each method, the update payload arrives as a Pydantic model and is turned into a dict with
`data.model_dump(exclude_unset=True)`. That dict is *what the client sent*, not what actually
changed — a form that resubmits every field unchanged would otherwise look like a 20-field
edit.

So: **compare the incoming values against the current model values before applying the
update**, and record only the fields whose value genuinely differs. If nothing differs, write
no log row at all.

Follow the shape the existing `delete` calls use:

```python
changed = [
    field for field, new_value in update_dict.items()
    if getattr(entity, field, None) != new_value
]
if changed and self.activity_log_repository is not None:
    self.activity_log_repository.record_action(
        owner_id=owner_id,
        action="update",
        entity_type="property",
        entity_id=entity.id,
        label=...,                      # same label expression the delete path uses
        details={"fields": sorted(changed)},
    )
```

Place the call **before** the repository applies the update, so the comparison sees the old
values. Do not commit — the existing commit covers it.

### 🔴 The one hard rule

**`details` records field *names* only. Never values, old or new.**

A renter's `phone`, `first_name`, `email`, a property's `address`, a transaction's `notes` —
these are tenant personal data. `{"fields": ["phone", "base_rent"]}` is fine and is what makes
the log useful. `{"phone": {"from": "050-...", "to": "052-..."}}` is not, and would quietly put
contact details into a table that is read by analytics queries.

(`label` is a separate matter: it already holds names and addresses on the delete path, which
is a deliberate, documented choice — it is what makes the log readable, and it is erased with
the account in `user_service.delete_account`. Keep using it exactly as the delete path does,
and don't extend that pattern into `details`.)

### Note on enum and JSON fields

`update_property` normalises some fields before applying them — `type` becomes a
`PropertyTypeEnum`, `parking_numbers` is JSON-encoded into a string. Compare *after* that
normalisation, or an unchanged `parking_numbers` will compare a list against a stored JSON
string and report a change on every single save. This is the most likely bug in this task —
check it explicitly.

`renter_service.update_renter` has the most involved update path (lease recomputation); read
it fully before editing.

## Retention

Nothing to do. `ACTIVITY_LOG_RETENTION_DAYS` defaults to 365 and the existing sweep in
`retention_service` covers the whole table, new action included.

## Tests

Add to the existing service tests, matching their style:

- Editing a field writes exactly one `update` row whose `details["fields"]` names that field.
- Submitting an unchanged payload writes **no** row.
- A multi-field edit records all changed field names and no unchanged ones.
- `details` contains no values — assert on the dict's shape, not just its keys.
- The log row and the update land in the same transaction: if the update raises, no log row
  survives.
- `parking_numbers` resubmitted unchanged does not register as a change (the normalisation
  trap above).

Run: `pytest tests/ -q`

## Definition of done

- The four methods log `update` when something actually changed, and nothing when it didn't.
- No migration, no new repository method, no DI changes.
- No values in `details` anywhere.
- Full suite green.
- Update the `activity_log.py` module docstring — it currently says *"Only deletions are
  written today"*, which is already out of date (terminate/reopen exist) and will be more so.
