# Architectural Patterns

## 1. Dependency Injection (Layered Composition)

All DI is in `app/api/dependencies.py`. The chain is always:

```
get_db() → get_*_repository(db) → get_*_service(repos) → router endpoint
```

Repos are injected into services; services are injected into routers. Never skip layers.
Use `Annotated[Type, Depends(factory)]` — not bare `Depends()`.

`get_current_user` validates Clerk JWT and returns `{"user_id": str, "role": "owner"}`.
Every protected endpoint receives `current_user` and passes `current_user["user_id"]` down to the service.

## 2. CRUD Repository Template

Every repository follows the same interface (`app/repositories/*.py`):

```python
class XyzRepository:
    def __init__(self, session: Session): ...
    def get_all(self, owner_id: str) -> list[Model]: ...
    def get_by_id(self, id: int, owner_id: str | None = None) -> Model | None: ...
    def create(self, model: Model) -> Model: ...      # add + commit + refresh
    def update(self, model: Model, data: dict) -> Model: ...  # setattr loop + commit + refresh
    def delete(self, id: int) -> bool: ...            # delete + commit
```

All queries use SQLAlchemy 2.0 style: `select(Model).where(...)` + `session.scalars(...).all()`.
Use `selectinload(Model.relation)` when relationships will be accessed — prevents N+1.

### Nullable Field Updates

In update methods, use a `nullable_fields` set to allow explicit NULL via `model_dump(exclude_unset=True)`:

```python
nullable_fields = {"image_url", "property_owner", ...}
for key, value in data.items():
    if hasattr(model, key) and (value is not None or key in nullable_fields):
        setattr(model, key, value)
```

See `app/repositories/property_repository.py`.

## 3. Service Layer Responsibilities

Services (`app/services/*.py`) do exactly three things:

1. **FK validation** — verify referenced IDs exist and belong to the owner
2. **Transformation** — convert schema → model (enums, JSON encoding, computed fields)
3. **Authorization** — verify `owner_id` matches before returning or mutating

Services never write SQL. They call repo methods.
For complex read DTOs with computed fields (e.g. `property_name`, `renter_name`), use a private `_*_to_read()` method — see `app/services/transaction_service.py`.

## 4. Schema Conventions (Pydantic v2)

Every domain has: `XyzCreate`, `XyzUpdate` (all fields `Optional`), `XyzRead`.
All Read schemas require `model_config = ConfigDict(from_attributes=True, populate_by_name=True)`.

**camelCase API, snake_case DB**: use `Field(validation_alias="snake_case")` on Read schemas.

**Custom validators**:
- `@field_validator` for field-level rules (e.g. `amount > 0`)
- `@model_validator(mode="after")` for computed fields (e.g. `hasRenters`, `isActive`)

## 5. Multi-Tenancy (owner_id Filtering)

Every top-level resource (Property, Supplier, ExpenseCategory) has an `owner_id: String` column storing the Clerk user ID.
Repositories always filter by `owner_id` — never return records across tenants.
Services do a secondary check after fetching: if `resource.owner_id != owner_id`, raise `403`.

`ExpenseCategory` is the exception: predefined categories have `owner_id = NULL` and are shared across all owners. Queries use `OR (owner_id = X OR owner_id IS NULL)`.

## 6. Enum Handling

Enums are defined in `app/models/*.py` as `class XyzEnum(str, enum.Enum)`.
SQLAlchemy column uses `values_callable=lambda x: [e.value for e in x]` to store string values.
Conversion from raw string to enum happens in the service layer before passing to the model constructor.

## 7. JSON in Text Columns

Two fields store JSON as `Text` columns:
- `Renter.lease_years` — array of `{amount, type}` objects
- `Property.parking_numbers` — array of strings

**Write:** `json.dumps(value)` in the service before saving.
**Read:** `json.loads(value)` in the schema validator or service `_to_read()` method.
This was chosen over JSONB to keep migrations simple — do not add new JSON-as-Text fields; use JSONB or a related table instead.

## 8. Relationship Loading Strategy

Default: use `selectinload()` for all relationships accessed in a response.
Never rely on lazy loading — sessions close after the request and lazy loads will fail.

Complex queries (e.g. TransactionRepository.list) combine `outerjoin()` for filtering with `selectinload()` on the final result for serialization. See `app/repositories/transaction_repository.py`.

## 9. Migration Conventions

File naming: `YYYYMMDD_description.py`.
Always import `app.config.settings.DATABASE_URL` in `alembic/env.py` — never hardcode the URL.
Every migration must have both `upgrade()` and `downgrade()`.
Run `alembic upgrade head` before server start (baked into Railway start command).
