# Employee Attendance & Analytics API — Implementation Guide

> **Quick summary**: A single-file FastAPI + MongoDB API that tracks employee punch-in/out, allows audited manual corrections, and serves analytics via native MongoDB aggregation pipelines.

---

## 1. What the Project Does (Plain English)

Imagine a company attendance system where:

- Employees **punch in** and **punch out** each day (like a time clock).
- Managers can **manually correct** records (forgot to punch out? wrong time? leave taken?) — every correction creates a permanent history trail.
- The system provides **analytics reports**: how many days someone attended, who came in late the most, department-level summaries, and daily attendance trends.

**Stack**: Python 3.11 + FastAPI (web framework) + MongoDB (database) + PyMongo (MongoDB driver).

All code lives in **one file**: `app/main.py`.

---

## 2. Project Architecture (How Data Flows)

```
HTTP Request
    │
    ▼
FastAPI Route (e.g. POST /attendance/punch-in)
    │
    ▼
Pydantic Model  ──── validates input (types, ranges, regex patterns)
    │
    ▼
Business Logic Helpers  ──── apply rules R1–R8 (timezone, rounding, late calc)
    │
    ▼
PyMongo  ──── reads/writes to MongoDB
    │
    ▼
Serializer (doc_to_employee / doc_to_attendance_record)
    │
    ▼
JSON Response (epoch ms for timestamps, no _id fields)
```

### Key Data Representation Rules

| What | In API (HTTP) | In MongoDB | Why |
|:-----|:-------------|:-----------|:----|
| Timestamps (punch_in, punch_out, created_at) | Integer epoch milliseconds | BSON UTC datetime | Avoids timezone ambiguity |
| Calendar dates | String `"2026-07-14"` | String `"2026-07-14"` | No implicit TZ conversion |
| Shift times | String `"09:30"` | String `"09:30"` | Plain HH:MM (IST) |
| Employee ID | `emp_code` (e.g. `"EMP0001"`) | `_id` hidden | Business key, not DB internal key |

---

## 3. Business Rules (R1–R8) — The Core Logic

### R1: Everything is in Indian Standard Time (IST, UTC+05:30)

A punch-in's **attendance date** is determined by its IST clock time.
For **overnight shifts** (e.g. 22:00–06:00, shift_end ≤ shift_start):
- If you punch in at 05:00 IST (which is before shift_end 06:00), the record belongs to **yesterday**.

```python
def determine_attendance_date(punch_in_ist, shift_start, shift_end):
    is_overnight = shift_end <= shift_start
    if is_overnight:
        h_end, m_end = map(int, shift_end.split(":"))
        if punch_in_ist.time() < time(h_end, m_end):
            return (punch_in_ist.date() - timedelta(days=1)).isoformat()
    return punch_in_ist.date().isoformat()
```

All timestamps are **truncated to whole seconds** before any calculation:
```
truncated_ms = (raw_ms // 1000) * 1000
```

### R2: Grace Period & Late Calculation

- Grace period = 10 minutes (600 seconds)
- Late only if punch-in is **strictly more than 600 seconds** after shift_start
- `late_minutes` = **floor(seconds since shift_start / 60)** — NOT since grace period ended!

