# SFMS Backend — API Documentation

Base URL (local): `http://127.0.0.1:8000`
Base URL (prod): `https://your-domain.com`
All endpoints below are prefixed with **`/api`** (set via `API_V1_PREFIX` in `.env`).

Auth: every endpoint except the ones under **Auth** requires
`Authorization: Bearer <access_token>`.

> **How to extend this later:** each numbered section below maps 1:1 to one file in
> `app/api/v1/endpoints/`. To add a field, change validation, or add a new route, edit that one
> file + its matching schema in `app/schemas/`. Nothing else needs to change — the router
> (`app/api/v1/router.py`) auto-includes whatever routes exist in each file.

---

## 1. Auth — `app/api/v1/endpoints/auth.py`

| Method | Path | Auth | Body | Notes |
|---|---|---|---|---|
| POST | `/api/auth/register` | No | `{username, email, password}` | Creates user + seeds "Cash" & "External" accounts. Rate-limited 5/min. |
| POST | `/api/auth/login` | No | `{email, password}` | Returns `{access_token, refresh_token, token_type}`. Locks account after `MAX_FAILED_LOGIN_ATTEMPTS`. Rate-limited 10/min. |
| POST | `/api/auth/refresh-token` | No | `{refresh_token}` | Rotates the refresh token (old one is revoked, new one issued). |
| POST | `/api/auth/logout` | No | `{refresh_token}` | Revokes that refresh token. Returns 204. |
| POST | `/api/auth/verify-email` | No | `{token}` | Confirms the email using the token issued at registration. |
| POST | `/api/auth/forgot-password` | No | `{email}` | Always returns 202 (no account-enumeration leak). Rate-limited 3/min. |
| POST | `/api/auth/reset-password` | No | `{token, new_password}` | Consumes the reset token, sets new password. |

---

## 2. Users — `app/api/v1/endpoints/users.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/users/me` | — | Current user profile. |
| PUT | `/api/users/me` | `{username?}` | Update editable profile fields. |
| PUT | `/api/users/me/password` | `{current_password, new_password}` | Change password (requires current password). Returns 204. |
| DELETE | `/api/users/me` | — | Soft delete; `deletion_scheduled_at` = now + 30 days. Returns 202. |

---

## 3. Transaction Types — `app/api/v1/endpoints/transaction_types.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/transaction-types?active_only=false` | — | Returns user's custom types **+** the 10 system defaults. |
| POST | `/api/transaction-types` | `{name, nature, icon?, color?}` | `nature` = `inflow` \| `outflow`. |
| PUT | `/api/transaction-types/{id}` | any subset of the above + `is_active` | System-default rows (user_id=NULL) can't be edited — 403. |
| DELETE | `/api/transaction-types/{id}` | — | Soft delete (`is_active=false`). System defaults can't be deleted. |

