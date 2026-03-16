# Frontend object description: Property and Renter

All fields below are sent from the frontend in request bodies (POST create, PATCH update) and returned in responses (GET by id, list). Use the same field names and types when building forms and API calls.

**Not in scope yet (to be added later, e.g. with S3):** ID picture (renter), Land Registry Extract PDF (property), contract PDF (property and renter).

---

## Property

| API field | Type | Required (create) | Notes |
|-----------|------|-------------------|--------|
| `id` | integer | — | Read-only; set by backend. |
| `owner_id` | integer | — | Read-only; set by backend from current user. |
| `address` | string | Yes | Street address. |
| `city` | string | Yes | City. |
| `zip_code` | string | Yes | Zip / postal code. |
| `type` | string | Yes | One of: `"apartment"`, `"house"`, `"commercial"`. |
| `sq_ft` | integer | Yes | Surface area in square feet. Display as “Surface area” in the UI. |
| `purchase_price` | number | Yes | Purchase price. |
| `image_url` | string \| null | No | URL of property image (optional). |
| `number_of_rooms` | integer \| null | No | Number of rooms. |
| `parking_numbers` | array of strings \| null | No | List of parking spot identifiers (e.g. `["A-12", "B-34"]`). |
| `electricity_meter_number` | string \| null | No | Electricity meter number. |
| `water_meter_tax` | number \| null | No | Water meter tax amount. |
| `property_tax` | number \| null | No | Property tax amount. |
| `house_committee` | number \| null | No | House committee amount. |

- **Create (POST /properties):** Send `address`, `city`, `zip_code`, `type`, `sq_ft`, `purchase_price`; all other fields are optional.
- **Update (PATCH /properties/{id}):** Send only fields that change; all are optional.
- **Read (GET):** Single property includes nested `renters` array; list endpoint returns the same flat fields (no `renters`).

---

## Renter

| API field | Type | Required (create) | Notes |
|-----------|------|-------------------|--------|
| `id` | integer | — | Read-only; set by backend. |
| `property_id` | integer \| null | No | ID of assigned property; null if unassigned. |
| `first_name` | string | Yes | First name. Send together with `last_name` to represent “name”. |
| `last_name` | string | Yes | Last name. |
| `phone` | string | Yes | Phone number. |
| `email` | string | Yes | Email. |
| `monthly_rent` | number | Yes | Rent amount. Display as “Rent” in the UI. |
| `lease_start` | string (date) | Yes | Lease start date; ISO 8601 date (e.g. `"2025-01-01"`). Display as “Date of start”. |
| `lease_end` | string (date) | Yes | Lease end date; ISO 8601 date. Display as “Date of end”. |
| `number_of_payments` | integer \| null | No | Number of payments (e.g. per year or total). |
| `payment_type` | string \| null | No | E.g. monthly, bimonthly, or free text. |
| `payment_day_of_month` | integer \| null | No | Day of month when rent is due (1–31). Display as “Date of payment”. |
| `insurance_type` | string \| null | No | Insurance type. |
| `insurance_amount` | number \| null | No | Insurance amount. |
| `property` | object \| null | — | Read-only; nested property brief when renter is loaded with property. |

- **Create (POST /renters):** Send `first_name`, `last_name`, `phone`, `email`, `monthly_rent`, `lease_start`, `lease_end`; all other fields are optional.
- **Update (PATCH /renters/{id}):** Send only fields that change; all are optional.
- **Validation:** `payment_day_of_month` must be between 1 and 31 if provided.

---

## Summary

- Every field in the tables above is provided by the frontend on create/update (where applicable) and returned by the API on read/list.
- “Name” in the UI = `first_name` + `last_name`. “Rent” = `monthly_rent`. “Date of start” / “Date of end” = `lease_start` / `lease_end`. “Surface area” = `sq_ft`. “Date of payment” = `payment_day_of_month` (1–31).
- ID picture, Land Registry Extract PDF, and contract PDFs are not implemented yet and will be added later (e.g. with S3).
