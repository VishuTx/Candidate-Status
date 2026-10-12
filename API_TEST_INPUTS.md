# API Test Inputs & Query Reference Guide

> **Purpose**: This guide provides exact, testable values, query parameters, path variables, and JSON payloads for every route in the **Employee Attendance & Analytics API**. It also details step-by-step how to add **new values and records** beyond the existing sample dataset.

---

## Table of Contents
1. [Baseline Dataset Summary (What Already Exists)](#1-baseline-dataset-summary-what-already-exists)
2. [Route-by-Route Input Values & Queries](#2-route-by-route-input-values--queries)
   - [A. System Health](#a-system-health)
   - [B. Employee Management](#b-employee-management)
   - [C. Attendance Clock In & Out](#c-attendance-clock-in--out)
   - [D. Attendance Regularization & Audit](#d-attendance-regularization--audit)
   - [E. Monthly Employee Analytics](#e-monthly-employee-analytics)
   - [F. Department Monthly Summary](#f-department-monthly-summary)
   - [G. Late Arrivals Leaderboard](#g-late-arrivals-leaderboard)
   - [H. Department Daily Trend](#h-department-daily-trend)
   - [I. MongoDB Query Explain Plans](#i-mongodb-query-explain-plans)
3. [How to Add New Values Apart from Existing Ones](#3-how-to-add-new-values-apart-from-existing-ones)
   - [Method 1: Adding via REST API Endpoints (Recommended)](#method-1-adding-via-rest-api-endpoints-recommended)
   - [Method 2: Adding via Sample JSON Seed Files](#method-2-adding-via-sample-json-seed-files)
   - [Method 3: Adding via Direct PyMongo Script](#method-3-adding-via-direct-pymongo-script)
4. [Timestamp Generator & IST Reference Lookup](#4-timestamp-generator--ist-reference-lookup)
5. [Validation Constraints & Error Reference Checklist](#5-validation-constraints--error-reference-checklist)

---

## 1. Baseline Dataset Summary (What Already Exists)

When you initialize the database with `python sample_seed.py`, the following baseline records are loaded:

### Existing Employees (`db.employees`)

| `emp_code` | Name | Department | Shift Start | Shift End | Shift Type | Joined On | Notes |
|:---|:---|:---|:---|:---|:---|:---|:---|
| **`EMP0001`** | Asha Rao | Engineering | `09:30` | `18:30` | Day | `2026-01-05` | Full-month employee; on-time punch history |
| **`EMP0002`** | Vikram Shah | Engineering | `09:30` | `18:30` | Day | `2026-07-13` | Mid-month joiner (15 working days in July) |
| **`EMP0003`** | Neha Iyer | Sales | `09:30` | `18:30` | Day | `2026-02-01` | Frequent late arrivals; Rank 1 on leaderboard |
| **`EMP0004`** | Rohit Das | Sales | `09:30` | `18:30` | Day | `2026-02-01` | Has WFH (half-day) and ABSENT records |
| **`EMP0005`** | Meera Nair | Support | `22:00` | `06:00` | Overnight | `2026-03-10` | Overnight shift spanning midnight |
| **`EMP0006`** | Karan Bedi | Support | `09:30` | `18:30` | Day | `2026-04-01` | Open punch-in record (punch_out is null) |

### Existing Attendance Logs (`db.attendance_logs`)

| `emp_code` | Date | Status | Punch In (IST) | Punch Out (IST) | Work Hours | Late Min | Overtime | Notes |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| `EMP0001` | `2026-07-06` | `PRESENT` | 09:28 | 18:35 | 9.12 | 0 | 0 | Normal full day inside grace |
| `EMP0003` | `2026-07-06` | `PRESENT` | 10:05 | 19:10 | 9.08 | 35 | 40 | Late by 35 min (>10m grace), 40m overtime |
| `EMP0006` | `2026-07-07` | `PRESENT` | 09:35 | *null* | *null* | 0 | 0 | **Open record** ready for testing punch-out |
| `EMP0004` | `2026-07-07` | `WFH` | 09:30 | 13:00 | 3.50 | 0 | 0 | Half-day (< 4.5 hrs); counts as 0.5 present day |
| `EMP0004` | `2026-07-08` | `ABSENT` | *null* | *null* | *null* | 0 | 0 | Absent record; punch times null |
| `EMP0003` | `2026-07-08` | `LEAVE` | *null* | *null* | *null* | 0 | 0 | Approved leave; punch times null |
| `EMP0005` | `2026-07-06` | `PRESENT` | 21:55 | 06:40 (+1d)| 8.75 | 0 | 40 | Overnight shift; out on July 7 with 40m overtime |
| `EMP0001` | `2026-07-07` | `PRESENT` | 09:28 | 18:30 | 9.03 | 0 | 0 | Regularized record with historical audit trail |
| `EMP0002` | `2026-07-14` | `PRESENT` | 09:32 | 18:31 | 8.98 | 0 | 0 | Mid-month joiner attendance record |

---

## 2. Route-by-Route Input Values & Queries

Base URL: `http://localhost:8000`  
Interactive Swagger UI: `http://localhost:8000/docs`

---

### A. System Health

#### `GET /health`
* **Purpose**: Tests database connectivity via `admin.command('ping')`.
* **Inputs**: None.
* **Curl**:
  ```bash
  curl -X GET "http://localhost:8000/health"
  ```
* **Expected Response (200)**:
  ```json
  {"status": "ok"}
  ```

---

### B. Employee Management

#### `GET /employees`
* **Purpose**: Retrieves paginated list of employees with optional department filtering.
* **Query Parameters**:
  - `department` (optional string): `Engineering` | `Sales` | `Support`
  - `page` (optional integer, >= 1, default: `1`)
  - `page_size` (optional integer, 1–100, default: `20`)

**Test Values & Variations:**

| Test Case | Query Parameters / URL | Expected Result |
|:---|:---|:---|
| **All Employees** | `/employees` | Returns all 6 baseline employees |
| **Filter Engineering** | `/employees?department=Engineering` | Returns `EMP0001` and `EMP0002` |
| **Filter Sales** | `/employees?department=Sales` | Returns `EMP0003` and `EMP0004` |
| **Filter Support** | `/employees?department=Support` | Returns `EMP0005` and `EMP0006` |
| **Pagination Test** | `/employees?page=1&page_size=2` | Returns first 2 employees with `total: 6` |
| **Non-existent Dept**| `/employees?department=Finance` | Returns `items: []`, `total: 0` |

* **Curl Example**:
  ```bash
  curl -X GET "http://localhost:8000/employees?department=Engineering&page=1&page_size=10"
  ```

---

#### `POST /employees`
* **Purpose**: Registers a new employee.
* **Request Body Schema**:
  - `emp_code`: String matching regex `^EMP\d{4,6}$` (e.g. `EMP0007`)
  - `name`: String (1–100 characters)
  - `email`: Valid email format `^[^@\s]+@[^@\s]+\.[^@\s]+$`
  - `department`: String (1–50 characters)
  - `shift_start`: Time string `HH:MM` (e.g. `09:30`)
  - `shift_end`: Time string `HH:MM` (must differ from `shift_start`)
  - `joined_on`: Date string `YYYY-MM-DD`

**Test Values & Payloads:**

* **Payload 1: Standard Day-Shift Employee**
  ```json
  {
    "emp_code": "EMP0007",
    "name": "Dev Sharma",
    "email": "dev.sharma@example.com",
    "department": "Engineering",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-07-01"
  }
  ```
  *Curl*:
  ```bash
  curl -X POST "http://localhost:8000/employees" \
       -H "Content-Type: application/json" \
       -d '{"emp_code":"EMP0007","name":"Dev Sharma","email":"dev.sharma@example.com","department":"Engineering","shift_start":"09:30","shift_end":"18:30","joined_on":"2026-07-01"}'
  ```

* **Payload 2: Overnight-Shift Employee**
  ```json
  {
    "emp_code": "EMP0008",
    "name": "Pooja Varma",
    "email": "pooja.varma@example.com",
    "department": "Support",
    "shift_start": "21:00",
    "shift_end": "05:00",
    "joined_on": "2026-07-01"
  }
  ```

* **Payload 3: Duplicate Employee Test (Expects 409 Conflict)**
  * Resend `EMP0001` &rarr; Returns: `{"detail": "emp_code already exists"}`

---

### C. Attendance Clock In & Out

#### `POST /attendance/punch-in`
* **Purpose**: Records a clock-in instant, assigns IST calendar date, and computes late minutes.
* **Request Body Schema**:
  - `emp_code` (required string): Valid employee code
  - `punched_at` (optional integer epoch ms): 13 digits between `100000000000` and `4102444800000`. Omit for current system time.
  - `status` (optional string): `"PRESENT"` (default) | `"WFH"` | `"ON_DUTY"`

**Test Values & Payloads:**

* **Scenario 1: On-Time Day Shift (`EMP0001`)**
  * Date/Time: `2026-07-08 09:30:00 IST` &rarr; Epoch ms: `1783483200000`
  * Expected: `date: "2026-07-08"`, `late_minutes: 0`
  ```json
  {
    "emp_code": "EMP0001",
    "punched_at": 1783483200000,
    "status": "PRESENT"
  }
  ```
  *Curl*:
  ```bash
  curl -X POST "http://localhost:8000/attendance/punch-in" \
       -H "Content-Type: application/json" \
       -d '{"emp_code":"EMP0001","punched_at":1783483200000,"status":"PRESENT"}'
  ```

* **Scenario 2: Grace Period Boundary (`EMP0002`)**
  * Date/Time: `2026-07-08 09:40:00 IST` (exactly 10 min grace) &rarr; Epoch ms: `1783483800000`
  * Expected: `late_minutes: 0` (boundary is not late)
  ```json
  {
    "emp_code": "EMP0002",
    "punched_at": 1783483800000,
    "status": "PRESENT"
  }
  ```

* **Scenario 3: Late Arrival Exceeding Grace (`EMP0003`)**
  * Date/Time: `2026-07-08 10:10:00 IST` (40 min after 09:30) &rarr; Epoch ms: `1783485600000`
  * Expected: `late_minutes: 40` (calculated from shift start, not grace end)
  ```json
  {
    "emp_code": "EMP0003",
    "punched_at": 1783485600000,
    "status": "PRESENT"
  }
  ```

* **Scenario 4: Overnight Shift Before Shift End (`EMP0005`)**
  * Shift: `22:00` to `06:00`. Punch at `2026-07-08 02:00:00 IST` &rarr; Epoch ms: `1783456200000`
  * Expected: Assigned to previous calendar date `date: "2026-07-07"`
  ```json
  {
    "emp_code": "EMP0005",
    "punched_at": 1783456200000,
    "status": "PRESENT"
  }
  ```

* **Scenario 5: Duplicate Punch-In Test (Expects 409 Conflict)**
  * Re-punch `EMP0001` on `2026-07-06` (already in DB) &rarr; `{"detail": "already punched in for this date"}`

---

#### `POST /attendance/punch-out`
* **Purpose**: Atomically closes the most recent open punch-in record, calculating `work_hours`, `overtime_minutes`, and `half_day`.
* **Request Body Schema**:
  - `emp_code` (required string): Valid employee code with an open record
  - `punched_at` (optional integer epoch ms): Omit for current system time

**Test Values & Payloads:**

* **Scenario 1: Close Baseline Open Record (`EMP0006`)**
  * `EMP0006` has an open punch-in on `2026-07-07 09:35:00 IST` (`punch_out: null`).
  * Punch out at `2026-07-07 19:30:00 IST` &rarr; Epoch ms: `1783346400000`
  * Expected: `work_hours: 9.92`, `overtime_minutes: 60` (19:30 - 18:30 shift end), `half_day: false`
  ```json
  {
    "emp_code": "EMP0006",
    "punched_at": 1783346400000
  }
  ```
  *Curl*:
  ```bash
  curl -X POST "http://localhost:8000/attendance/punch-out" \
       -H "Content-Type: application/json" \
       -d '{"emp_code":"EMP0006","punched_at":1783346400000}'
  ```

* **Scenario 2: Already Punched-Out Record (Expects 409 Conflict)**
  * Try punching out `EMP0001` on July 6 (already closed) &rarr; `{"detail": "Record already punched out"}`

* **Scenario 3: Non-existent Punch-In (Expects 404 Not Found)**
  * Punch out for an employee who has not punched in &rarr; `{"detail": "No punch-in found for this employee at or before punched_at"}`

---

#### `GET /attendance`
* **Purpose**: Retrieves paginated attendance records sorted by `date DESC, emp_code ASC`.
* **Query Parameters**:
  - `emp_code` (optional string): e.g. `EMP0001`
  - `date_from` (optional string `YYYY-MM-DD`): e.g. `2026-07-01`
  - `date_to` (optional string `YYYY-MM-DD`): e.g. `2026-07-31`
  - `status` (optional string): `PRESENT` | `WFH` | `ON_DUTY` | `ABSENT` | `LEAVE`
  - `page` (optional int, default: `1`)
  - `page_size` (optional int, default: `20`)

**Test Values & Filter Combinations:**

| Test Objective | Query String / URL | Expected Records |
|:---|:---|:---|
| **Full Log** | `/attendance` | All attendance documents in DB |
| **Filter by Employee** | `/attendance?emp_code=EMP0001` | Returns logs for `EMP0001` (July 6, July 7) |
| **Date Range Filter** | `/attendance?date_from=2026-07-06&date_to=2026-07-07` | Records on July 6 and July 7 |
| **Status: WFH** | `/attendance?status=WFH` | Returns `EMP0004` on `2026-07-07` |
| **Status: ABSENT** | `/attendance?status=ABSENT` | Returns `EMP0004` on `2026-07-08` |
| **Status: LEAVE** | `/attendance?status=LEAVE` | Returns `EMP0003` on `2026-07-08` |
| **Combined Query** | `/attendance?emp_code=EMP0004&status=ABSENT` | Exact match for Rohit Das absent record |
| **Validation Error** | `/attendance?date_from=2026-07-10&date_to=2026-07-05` | `422 Unprocessable Entity` (date_from > date_to) |

* **Curl Example**:
  ```bash
  curl -X GET "http://localhost:8000/attendance?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-15"
  ```

---

### D. Attendance Regularization & Audit

#### `PATCH /attendance/{emp_code}/{date}`
* **Purpose**: HR manual correction with an append-only audit trail in the `history` array.
* **Path Parameters**:
  - `emp_code`: e.g. `EMP0001`, `EMP0003`, `EMP0004`
  - `date`: `YYYY-MM-DD` (e.g. `2026-07-06`, `2026-07-07`, `2026-07-08`)
* **Request Body Schema**:
  - `status` (optional string): `PRESENT` | `WFH` | `ON_DUTY` | `ABSENT` | `LEAVE`
  - `punch_in` (optional epoch ms)
  - `punch_out` (optional epoch ms)
  - `reason` (required string, 5–200 characters)
  - `regularized_by` (required string, 1–50 characters)

**Test Values & Payloads:**

* **Scenario 1: Update Status to WFH (Preserving Timestamps)**
  * Target: `EMP0001` on `2026-07-06`
  ```json
  {
    "status": "WFH",
    "reason": "Approved work from home retroactively",
    "regularized_by": "HR Admin"
  }
  ```
  *Curl*:
  ```bash
  curl -X PATCH "http://localhost:8000/attendance/EMP0001/2026-07-06" \
       -H "Content-Type: application/json" \
       -d '{"status":"WFH","reason":"Approved work from home retroactively","regularized_by":"HR Admin"}'
  ```

* **Scenario 2: Correct Punch Times & Remove Late Penalty**
  * Target: `EMP0003` on `2026-07-06` (originally punched in at 10:05 with 35 late minutes).
  * New Punch In: `09:28:00 IST` &rarr; `1783310280000`
  * New Punch Out: `18:35:00 IST` &rarr; `1783343100000`
  * Expected: `late_minutes` updates from 35 to 0; history entry records the change.
  ```json
  {
    "punch_in": 1783310280000,
    "punch_out": 1783343100000,
    "reason": "Biometric terminal failure at reception",
    "regularized_by": "Security Supervisor"
  }
  ```

* **Scenario 3: Mark Absent (Punch Times Automatically Cleared)**
  * Target: `EMP0001` on `2026-07-06`
  * Rule: Do NOT supply `punch_in` or `punch_out` when setting `ABSENT` or `LEAVE`.
  ```json
  {
    "status": "ABSENT",
    "reason": "Unexcused absence flagged by manager",
    "regularized_by": "Dept Head"
  }
  ```

* **Scenario 4: Invalid Request - Punch Times with Absent (Expects 422)**
  * Sending `{"status": "ABSENT", "punch_in": 1783310280000, ...}` &rarr; `422 Unprocessable Entity`

* **Scenario 5: Invalid Request - No Fields Changed (Expects 422)**
  * Sending a patch that modifies no stored values &rarr; `{"detail": "Request changes nothing"}`

---

### E. Monthly Employee Analytics

#### `GET /analytics/employees/{emp_code}/monthly`
* **Purpose**: Calculates working days (Mon–Fri pro-rated from `joined_on`), present days (half-day = 0.5), leave days, late count, total overtime, and attendance percentage.
* **Path Parameter**: `emp_code` (e.g. `EMP0001`)
* **Query Parameter**: `month` (required `YYYY-MM`, e.g. `2026-07`)

**Test Values & Scenarios:**

| Employee | Month | Key Analytics Tested | Expected Metrics |
|:---|:---|:---|:---|
| **`EMP0001`** | `2026-07` | Full-month employee (joined Jan 5) | `working_days: 23`, `present_days: 2.0` |
| **`EMP0002`** | `2026-07` | Mid-month joiner (joined July 13) | `working_days: 15` (July 13 to July 31 Mon-Fri) |
| **`EMP0003`** | `2026-07` | Late arrivals + Leave | `late_count: 1`, `total_late_minutes: 35`, `leave_days: 1` |
| **`EMP0004`** | `2026-07` | Half-day WFH (3.5h) + Absent | `present_days: 0.5`, `attendance_pct: 2.17%` |
| **`EMP0001`** | `2026-06` | Month with Zero Logs | `present_days: 0.0`, `attendance_pct: 0.0%` (no division by zero) |
| **`EMP9999`** | `2026-07` | Non-existent employee | `404 Not Found` |

* **Curl Example**:
  ```bash
  curl -X GET "http://localhost:8000/analytics/employees/EMP0002/monthly?month=2026-07"
  ```
* **Sample Response**:
  ```json
  {
    "emp_code": "EMP0002",
    "month": "2026-07",
    "working_days": 15,
    "present_days": 1.0,
    "leave_days": 0,
    "late_count": 0,
    "total_late_minutes": 0,
    "total_overtime_minutes": 0,
    "attendance_pct": 6.67
  }
  ```

---

### F. Department Monthly Summary

#### `GET /analytics/departments/summary`
* **Purpose**: Aggregates headcount, presence days, average work hours, late counts, and leave counts across departments using native MongoDB `$lookup`.
* **Query Parameters**:
  - `month` (required `YYYY-MM`, e.g. `2026-07`)
  - `department` (optional string): `Engineering` | `Sales` | `Support`

**Test Values & Scenarios:**

| Test Case | Query Parameters / URL | Expected Behavior |
|:---|:---|:---|
| **All Departments** | `?month=2026-07` | Returns summary rows for Engineering, Sales, and Support |
| **Engineering Only** | `?month=2026-07&department=Engineering` | Headcount = 2 (`EMP0001`, `EMP0002`) |
| **Sales Only** | `?month=2026-07&department=Sales` | Headcount = 2, includes half-day and leave stats |
| **Support Only** | `?month=2026-07&department=Support` | Headcount = 2, includes overnight shift stats |
| **Month with No Logs**| `?month=2026-05` | Headcount preserved; `present_days: 0.0`, `avg_work_hours: null` |

* **Curl Example**:
  ```bash
  curl -X GET "http://localhost:8000/analytics/departments/summary?month=2026-07&department=Engineering"
  ```

---

### G. Late Arrivals Leaderboard

#### `GET /analytics/leaderboard/late`
* **Purpose**: Ranks employees by total late minutes using MongoDB `$setWindowFields` with `$rank` and deterministic tie-sorting by `emp_code ASC`.
* **Query Parameters**:
  - `month` (required `YYYY-MM`, e.g. `2026-07`)
  - `limit` (optional integer, 1–50, default: `10`)
  - `department` (optional string): `Sales`, `Engineering`, `Support`

**Test Values & Scenarios:**

| Test Case | Query Parameters | Expected Leaderboard |
|:---|:---|:---|
| **Company-Wide Top 5** | `?month=2026-07&limit=5` | Rank 1: `EMP0003` (Neha Iyer, Sales, 35 late minutes) |
| **Department Filtered** | `?month=2026-07&department=Sales&limit=10` | Rank 1: `EMP0003` |
| **No Late Arrivals** | `?month=2026-07&department=Engineering` | Returns `items: []` |

* **Curl Example**:
  ```bash
  curl -X GET "http://localhost:8000/analytics/leaderboard/late?month=2026-07&limit=5"
  ```

---

### H. Department Daily Trend

#### `GET /analytics/departments/{department}/trend`
* **Purpose**: Produces calendar-continuous daily attendance metrics using `$densify` for gap filling and `$setWindowFields` for 7-day rolling moving averages.
* **Path Parameter**: `department` (`Engineering`, `Sales`, `Support`)
* **Query Parameters**:
  - `from` (required `YYYY-MM-DD`, e.g. `2026-07-01`)
  - `to` (required `YYYY-MM-DD`, e.g. `2026-07-14`)

**Test Values & Scenarios:**

| Department | Date Range (`from` & `to`) | What is Tested |
|:---|:---|:---|
| **`Engineering`** | `from=2026-07-01&to=2026-07-14` | 14 continuous days densified; headcount transitions from 1 to 2 on July 13 |
| **`Sales`** | `from=2026-07-05&to=2026-07-10` | Half-day present count (0.5 on July 7); 7-day moving average computation |
| **`Support`** | `from=2026-07-01&to=2026-07-07` | Overnight shift trend gap-filling |
| **`NonExistent`**| `from=2026-07-01&to=2026-07-07` | Returns `404 Not Found` ("Department not found") |
| **Reversed Dates**| `from=2026-07-15&to=2026-07-01` | Returns `422 Unprocessable Entity` ("from must not be after to") |

* **Curl Example**:
  ```bash
  curl -X GET "http://localhost:8000/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-14"
  ```

---

### I. MongoDB Query Explain Plans

#### `GET /admin/explain/{endpoint}`
* **Purpose**: Inspects native MongoDB query plans (`executionStats`) to verify index utilization (`IXSCAN`) and absence of collection scans (`COLLSCAN`).
* **Path Parameter**: One of:
  - `attendance_list`
  - `employee_monthly`
  - `department_summary`
  - `late_leaderboard`
  - `department_trend`

**Test Values & Parameters:**

```bash
# 1. Explain attendance list query (verifies compound index [date, emp_code])
curl -X GET "http://localhost:8000/admin/explain/attendance_list?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-31"

# 2. Explain employee monthly aggregation
curl -X GET "http://localhost:8000/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07"

# 3. Explain department summary pipeline (verifies [department, joined_on] index)
curl -X GET "http://localhost:8000/admin/explain/department_summary?month=2026-07&department=Engineering"

# 4. Explain late leaderboard pipeline
curl -X GET "http://localhost:8000/admin/explain/late_leaderboard?month=2026-07&limit=5"

# 5. Explain department trend pipeline
curl -X GET "http://localhost:8000/admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-14"
```

---

## 3. How to Add New Values Apart from Existing Ones

There are **three recommended methods** for adding new data into the system. Choose the method that best matches your workflow:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Methods to Add New Values                            │
├──────────────────────────┬─────────────────────────┬───────────────────┤
│ Method 1: REST API       │ Method 2: Seed JSON     │ Method 3: PyMongo │
│ (Best for app testing    │ (Best for automated     │ (Best for custom  │
│  and validation rules)   │  resets and datasets)   │  batch workloads) │
└──────────────────────────┴─────────────────────────┴───────────────────┘
```

---

### Method 1: Adding via REST API Endpoints (Recommended)

This is the standard production path. The API validates constraints, computes derived fields, and prevents data anomalies automatically.

#### Step 1: Create a New Employee
Run a `POST` request to `/employees`. Ensure:
- `emp_code`: Not already in DB; matches `^EMP\d{4,6}$` (e.g. `EMP0007`, `EMP0008`, `EMP0009`).
- `department`: Can be an existing department or a completely new one (e.g. `Marketing`, `DevOps`, `Finance`).
- `shift_start` & `shift_end`: Format `HH:MM` in IST.

```bash
curl -X POST "http://localhost:8000/employees" \
  -H "Content-Type: application/json" \
  -d '{
    "emp_code": "EMP0007",
    "name": "Ananya Sen",
    "email": "ananya.sen@example.com",
    "department": "Engineering",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-07-15"
  }'
```

#### Step 2: Record a Punch-In for the New Employee
Clock the employee in using `POST /attendance/punch-in`:
```bash
curl -X POST "http://localhost:8000/attendance/punch-in" \
  -H "Content-Type: application/json" \
  -d '{
    "emp_code": "EMP0007",
    "punched_at": 1784088000000,
    "status": "PRESENT"
  }'
```
*(Here `1784088000000` corresponds to `2026-07-15 09:30:00 IST`)*.

#### Step 3: Record a Punch-Out
Clock the employee out using `POST /attendance/punch-out`:
```bash
curl -X POST "http://localhost:8000/attendance/punch-out" \
  -H "Content-Type: application/json" \
  -d '{
    "emp_code": "EMP0007",
    "punched_at": 1784120400000
  }'
```
*(Here `1784120400000` corresponds to `2026-07-15 18:30:00 IST` &rarr; 9.00 work hours, 0 late, 0 overtime)*.

#### Step 4: Add Special Records (Leave or Absent) via Regularization
If you need an `ABSENT` or `LEAVE` record:
1. Punch in first to create the date entry.
2. Call `PATCH /attendance/{emp_code}/{date}` with `"status": "LEAVE"` or `"status": "ABSENT"`. The punch times and work hours will automatically be cleared:
```bash
curl -X PATCH "http://localhost:8000/attendance/EMP0007/2026-07-15" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "LEAVE",
    "reason": "Personal leave approved retroactively",
    "regularized_by": "HR Manager"
  }'
```

---

### Method 2: Adding via Sample JSON Seed Files

If you want new values to persist across database resets (`python sample_seed.py`), add them directly to the files in `candidate_kit/sample_data/`.

#### 1. Adding to `sample_data/employees.json`
Append your new employee objects into the JSON array. Remember to format `created_at` using MongoDB Extended JSON format (`{"$date": "ISO_UTC_STRING"}`):

```json
[
  ...
  {
    "emp_code": "EMP0007",
    "name": "Ananya Sen",
    "email": "ananya.sen@example.com",
    "department": "Engineering",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-07-15",
    "created_at": {
      "$date": "2026-07-15T04:00:00Z"
    }
  }
]
```

#### 2. Adding to `sample_data/attendance_logs.json`
Append attendance records. **Critical rule for seeded data**: Stored derived values (`work_hours`, `late_minutes`, `overtime_minutes`, `half_day`) are authoritative in MongoDB Extended JSON.

##### Template A: Standard Completed Day
```json
{
  "emp_code": "EMP0007",
  "date": "2026-07-15",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-15T04:00:00Z"
  },
  "punch_out": {
    "$date": "2026-07-15T13:00:00Z"
  },
  "work_hours": 9.0,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

##### Template B: Approved Leave Record
```json
{
  "emp_code": "EMP0007",
  "date": "2026-07-16",
  "status": "LEAVE",
  "punch_in": null,
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

##### Template C: Overnight Shift Record (22:00–06:00)
```json
{
  "emp_code": "EMP0005",
  "date": "2026-07-15",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-15T16:25:00Z"
  },
  "punch_out": {
    "$date": "2026-07-16T01:15:00Z"
  },
  "work_hours": 8.83,
  "late_minutes": 0,
  "overtime_minutes": 45,
  "half_day": false,
  "history": []
}
```

#### 3. Reload the Database
Execute the seed script to wipe and reload:
```bash
python sample_seed.py
```
*Output:*
```text
loaded  7 documents into 'attendance_db.employees'
loaded 12 documents into 'attendance_db.attendance_logs'
```

---

### Method 3: Adding via Direct PyMongo Script

If you are running automated test suites or inserting arbitrary batches of data dynamically, you can run a Python script directly against MongoDB.

Save and run the following script:

```python
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
db_name = os.getenv("MONGO_DB", "attendance_db")
client = MongoClient(mongo_uri, tz_aware=True)
db = client[db_name]

# 1. Insert new employee
new_employee = {
    "emp_code": "EMP0009",
    "name": "Kavita Rao",
    "email": "kavita.rao@example.com",
    "department": "Finance",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-07-01",
    "created_at": datetime.now(timezone.utc),
}
db.employees.update_one(
    {"emp_code": new_employee["emp_code"]},
    {"$set": new_employee},
    upsert=True,
)
print(f"Upserted employee: {new_employee['emp_code']}")

# 2. Insert attendance record
punch_in = datetime(2026, 7, 15, 4, 0, 0, tzinfo=timezone.utc)    # 09:30 IST
punch_out = datetime(2026, 7, 15, 13, 0, 0, tzinfo=timezone.utc)  # 18:30 IST

new_log = {
    "emp_code": "EMP0009",
    "date": "2026-07-15",
    "status": "PRESENT",
    "punch_in": punch_in,
    "punch_out": punch_out,
    "work_hours": 9.0,
    "late_minutes": 0,
    "overtime_minutes": 0,
    "half_day": False,
    "history": [],
}
db.attendance_logs.update_one(
    {"emp_code": new_log["emp_code"], "date": new_log["date"]},
    {"$set": new_log},
    upsert=True,
)
print(f"Upserted attendance record for {new_log['emp_code']} on {new_log['date']}")
```

---

## 4. Timestamp Generator & IST Reference Lookup

The API strictly accepts timestamps in **integer epoch milliseconds (13 digits)**. All timestamps are interpreted in **Indian Standard Time (IST, UTC+05:30)**.

### Python Helper Snippet: Generate Epoch ms for any IST Time

Run this one-liner in Python to get exact millisecond values for any custom test date:

```python
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# Format: (Year, Month, Day, Hour, Minute, Second)
dt = datetime(2026, 7, 15, 9, 30, 0, tzinfo=IST)
epoch_ms = int(dt.timestamp() * 1000)
print(f"Epoch ms: {epoch_ms}")  # 1784088000000
```

### Ready-to-Use Epoch Millisecond Values for July 2026

| Target Date & IST Time | Scenario Description | UTC Equivalent | Epoch Milliseconds (13 digits) |
|:---|:---|:---|:---|
| **2026-07-06 09:28:00 IST** | Baseline Day Shift On-Time Punch-In | 2026-07-06 03:58:00 UTC | `1783310280000` |
| **2026-07-06 10:05:00 IST** | Baseline Day Shift Late Punch-In (35m) | 2026-07-06 04:35:00 UTC | `1783312500000` |
| **2026-07-06 18:35:00 IST** | Baseline Day Shift Punch-Out (5m OT) | 2026-07-06 13:05:00 UTC | `1783343100000` |
| **2026-07-06 19:10:00 IST** | Baseline Day Shift Punch-Out (40m OT) | 2026-07-06 13:40:00 UTC | `1783345200000` |
| **2026-07-06 21:55:00 IST** | Baseline Overnight Shift Punch-In | 2026-07-06 16:25:00 UTC | `1783355100000` |
| **2026-07-07 06:40:00 IST** | Baseline Overnight Shift Punch-Out (40m OT)| 2026-07-07 01:10:00 UTC | `1783386600000` |
| **2026-07-07 19:30:00 IST** | Closing EMP0006 Open Punch-Out | 2026-07-07 14:00:00 UTC | `1783346400000` |
| **2026-07-08 09:30:00 IST** | New Day Shift On-Time Punch-In | 2026-07-08 04:00:00 UTC | `1783483200000` |
| **2026-07-08 09:40:00 IST** | Grace Period Boundary (10m - Not Late)| 2026-07-08 04:10:00 UTC | `1783483800000` |
| **2026-07-08 09:40:01 IST** | 1 Second Past Grace (10m Late Penalty)| 2026-07-08 04:10:01 UTC | `1783483801000` |
| **2026-07-08 10:10:00 IST** | Late Punch-In (40m Late) | 2026-07-08 04:40:00 UTC | `1783485600000` |
| **2026-07-08 13:45:00 IST** | Half-Day Punch-Out (4.25 hrs < 4.5 hrs)| 2026-07-08 08:15:00 UTC | `1783498500000` |
| **2026-07-08 18:30:00 IST** | Normal Shift End Punch-Out | 2026-07-08 13:00:00 UTC | `1783515600000` |
| **2026-07-08 19:15:00 IST** | Overtime Punch-Out (45m OT) | 2026-07-08 13:45:00 UTC | `1783518300000` |
| **2026-07-15 09:30:00 IST** | Mid-Month Custom Test Punch-In | 2026-07-15 04:00:00 UTC | `1784088000000` |
| **2026-07-15 18:30:00 IST** | Mid-Month Custom Test Punch-Out | 2026-07-15 13:00:00 UTC | `1784120400000` |

---

## 5. Validation Constraints & Error Reference Checklist

Avoid unexpected HTTP errors by adhering to the following schema constraints:

### Field Formats & Regex Rules

| Field Name | Type | Validation Pattern / Range | Example Valid Value | Error on Violation |
|:---|:---|:---|:---|:---|
| `emp_code` | String | `^EMP\d{4,6}$` (unique business key) | `"EMP0007"` | `422` (pattern) or `409` (duplicate) |
| `name` | String | 1 to 100 characters | `"Ananya Sen"` | `422 Unprocessable Entity` |
| `email` | String | Standard email regex pattern | `"ananya@example.com"` | `422 Unprocessable Entity` |
| `department`| String | 1 to 50 characters | `"Engineering"` | `422 Unprocessable Entity` |
| `shift_start`| String | `^([01]\d\|2[0-3]):[0-5]\d$` (HH:MM) | `"09:30"` | `422 Unprocessable Entity` |
| `shift_end` | String | `^([01]\d\|2[0-3]):[0-5]\d$` (HH:MM) | `"18:30"` | `422` (must differ from start) |
| `joined_on` | String | `^\d{4}-(0[1-9]\|1[0-2])-(0[1-9]\|[12]\d\|3[01])$` | `"2026-07-01"` | `422 Unprocessable Entity` |
| `date` | String | `^\d{4}-(0[1-9]\|1[0-2])-(0[1-9]\|[12]\d\|3[01])$` | `"2026-07-08"` | `422 Unprocessable Entity` |
| `month` | String | `^\d{4}-(0[1-9]\|1[0-2])$` | `"2026-07"` | `422 Unprocessable Entity` |
| `punched_at`| Integer | `100_000_000_000 <= ms <= 4_102_444_800_000` | `1783483200000` | `422 Unprocessable Entity` |
| `reason` | String | 5 to 200 characters | `"Approved remote work"`| `422 Unprocessable Entity` |
| `regularized_by`| String| 1 to 50 characters | `"HR Lead"` | `422 Unprocessable Entity` |

### Common HTTP Status Codes Checklist

* **`200 OK`**: Successful GET request, Punch-out, or Regularization.
* **`201 Created`**: Successful employee creation or punch-in.
* **`404 Not Found`**:
  - Employee code does not exist.
  - Department not found in department trend route.
  - Attendance record does not exist on that date.
* **`409 Conflict`**:
  - Employee with that `emp_code` already exists.
  - Punch-in already recorded for that employee on that date.
  - Punch-out attempted on a record that has already been closed.
  - Concurrent regularization conflict.
* **`422 Unprocessable Entity`**:
  - Date format or regex mismatch.
  - Supplying punch times when setting status to `ABSENT` or `LEAVE`.
  - Punch-in date does not match the target record date.
  - Punch-out is before punch-in or exceeds 24 hours.
  - Regularization payload results in zero field modifications.
  - `date_from > date_to` or `from > to`.
* **`503 Service Unavailable`**:
  - MongoDB database or Atlas cluster unreachable during `/health`.