Example (shift 09:30):
- Punch at 09:40:00 → 600s → **0 late minutes** (exactly at grace period boundary = not late)
- Punch at 09:40:01 → 601s → **10 late minutes** (601 // 60 = 10)
- Punch at 10:15:59 → 2759s → **45 late minutes** (2759 // 60 = 45)

### R3: Overtime Calculation

- Overtime = whole minutes (floor) from `shift_end` to `punch_out`
- **Minimum threshold**: Only counted if ≥ 30 minutes; otherwise returns 0
- Overnight shifts: `shift_end` is on `record_date + 1 day`

### R4 & R8: Half-Up Rounding (NOT Python's Default!)

Python's `round()` uses **Banker's Rounding** (round-half-to-even). The spec requires **half-up rounding**.

```python
from decimal import Decimal, ROUND_HALF_UP

def round_half_up(val, decimals=2):
    d = Decimal(str(val))
    q = Decimal("10") ** -decimals
    return float(d.quantize(q, rounding=ROUND_HALF_UP))
```

Example: `4.125` → `4.13` (half-up) vs `4.12` (Python's default banker's rounding)

### R5: Half-Day Rule

If `work_hours < 4.50` after rounding → `half_day = True`.
Half days count as **0.5 days** in analytics.

### R7: Working Days Calculation

Working days = Mon–Fri calendar days **from `joined_on`** (for mid-month starters).

---

## 4. All Endpoints at a Glance

| Method | Path | What it Does | Key Status Codes |
|:-------|:-----|:-------------|:----------------|
| GET | `/health` | DB connectivity check | 200, 503 |
| POST | `/employees` | Create employee | 201, 409, 422 |
| GET | `/employees` | List with pagination | 200, 422 |
| POST | `/attendance/punch-in` | Record clock-in | 201, 404, 409, 422 |
| POST | `/attendance/punch-out` | Record clock-out + compute stats | 200, 404, 409, 422 |
| GET | `/attendance` | List records with filters | 200, 422 |
| PATCH | `/attendance/{emp_code}/{date}` | Manual correction + audit | 200, 404, 409, 422 |
| GET | `/analytics/employees/{emp_code}/monthly` | Employee monthly stats | 200, 404, 422 |
| GET | `/analytics/departments/summary` | Department rollup | 200, 422 |
| GET | `/analytics/leaderboard/late` | Late arrivals ranking | 200, 422 |
| GET | `/analytics/departments/{dept}/trend` | Daily attendance trend | 200, 404, 422 |
| GET | `/admin/explain/{endpoint}` | MongoDB query plan | 200, 422 |

---

## 5. Phase-by-Phase Implementation

### Phase 1: Foundation (App Startup & Business Logic Helpers)

**What was done:**
1. **MongoDB Indexes** created at startup (via FastAPI lifespan):
   - `employees(emp_code)` — unique, fast lookup
   - `employees(department, joined_on)` — analytics filter
   - `attendance_logs(emp_code, date)` — unique, prevents duplicate records
   - `attendance_logs(date DESC, emp_code ASC)` — pagination sort
   - `attendance_logs(emp_code, punch_in)` — punch-out matching
   - `attendance_logs(date, late_minutes)` — leaderboard filtering
   - `attendance_logs(status, date)` — status filter

2. **Business rule helpers** implemented: `truncate_instant_ms`, `determine_attendance_date`, `compute_late_minutes`, `compute_work_hours`, `compute_overtime`, `compute_half_day`, `round_half_up`

3. **Serializers** that strip `_id` and convert datetimes → epoch ms: `doc_to_employee`, `doc_to_attendance_record`

### Phase 2: Starter Code Bug Fixes

The starter `app/main.py` had **22 defects**. Key fixes:

| Bug | Fix |
|:----|:----|
| `/health` returned 200 even if DB was down | Added `client.admin.command('ping')` + 503 on failure |
| Duplicate employee race condition (TOCTOU) | Unique index + catch `DuplicateKeyError` → 409 |
| Pagination off-by-one (`page * size` vs `(page-1) * size`) | Fixed skip formula |
| `count_documents({})` ignoring filters | Changed to `count_documents(q)` |
| Python's `round()` (banker's rounding) for work_hours | Replaced with `Decimal.quantize(ROUND_HALF_UP)` |
| Loading entire collection in Python memory | Sort/skip/limit delegated to MongoDB cursor |
| `punch_in` returning `id` field | Removed; records identified by `(emp_code, date)` |
| Missing validation: `date_from > date_to` | Added 422 check |
| `created_at` as ISO string instead of epoch ms | Serialized via `datetime_to_epoch_ms()` |

### Phase 3: Punch-Out & Regularization

**Punch-Out** (`POST /attendance/punch-out`):
- Finds the most recent record where `punch_in ≤ punched_at`
- Computes `work_hours`, `overtime_minutes`, `half_day`
- Uses `find_one_and_update` with `"punch_out": None` filter — **atomic**, prevents double punch-out

**Regularization** (`PATCH /attendance/{emp_code}/{date}`):
- Allows correcting status, punch_in, punch_out
- ABSENT/LEAVE: clears all punch times, 422 if punches provided
- Validates punch_in still resolves to same attendance date (R1)
- Validates punch_out within 24h of punch_in
- 422 if nothing actually changed
- Builds `history` entry with **only changed fields** (before/after)
- Uses `find_one_and_update` matching exact history array → prevents concurrent correction data loss (409 on conflict)

### Phase 4: Analytics Aggregation Pipelines

All analytics run **entirely inside MongoDB** — no Python-side aggregation.

**`GET /analytics/employees/{emp_code}/monthly`**:
- Counts working days (Mon–Fri) in Python (simple loop from `joined_on`)
- MongoDB `$group` aggregates: `present_days` (with half_day=0.5), `leave_days`, `late_count`, totals
- `attendance_pct = (present_days / working_days) * 100`, rounded half-up

**`GET /analytics/departments/summary`**:
- Pipeline **roots on `employees`** (not attendance_logs) → employees with zero logs still appear in headcount (R9)
- Uses `$lookup` to join attendance logs
- Calculates `avg_work_hours` as arithmetic mean (not average of per-employee averages)

**`GET /analytics/leaderboard/late`**:
- Groups late minutes per employee
- `$setWindowFields` with `$rank` → standard competition ranking (1, 2, 2, 4)
- `$limit` applied **after** ranking (not before)
- Ties broken by `emp_code ASC`

**`GET /analytics/departments/{dept}/trend`**:
- `$densify` fills calendar gaps (days with no attendance records still appear)
- `$lookup` into employees to compute daily headcount
- `$setWindowFields` with `documents: [-6, "current"]` for 7-day rolling average
- `attendance_rate` rounded to 4 decimals

### Phase 5: Query Explain Endpoint

`GET /admin/explain/{endpoint}`:
- Accepts: `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`
- Runs the **exact same query/pipeline** as the real endpoint using `db.command({"explain": ..., "verbosity": "executionStats"})`
- Returns the MongoDB execution plan to verify indexes are being used (`IXSCAN` not `COLLSCAN`)

---

## 6. How to Run & Test Everything

### Start the Server

```bash
# From candidate_kit/ directory:
# 1. Activate the virtual environment:
.venv\Scripts\activate          # Windows
# source .venv/bin/activate    # Linux/macOS

# 2. Install dependencies:
pip install -r requirements.txt

# 3. Start the API:
uvicorn app.main:app --port 8000

# 4. (Optional) Load sample data:
python sample_seed.py
```

### Interactive Docs
Open **http://localhost:8000/docs** in your browser — full Swagger UI.

### Test Each Endpoint (curl examples)

**Health Check**
```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

**Create Employee**
```bash
curl -X POST http://localhost:8000/employees ^
  -H "Content-Type: application/json" ^
  -d "{\"emp_code\":\"EMP0099\",\"name\":\"Jane Smith\",\"email\":\"jane@example.com\",\"department\":\"Engineering\",\"shift_start\":\"09:30\",\"shift_end\":\"18:30\",\"joined_on\":\"2026-01-01\"}"
# Expected: 201 with created_at as integer epoch ms
```

**Punch In**
```bash
curl -X POST http://localhost:8000/attendance/punch-in ^
  -H "Content-Type: application/json" ^
  -d "{\"emp_code\":\"EMP0099\",\"punched_at\":1783290600000}"
# Expected: 201 with punch-in record, late_minutes computed
```

**Punch Out**
```bash
curl -X POST http://localhost:8000/attendance/punch-out ^
  -H "Content-Type: application/json" ^
  -d "{\"emp_code\":\"EMP0099\",\"punched_at\":1783318200000}"
# Expected: 200 with work_hours, late_minutes, overtime_minutes, half_day computed
```

**Regularize**
```bash
curl -X PATCH http://localhost:8000/attendance/EMP0099/2026-07-14 ^
  -H "Content-Type: application/json" ^
  -d "{\"status\":\"LEAVE\",\"reason\":\"Medical leave approved\",\"regularized_by\":\"HR Manager\"}"
# Expected: 200 with history entry showing what changed
```

**Monthly Analytics**
```bash
curl "http://localhost:8000/analytics/employees/EMP0099/monthly?month=2026-07"
```

**Department Summary**
```bash
curl "http://localhost:8000/analytics/departments/summary?month=2026-07"
```

**Late Leaderboard**
```bash
curl "http://localhost:8000/analytics/leaderboard/late?month=2026-07&limit=5"
```

**Department Trend**
```bash
curl "http://localhost:8000/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07"
```

**Explain Query Plan**
```bash
curl "http://localhost:8000/admin/explain/attendance_list"
# Look for "stage": "IXSCAN" in response (not "COLLSCAN")
```

---

## 7. Common Edge Cases to Test

| Scenario | What to Send | Expected Response |
|:---------|:------------|:-----------------|
| Duplicate employee | POST /employees with same emp_code twice | 409 on second |
| Invalid emp_code format | `"emp_code": "BAD01"` | 422 |
| Same shift start and end | `shift_start == shift_end` | 422 |
| Punch-in for unknown employee | emp_code not in DB | 404 |
| Double punch-in same day | Two punch-in requests for same employee+date | 409 on second |
| Punch-out when none punched in | punch-out for non-existent record | 404 |
| Punch-out already closed record | Second punch-out request | 409 |
| Regularize ABSENT with punch_in | Provide punch_in when setting ABSENT | 422 |
| Regularize with no actual changes | All values same as existing | 422 |
| Date range reversed | `date_from=2026-07-10&date_to=2026-07-01` | 422 |
| Overnight shift date assignment | Punch at 05:00 IST for 22:00–06:00 shift | Date = previous day |
| Seconds-precision timestamp | `punched_at=1783290` (too small) | 422 |

---

## 8. Full Compliance Checklist

| Requirement | Implementation | Status |
|:------------|:--------------|:-------|
| All 12 endpoints from OpenAPI spec | All present in main.py | PASS |
| No `_id` or `id` in responses | Stripped by serializers | PASS |
| `created_at` as epoch ms integer | `datetime_to_epoch_ms()` | PASS |
| `punch_in`/`punch_out` as epoch ms | Converted in serializer | PASS |
| IST timezone for all date calc | `IST = timezone(timedelta(hours=5, minutes=30))` | PASS |
| Instant truncation to seconds | `truncate_instant_ms()` | PASS |
| Overnight shift date assignment | `determine_attendance_date()` | PASS |
| Grace period 10 min (strict >600s) | `compute_late_minutes()` | PASS |
| Overtime >= 30 min threshold | `compute_overtime()` | PASS |
| Half-up rounding | `Decimal.quantize(ROUND_HALF_UP)` | PASS |
| Half-day < 4.50 hours | `compute_half_day()` | PASS |
| Working days Mon-Fri (mid-month) | Calendar loop from joined_on | PASS |
| History only changed fields | Change detection before update | PASS |
| Atomic punch-out (no double) | `find_one_and_update` with `punch_out: None` | PASS |
| Atomic regularization (no lost history) | `find_one_and_update` matching history array | PASS |
| Department headcount includes zero-log employees | Pipeline roots on `employees` collection | PASS |
| MongoDB `$rank` window for leaderboard | `$setWindowFields` with `$rank` | PASS |
| `$densify` for trend calendar gaps | In `build_department_trend_pipeline` | PASS |
| 7-day rolling average | `$setWindowFields` with `documents: [-6, "current"]` | PASS |
| All indexes prevent COLLSCAN | 7 indexes created at startup | PASS |
| Explain endpoint for all 5 queries | `GET /admin/explain/{endpoint}` | PASS |
| REVIEW.md with 22 defects | Fully documented | PASS |
| DECISIONS.md with 5 design choices | Fully documented | PASS |
