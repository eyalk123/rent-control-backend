# Rent Control Backend – Frontend Integration Guide

Use this document as a prompt or reference when building or integrating the frontend with the Rent Control property management API.

---

## Overview

The backend is a **FastAPI** REST API for property and renter management. Properties belong to owners (users), and renters can be assigned to properties. All endpoints require authentication (currently mocked; see Authentication section).

**Base URL:** `http://localhost:8000` (or your deployed URL)

**API docs:** `GET /docs` (Swagger UI) and `GET /redoc` (ReDoc)

---

## Authentication

Authentication is **mocked**. All endpoints expect a "logged-in" user, but the backend does not validate tokens yet. `get_current_user` always returns `{ "user_id": 1, "role": "owner" }`.

- In development, you can call the API **without** auth headers.
- For production, plan for real auth (e.g., Bearer token, session cookie). The frontend should send credentials in whatever format the backend will support.

---

## API Endpoints

### Properties

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/properties` | List all properties for the current user |
| `GET` | `/properties/{property_id}` | Get one property with its renters |
| `POST` | `/properties` | Create a new property |
| `PATCH` | `/properties/{property_id}` | Partially update a property |
| `DELETE` | `/properties/{property_id}` | Delete a property |
| `POST` | `/properties/{property_id}/image` | Upload property image (multipart/form-data) |

### Renters

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/renters` | List all renters (owned by current user’s properties) |
| `GET` | `/renters/{renter_id}` | Get one renter with property details |
| `POST` | `/renters` | Create a new renter (optionally assign to a property) |
| `PATCH` | `/renters/{renter_id}` | Partially update a renter |
| `DELETE` | `/renters/{renter_id}` | Delete a renter |

---

## Data Models & Payloads

### Property

**Full response (single property with renters):**
```typescript
interface Property {
  id: number;
  owner_id: number;
  address: string;
  city: string;
  zip_code: string;
  type: "apartment" | "house" | "commercial";
  sq_ft: number;
  purchase_price: number;
  image_url: string | null;
  renters: Renter[];
}
```

**List response (property without renters):**
```typescript
interface PropertyListItem {
  id: number;
  owner_id: number;
  address: string;
  city: string;
  zip_code: string;
  type: "apartment" | "house" | "commercial";
  sq_ft: number;
  purchase_price: number;
  image_url: string | null;
}
```

**Create payload (what the frontend sends on `POST /properties`):**
```typescript
interface PropertyCreate {
  address: string;
  city: string;
  zip_code: string;
  type: "apartment" | "house" | "commercial";
  sq_ft: number;
  purchase_price: number;
  image_url?: string | null;  // optional
}
```

**Update payload (what the frontend sends on `PATCH /properties/{id}`):**  
All fields are optional. Send only what changes.
```typescript
interface PropertyUpdate {
  address?: string;
  city?: string;
  zip_code?: string;
  type?: "apartment" | "house" | "commercial";
  sq_ft?: number;
  purchase_price?: number;
  image_url?: string | null;
}
```

**Notes:**
- `id` and `owner_id` are never sent by the frontend (auto-generated / from auth).
- `type` must be lowercase: `"apartment"`, `"house"`, or `"commercial"`.
- `renters` is only in the single-property response (`GET /properties/{id}`).

---

### Renter

**Full response:**
```typescript
interface Renter {
  id: number;
  property_id: number | null;
  first_name: string;
  last_name: string;
  phone: string;
  email: string;
  monthly_rent: number;
  lease_start: string;   // ISO date "YYYY-MM-DD"
  lease_end: string;     // ISO date "YYYY-MM-DD"
  property: PropertyBrief | null;  // when included
}

interface PropertyBrief {
  id: number;
  address: string;
  city: string;
  type: "apartment" | "house" | "commercial";
}
```

**Create payload (`POST /renters`):**
```typescript
interface RenterCreate {
  property_id?: number | null;  // optional – assign to property or leave unassigned
  first_name: string;
  last_name: string;
  phone: string;
  email: string;
  monthly_rent: number;
  lease_start: string;   // "YYYY-MM-DD"
  lease_end: string;    // "YYYY-MM-DD"
}
```

**Update payload (`PATCH /renters/{id}`):**  
All fields optional. Use for edits, moving to another property (`property_id`), or lease renewal.
```typescript
interface RenterUpdate {
  property_id?: number | null;
  first_name?: string;
  last_name?: string;
  phone?: string;
  email?: string;
  monthly_rent?: number;
  lease_start?: string;
  lease_end?: string;
}
```

---

## Property Image Upload

`POST /properties/{property_id}/image` expects **multipart/form-data** with a file field.

Example (fetch):
```javascript
const formData = new FormData();
formData.append("file", fileInput.files[0]);

const response = await fetch(`/properties/${propertyId}/image`, {
  method: "POST",
  body: formData,
});
```

The backend returns the updated property (same shape as `PropertyListItem`) with the new `image_url`.

---

## Error Responses

- **404 Not Found:** Resource not found or user lacks access.
  ```json
  { "detail": "Property not found" }
  ```
- **422 Unprocessable Entity:** Validation errors (wrong types, missing required fields).
- **403 Forbidden:** Access denied (e.g., property belongs to another owner).

---

## CORS

CORS is enabled with `allow_origins=["*"]`, so the frontend can call the API from any origin during development.

---

## Summary for Frontend Implementation

1. **Properties:** Use `PropertyCreate` for create, `PropertyUpdate` for PATCH. Do not send `id`, `owner_id`, or `renters`.
2. **Renters:** Use `RenterCreate` for create (with optional `property_id`). Use `RenterUpdate` for edits and lease changes.
3. **Dates:** Use ISO strings `"YYYY-MM-DD"` for `lease_start` and `lease_end`.
4. **Property types:** Use lowercase `"apartment"`, `"house"`, `"commercial"`.
5. **Images:** Use multipart/form-data for property image uploads.
6. **Auth:** No auth required in current mock setup; prepare for Bearer/session auth later.