**To add an 11th default type or change one:** edit
`DEFAULT_TRANSACTION_TYPES` in `app/utils/seed_system_defaults.py` and re-run the script — or
just `POST` a new one as an admin/user, no code change required (per the doc's own note).

---

## 4. Categories — `app/api/v1/endpoints/categories.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/categories?transaction_type_id=&active_only=` | — | Filter by type to populate the category dropdown. |
| POST | `/api/categories` | `{transaction_type_id, name, description?, icon?, color?}` | Unique per `(user_id, transaction_type_id, name)`. |
| PUT | `/api/categories/{id}` | partial | |
| DELETE | `/api/categories/{id}` | — | Soft delete. |

---

## 5. Groups — `app/api/v1/endpoints/groups.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/groups?category_id=&active_only=` | — | 0/1/many groups per category drives the UI branching (workflow 5.1). |
| POST | `/api/groups` | `{category_id, name, description?}` | `category_id` required (NOT NULL, per doc fix). |
| PUT | `/api/groups/{id}` | partial | |
| DELETE | `/api/groups/{id}` | — | Soft delete. |

---

## 6. Contacts — `app/api/v1/endpoints/contacts.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/contacts?group_id=&active_only=` | — | `group_id` filter as specified in section 8 of the architecture doc. |
| POST | `/api/contacts` | `{group_id, name, phone?, email?, address?, notes?}` | `group_id` required (NOT NULL, per doc fix). |
| PUT | `/api/contacts/{id}` | partial | |
| DELETE | `/api/contacts/{id}` | — | Soft delete. |

---

## 7. Accounts — `app/api/v1/endpoints/accounts.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/accounts?active_only=&include_system=false` | — | Hides the internal "External" account unless `include_system=true`. |
| POST | `/api/accounts` | `{name, account_type, account_number?, bank_name?, branch?, current_balance?}` | `account_type` = `cash`\|`digital_wallet`\|`bank`. |
| PUT | `/api/accounts/{id}` | partial | The system "External" account can't be edited. |
| DELETE | `/api/accounts/{id}` | — | Soft delete. The default "Cash" and system "External" accounts can't be deleted. |

---

## 8. Payment Methods — `app/api/v1/endpoints/payment_methods.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/payment-methods?account_id=&active_only=` | — | Filter by account to populate the payment-method dropdown after account selection. |
| POST | `/api/payment-methods` | `{account_id, name}` | |
| PUT | `/api/payment-methods/{id}` | partial | |
| DELETE | `/api/payment-methods/{id}` | — | Soft delete. |

---

## 9. Priorities — `app/api/v1/endpoints/priorities.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/priorities?active_only=` | — | User's custom priorities **+** the 3 system defaults (Emergency/Necessary/Wish). |
| POST | `/api/priorities` | `{name}` | |
| PUT | `/api/priorities/{id}` | partial | System defaults can't be edited. |
| DELETE | `/api/priorities/{id}` | — | Soft delete. System defaults can't be deleted. |

---

## 10. Transactions — `app/api/v1/endpoints/transactions.py`

This is the core module. Business rules live in `app/crud/transaction_service.py`.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/transactions?page=&page_size=` | — | Paginated list, newest first. |
| POST | `/api/transactions` | see below | Runs the full validation + balance/loan/budget engine. |
| GET | `/api/transactions/{id}` | — | |
| PUT | `/api/transactions/{id}` | `{description?, reference_no?, remarks?, priority_id?, payment_method_id?, is_active?}` | Only non-financial fields are editable. Amount/accounts/category/type are immutable — delete + recreate instead. |
| DELETE | `/api/transactions/{id}` | — | Soft delete; **automatically reverses** account balances, loan `remaining_amount`, and budget `spent_amount`. |
| POST | `/api/transactions/filter` | `TransactionFilter` (see below) | Paginated, multi-field filtering. |

### POST /api/transactions — request body
```json
{
  "txn_date": "2026-08-28",
  "txn_time": "14:30:00",
  "transaction_type_id": 1,
  "category_id": 5,
  "description": "Grocery shopping",
  "from_account_id": 3,
  "to_account_id": null,
  "payment_method_id": 7,
  "contact_id": null,
  "priority_id": 2,
  "amount": 1500.00,
  "charges": 0.00,
  "reference_no": null,
  "remarks": null,
  "loan_id": null,
  "due_date": null
}
```
Field notes:
- `from_account_id` / `to_account_id`: **omit the side that's auto-resolved** for boundary types
  (Income, Expense, Support, Charges, Refund, Loan Received/Repaid/Given/Collected) — the engine
  fills it with the system "External" account. For a plain transfer between two of the user's
  own real accounts, **both** must be provided.
- `loan_id`: **required** for Loan Repaid / Loan Collected (must reference an existing,
  incomplete loan of the matching type). **Must be omitted** for Loan Received / Loan Given
  (a new `Loan` row is created automatically).
- `due_date`: optional, only meaningful when opening a new loan (Loan Received / Loan Given).
- `contact_id`: mandatory for all 4 loan transaction types; optional otherwise.

### POST /api/transactions/filter — request body
```json
{
  "date_from": "2026-08-01",
  "date_to": "2026-08-31",
  "transaction_type_id": null,
  "category_id": null,
  "account_id": null,
  "payment_method_id": null,
  "priority_id": null,
  "contact_id": null,
  "page": 1,
  "page_size": 20
}
```

---

## 11. Loans — `app/api/v1/endpoints/loans.py`

Loans are **read-only via this module** — they're created and updated exclusively as a
side-effect of Transactions (see section 10).

| Method | Path | Notes |
|---|---|---|
| GET | `/api/loans` | All loans for the user. |
| GET | `/api/loans/active?type=received\|given` | Feeds the Loan Repaid / Loan Collected dropdown. |
| GET | `/api/loans/contact/{contact_id}` | All loans with one contact. |
| GET | `/api/loans/{id}` | Single loan. |

---

## 12. Budgets — `app/api/v1/endpoints/budgets.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/budgets?active_only=` | — | |
| GET | `/api/budgets/current` | — | Budgets whose date range covers today. |
| POST | `/api/budgets` | `{category_id, period, amount, start_date, end_date?}` | `period` = `weekly`\|`monthly`\|`yearly`. `spent_amount` starts at 0 and is auto-incremented by every matching Expense transaction. |
| PUT | `/api/budgets/{id}` | `{amount?, end_date?, is_active?}` | |
| DELETE | `/api/budgets/{id}` | — | Soft delete. |

---

## 13. Goals — `app/api/v1/endpoints/goals.py`

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/goals` | — | |
| GET | `/api/goals/progress` | — | `[{id, name, progress_percent, days_remaining, is_completed}]` |
| POST | `/api/goals` | `{name, target_amount, start_date, target_date?, priority_id?}` | |
| PUT | `/api/goals/{id}` | partial | |
| POST | `/api/goals/{id}/add-savings` | `{amount}` | Manual contribution; auto-completes when `saved_amount >= target_amount`. |
| DELETE | `/api/goals/{id}` | — | Hard delete (Goals have no `is_active` flag in the spec). |

---

## 14. Analytics / Dashboard — `app/api/v1/endpoints/analytics.py`

All figures are computed live via aggregate SQL — no cached summary table.

| Method | Path | Query params | Returns |
|---|---|---|---|
| GET | `/api/analytics/summary` | `range=today\|week\|month\|year\|custom`, `date_from`, `date_to` | Total inflow/outflow, net savings, active loans, budgets over limit, active goals. |
| GET | `/api/analytics/transactions` | same as above | Daily inflow/outflow trend + category-wise breakdown (for charts). |
| GET | `/api/analytics/loans` | — | Pending received/given totals, breakdown by contact, full loan list. |
| GET | `/api/analytics/budgets` | — | Budget vs actual per category, `percent_used`. |
| GET | `/api/analytics/goals` | — | Progress %, days remaining per goal. |
| GET | `/api/analytics/report` | `date_from`, `date_to` (required) | Combined payload of all the above for a custom range — export-ready. |

---

## 15. Error format

All errors follow FastAPI's default shape:
```json
{ "detail": "Human-readable message" }
```
Common status codes used throughout: `400` (business rule violation), `401` (auth), `403`
(forbidden — e.g. editing a system-default row), `404` (not found), `409` (duplicate
email/username), `423` (account locked), `429` (rate limited).

---

## 16. Versioning note

Everything currently lives under `/api/...` (no `/v1/` in the URL, though the code is already
organized under `app/api/v1/` internally). If you introduce breaking changes later, create
`app/api/v2/endpoints/`, mount it at `/api/v2` in `app/main.py`, and keep `/api` (v1) running
in parallel for existing clients.

---

## Registration/Profile update (October 2026)

`POST /api/auth/register` now requires `username`, `first_name`, `last_name`, `email`, and `password`; `phone_number` is optional. Existing JSON requests are still accepted. For an optional profile image, send the same fields as `multipart/form-data` plus a `profile_image` file (JPEG/PNG/WebP).

Usernames must start with a letter and contain only letters, numbers, and underscores. New usernames are normalized to lowercase and checked case-insensitively for conflicts.

A successful registration sends a 24-hour email-verification link. Login is rejected with HTTP 403 until verification succeeds. Existing `POST /api/auth/verify-email` remains available, and browser links can use `GET /api/auth/verify-email?token=...`. `POST /api/auth/resend-verification` accepts `{ "email": "..." }`.

Profile data returned by `UserOut` now includes `first_name`, `last_name`, `phone_number`, and `profile_image_url`. Authenticated users can upload/replace an image with `POST /api/users/me/profile-image` (multipart field `profile_image`) or remove it with `DELETE /api/users/me/profile-image`.

New registrations automatically receive the requested default transaction categories and their links to system transaction types.
