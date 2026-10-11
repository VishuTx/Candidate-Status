"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (read from environment, local .env is fallback)
"""
import calendar
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

try:
    import dns.resolver

    _res = dns.resolver.get_default_resolver()
    for _fallback in ["8.8.8.8", "1.1.1.1"]:
        if _fallback not in _res.nameservers:
            _res.nameservers.append(_fallback)
except Exception:
    pass

# Real environment variables take priority over .env
load_dotenv(override=False)

MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH_MIN = 100_000_000_000
EPOCH_MAX = 4_102_444_800_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create required indexes idempotently on startup
    db.employees.create_index([("emp_code", 1)], unique=True)
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.employees.create_index([("joined_on", 1), ("department", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("date", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", 1)])
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)])
    db.attendance_logs.create_index([("status", 1), ("date", 1)])
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Helpers & Business Logic (R1 - R8)
# --------------------------------------------------------------------------- #
def round_half_up(val: float | Decimal, decimals: int = 2) -> float:
    """R8: Round half-up (not banker's round-to-even)."""
    d = Decimal(str(val))
    q = Decimal("10") ** -decimals
    return float(d.quantize(q, rounding=ROUND_HALF_UP))


def truncate_instant_ms(ms: int) -> int:
    """R1: Instants are truncated to whole seconds before any calculation."""
    return (ms // 1000) * 1000


def epoch_ms_to_datetime_utc(ms: int) -> datetime:
    """Convert epoch milliseconds (truncated to whole seconds) to UTC datetime."""
    truncated_ms = truncate_instant_ms(ms)
    return datetime.fromtimestamp(truncated_ms / 1000.0, tz=timezone.utc)


def datetime_to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert datetime (naive or aware) to epoch milliseconds integer."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def determine_attendance_date(punch_in_ist: datetime, shift_start: str, shift_end: str) -> str:
    """
    R1: A record's date is the IST calendar date of its punch_in.
    Overnight shifts (shift_end <= shift_start): a punch-in whose IST time
    is strictly earlier than shift_end belongs to the shift that started the previous day.
    """
    is_overnight = shift_end <= shift_start
    if is_overnight:
        h_end, m_end = map(int, shift_end.split(":"))
        if punch_in_ist.time() < time(h_end, m_end):
            return (punch_in_ist.date() - timedelta(days=1)).isoformat()
    return punch_in_ist.date().isoformat()


def compute_late_minutes(punch_in_ist: datetime, record_date_str: str, shift_start: str) -> int:
    """
    R2: Grace period 10 minutes. Late only if punch-in is strictly more than 10:00 after shift_start;
    then late_minutes = whole minutes (floor) since shift_start, not since grace period ended.
    """
    record_date = datetime.strptime(record_date_str, "%Y-%m-%d").date()
    h_start, m_start = map(int, shift_start.split(":"))
    shift_start_dt = datetime.combine(record_date, time(h_start, m_start), tzinfo=IST)
    elapsed_sec = int((punch_in_ist - shift_start_dt).total_seconds())
    if elapsed_sec > 600:
        return elapsed_sec // 60
    return 0


def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    """
    R4: (punch_out - punch_in) in seconds / 3600, rounded to 2 decimals half-up.
    """
    diff_sec = Decimal(str(int((punch_out_utc - punch_in_utc).total_seconds())))
    hours = diff_sec / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(
    punch_out_ist: datetime, record_date_str: str, shift_start: str, shift_end: str
) -> int:
    """
    R3: Whole minutes (floor) from shift_end to punch_out, counted only if at least 30; otherwise 0.
    Overnight shifts: shift_end falls on record_date + 1 day.
    """
    record_date = datetime.strptime(record_date_str, "%Y-%m-%d").date()
    is_overnight = shift_end <= shift_start
    h_end, m_end = map(int, shift_end.split(":"))
    if is_overnight:
        shift_end_dt = datetime.combine(record_date + timedelta(days=1), time(h_end, m_end), tzinfo=IST)
    else:
        shift_end_dt = datetime.combine(record_date, time(h_end, m_end), tzinfo=IST)

    elapsed_sec = int((punch_out_ist - shift_end_dt).total_seconds())
    if elapsed_sec < 0:
        return 0
    ot_min = elapsed_sec // 60
    return ot_min if ot_min >= 30 else 0


def compute_half_day(work_hours: Optional[float]) -> bool:
    """R5: half_day is true when rounded work_hours < 4.50."""
    if work_hours is None:
        return False
    return work_hours < 4.50


# --------------------------------------------------------------------------- #
# Document Serializers (Conform to OpenAPI schema, omitting _id and internal fields)
# --------------------------------------------------------------------------- #
def doc_to_employee(doc: dict) -> dict:
    created_at = doc.get("created_at")
    if isinstance(created_at, datetime):
        created_at = datetime_to_epoch_ms(created_at)
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": created_at,
    }


def doc_to_attendance_record(doc: dict) -> dict:
    history = []
    for h in doc.get("history", []) or []:
        h_at = h.get("at")
        if isinstance(h_at, datetime):
            h_at = datetime_to_epoch_ms(h_at)
        changes = {}
        for k, v in (h.get("changes") or {}).items():
            c_from = v.get("from")
            c_to = v.get("to")
            if k in ("punch_in", "punch_out"):
                if isinstance(c_from, datetime):
                    c_from = datetime_to_epoch_ms(c_from)
                if isinstance(c_to, datetime):
                    c_to = datetime_to_epoch_ms(c_to)
            changes[k] = {"from": c_from, "to": c_to}
        history.append({
            "at": h_at,
            "by": h.get("by"),
            "reason": h.get("reason"),
            "changes": changes,
        })

    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": datetime_to_epoch_ms(doc.get("punch_in")),
        "punch_out": datetime_to_epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": history,
    }


# --------------------------------------------------------------------------- #
# Pydantic Models & Schemas
# --------------------------------------------------------------------------- #
EpochMillis = Annotated[int, Field(strict=True, ge=EPOCH_MIN, le=EPOCH_MAX)]


class PresenceStatus(str, Enum):
    PRESENT = "PRESENT"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class Status(str, Enum):
    PRESENT = "PRESENT"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"


class EmployeeIn(BaseModel):
    emp_code: Annotated[str, StringConstraints(pattern=r"^EMP\d{4,6}$")]
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    email: Annotated[str, StringConstraints(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    department: Annotated[str, StringConstraints(min_length=1, max_length=50)]
    shift_start: Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")] = "09:30"
    shift_end: Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")] = "18:30"
    joined_on: Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")]

    @model_validator(mode="after")
    def validate_shift_difference(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None
    status: PresenceStatus = PresenceStatus.PRESENT


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None


class RegularizeIn(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[EpochMillis] = None
    punch_out: Optional[EpochMillis] = None
    reason: Annotated[str, StringConstraints(min_length=5, max_length=200)]
    regularized_by: Annotated[str, StringConstraints(min_length=1, max_length=50)]


# --------------------------------------------------------------------------- #
# Endpoints (Part A: Review & Fixes)
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    now_utc = datetime.now(timezone.utc)
    doc = body.model_dump()
    doc["created_at"] = now_utc

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")

    return doc_to_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q: dict[str, Any] = {}
    if department:
        q["department"] = department

    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    cursor = (
        db.employees.find(q)
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )
    items = [doc_to_employee(doc) for doc in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        raw_ms = body.punched_at
    else:
        raw_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    ms = truncate_instant_ms(raw_ms)
    punch_in_utc = epoch_ms_to_datetime_utc(ms)
    punch_in_ist = punch_in_utc.astimezone(IST)

    record_date = determine_attendance_date(punch_in_ist, emp["shift_start"], emp["shift_end"])
    late_min = compute_late_minutes(punch_in_ist, record_date, emp["shift_start"])

    doc = {
        "emp_code": body.emp_code,
        "date": record_date,
        "status": body.status.value,
        "punch_in": punch_in_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_min,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="already punched in for this date")

    return doc_to_attendance_record(doc)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    status: Optional[Status] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")

    q: dict[str, Any] = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        date_q = {}
        if date_from:
            date_q["$gte"] = date_from
        if date_to:
            date_q["$lte"] = date_to
        q["date"] = date_q
    if status:
        q["status"] = status.value

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = (
        db.attendance_logs.find(q)
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )
    items = [doc_to_attendance_record(doc) for doc in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Endpoints (Part B & C: To be implemented in subsequent phases)
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        raw_ms = body.punched_at
    else:
        raw_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    ms = truncate_instant_ms(raw_ms)
    punch_out_utc = epoch_ms_to_datetime_utc(ms)
    punch_out_ist = punch_out_utc.astimezone(IST)

    # Closes the employee's most recent record whose punch_in is at or before punched_at
    # (this must work across midnight for overnight shifts).
    target_record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punch_out_utc}},
        sort=[("punch_in", -1)],
    )

    if not target_record:
        raise HTTPException(
            status_code=404, detail="No punch-in found for this employee at or before punched_at"
        )

    if target_record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Record already punched out")

    # Compute derived fields
    punch_in_utc = target_record["punch_in"]
    if punch_in_utc.tzinfo is None:
        punch_in_utc = punch_in_utc.replace(tzinfo=timezone.utc)

    work_hours = compute_work_hours(punch_in_utc, punch_out_utc)
    overtime_min = compute_overtime(
        punch_out_ist, target_record["date"], emp["shift_start"], emp["shift_end"]
    )
    half_day = compute_half_day(work_hours)

    # Atomic update to prevent race conditions (two simultaneous punch-outs yield one 200 and one 409)
    updated_doc = db.attendance_logs.find_one_and_update(
        {
            "_id": target_record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_utc,
                "work_hours": work_hours,
                "overtime_minutes": overtime_min,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated_doc is None:
        raise HTTPException(status_code=409, detail="Record already punched out")

    return doc_to_attendance_record(updated_doc)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str = Path(...),
    date: str = Path(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    body: RegularizeIn = ...,
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    existing = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not existing:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    # Determine target status
    if body.status is not None:
        target_status = body.status.value
    else:
        target_status = existing["status"]

    # Rules:
    # 1. Setting status ABSENT or LEAVE clears both punch times, so supplying punch_in or punch_out with it is 422
    if target_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="Cannot supply punch_in or punch_out when setting status to ABSENT or LEAVE",
            )
        target_punch_in = None
        target_punch_out = None
        target_work_hours = None
        target_late_minutes = 0
        target_overtime_minutes = 0
        target_half_day = False
    else:
        # 2. A presence status requires a punch_in (supplied or already stored)
        if body.punch_in is not None:
            target_punch_in = epoch_ms_to_datetime_utc(body.punch_in)
        else:
            target_punch_in = existing.get("punch_in")
            if target_punch_in is not None and target_punch_in.tzinfo is None:
                target_punch_in = target_punch_in.replace(tzinfo=timezone.utc)

        if target_punch_in is None:
            raise HTTPException(status_code=422, detail="Presence status requires a punch_in")

        # 3. punch_in must stay on the record's attendance date (R1)
        target_punch_in_ist = target_punch_in.astimezone(IST)
        calc_date = determine_attendance_date(target_punch_in_ist, emp["shift_start"], emp["shift_end"])
        if calc_date != date:
            raise HTTPException(
                status_code=422,
                detail=f"punch_in belongs to attendance date {calc_date}, does not match record date {date}",
            )

        # 4. punch_out handling
        if body.punch_out is not None:
            target_punch_out = epoch_ms_to_datetime_utc(body.punch_out)
        else:
            target_punch_out = existing.get("punch_out")
            if target_punch_out is not None and target_punch_out.tzinfo is None:
                target_punch_out = target_punch_out.replace(tzinfo=timezone.utc)

        if target_punch_out is not None:
            # 5. punch_out must be after punch_in and within 24 hours
            if target_punch_out <= target_punch_in:
                raise HTTPException(status_code=422, detail="punch_out must be strictly after punch_in")
            diff_sec = (target_punch_out - target_punch_in).total_seconds()
            if diff_sec > 86400:
                raise HTTPException(status_code=422, detail="punch_out must be within 24 hours of punch_in")

            target_punch_out_ist = target_punch_out.astimezone(IST)
            target_work_hours = compute_work_hours(target_punch_in, target_punch_out)
            target_overtime_minutes = compute_overtime(
                target_punch_out_ist, date, emp["shift_start"], emp["shift_end"]
            )
            target_half_day = compute_half_day(target_work_hours)
        else:
            target_work_hours = None
            target_overtime_minutes = 0
            target_half_day = False

        target_late_minutes = compute_late_minutes(target_punch_in_ist, date, emp["shift_start"])

    # Compute changes: only fields that actually changed
    changes: Dict[str, Dict[str, Any]] = {}

    old_status = existing["status"]
    if target_status != old_status:
        changes["status"] = {"from": old_status, "to": target_status}

    old_punch_in = existing.get("punch_in")
    if old_punch_in is not None and old_punch_in.tzinfo is None:
        old_punch_in = old_punch_in.replace(tzinfo=timezone.utc)
    if target_punch_in != old_punch_in:
        changes["punch_in"] = {"from": old_punch_in, "to": target_punch_in}

    old_punch_out = existing.get("punch_out")
    if old_punch_out is not None and old_punch_out.tzinfo is None:
        old_punch_out = old_punch_out.replace(tzinfo=timezone.utc)
    if target_punch_out != old_punch_out:
        changes["punch_out"] = {"from": old_punch_out, "to": target_punch_out}

    old_work_hours = existing.get("work_hours")
    if target_work_hours != old_work_hours:
        changes["work_hours"] = {"from": old_work_hours, "to": target_work_hours}

    old_late_minutes = existing.get("late_minutes", 0)
    if target_late_minutes != old_late_minutes:
        changes["late_minutes"] = {"from": old_late_minutes, "to": target_late_minutes}

    old_overtime_minutes = existing.get("overtime_minutes", 0)
    if target_overtime_minutes != old_overtime_minutes:
        changes["overtime_minutes"] = {"from": old_overtime_minutes, "to": target_overtime_minutes}

    old_half_day = existing.get("half_day", False)
    if target_half_day != old_half_day:
        changes["half_day"] = {"from": old_half_day, "to": target_half_day}

    # Rule: a request that changes nothing is 422
    if not changes:
        raise HTTPException(status_code=422, detail="Request changes nothing")

    # Append to history
    now_utc = datetime.now(timezone.utc)
    history_entry = {
        "at": now_utc,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Atomic update preventing lost concurrent regularizations
    existing_history = existing.get("history", []) or []
    updated_doc = db.attendance_logs.find_one_and_update(
        {
            "_id": existing["_id"],
            "history": existing_history,
        },
        {
            "$set": {
                "status": target_status,
                "punch_in": target_punch_in,
                "punch_out": target_punch_out,
                "work_hours": target_work_hours,
                "late_minutes": target_late_minutes,
                "overtime_minutes": target_overtime_minutes,
                "half_day": target_half_day,
            },
            "$push": {
                "history": history_entry,
            },
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated_doc is None:
        raise HTTPException(status_code=409, detail="Concurrent regularization conflict")

    return doc_to_attendance_record(updated_doc)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str = Path(...),
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    year, month_num = map(int, month.split("-"))
    last_day_num = calendar.monthrange(year, month_num)[1]
    start_date_str = f"{month}-01"
    end_date_str = f"{month}-{last_day_num:02d}"

    # Calculate working_days per R7: Mon-Fri from joined_on for mid-month joiners
    joined_on = emp["joined_on"]
    effective_start_str = max(start_date_str, joined_on)
    working_days = 0
    if effective_start_str <= end_date_str:
        cur_dt = datetime.strptime(effective_start_str, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        while cur_dt <= end_dt:
            if cur_dt.weekday() < 5:  # Mon-Fri
                working_days += 1
            cur_dt += timedelta(days=1)

    # Aggregation on attendance_logs
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": start_date_str, "$lte": end_date_str},
            }
        },
        {
            "$project": {
                "status": 1,
                "half_day": 1,
                "late_minutes": 1,
                "overtime_minutes": 1,
                "iso_day": {"$isoDayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$lte": ["$iso_day", 5]},
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0.0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    agg_res = list(db.attendance_logs.aggregate(pipeline))
    if agg_res:
        row = agg_res[0]
        present_days = float(row.get("present_days", 0.0))
        leave_days = int(row.get("leave_days", 0))
        late_count = int(row.get("late_count", 0))
        total_late_minutes = int(row.get("total_late_minutes", 0))
        total_overtime_minutes = int(row.get("total_overtime_minutes", 0))
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    if working_days > 0:
        raw_pct = (present_days / working_days) * 100
        attendance_pct = round_half_up(raw_pct, 2)
    else:
        attendance_pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


def build_department_summary_pipeline(month: str, department: Optional[str] = None):
    year, month_num = map(int, month.split("-"))
    last_day_num = calendar.monthrange(year, month_num)[1]
    start_date_str = f"{month}-01"
    end_date_str = f"{month}-{last_day_num:02d}"

    match_stage: dict[str, Any] = {"joined_on": {"$lte": end_date_str}}
    if department:
        match_stage["department"] = department

    pipeline = [
        {"$match": match_stage},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp"]},
                                    {"$gte": ["$date", start_date_str]},
                                    {"$lte": ["$date", end_date_str]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "all_logs": {"$push": "$logs"},
            }
        },
        {
            "$project": {
                "department": "$_id",
                "headcount": 1,
                "logs": {
                    "$reduce": {
                        "input": "$all_logs",
                        "initialValue": [],
                        "in": {"$concatArrays": ["$$value", "$$this"]},
                    }
                },
            }
        },
        {
            "$project": {
                "department": 1,
                "headcount": 1,
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {
                                "$cond": [
                                    {
                                        "$and": [
                                            {"$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                            {
                                                "$lte": [
                                                    {
                                                        "$isoDayOfWeek": {
                                                            "$dateFromString": {"dateString": "$$l.date"}
                                                        }
                                                    },
                                                    5,
                                                ]
                                            },
                                        ]
                                    },
                                    {"$cond": ["$$l.half_day", 0.5, 1.0]},
                                    0.0,
                                ]
                            },
                        }
                    }
                },
                "work_hours_list": {
                    "$filter": {
                        "input": "$logs.work_hours",
                        "as": "wh",
                        "cond": {"$ne": ["$$wh", None]},
                    }
                },
                "late_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {"$cond": [{"$gt": ["$$l.late_minutes", 0]}, 1, 0]},
                        }
                    }
                },
                "total_late_minutes": {"$sum": "$logs.late_minutes"},
                "leave_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {"$cond": [{"$eq": ["$$l.status", "LEAVE"]}, 1, 0]},
                        }
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {"$cond": [{"$eq": ["$$l.status", "ON_DUTY"]}, 1, 0]},
                        }
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": 1,
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": [{"$size": "$work_hours_list"}, 0]},
                        {"$avg": "$work_hours_list"},
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]
    return pipeline


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    pipeline = build_department_summary_pipeline(month, department)
    results = list(db.employees.aggregate(pipeline))
    items = []
    for r in results:
        avg_wh = r["avg_work_hours"]
        if avg_wh is not None:
            avg_wh = round_half_up(avg_wh, 2)
        items.append({
            "department": r["department"],
            "headcount": int(r["headcount"]),
            "present_days": float(r["present_days"]),
            "avg_work_hours": avg_wh,
            "late_count": int(r["late_count"]),
            "total_late_minutes": int(r["total_late_minutes"]),
            "leave_count": int(r["leave_count"]),
            "on_duty_count": int(r["on_duty_count"]),
        })
    return {"month": month, "items": items}


def build_late_leaderboard_pipeline(month: str, limit: int = 10, department: Optional[str] = None):
    year, month_num = map(int, month.split("-"))
    last_day_num = calendar.monthrange(year, month_num)[1]
    start_date_str = f"{month}-01"
    end_date_str = f"{month}-{last_day_num:02d}"

    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "date": {"$gte": start_date_str, "$lte": end_date_str},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {
            "$sort": {
                "rank": 1,
                "emp.emp_code": 1,
            }
        },
        {"$limit": limit},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$emp.emp_code",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    pipeline = build_late_leaderboard_pipeline(month, limit, department)
    results = list(db.attendance_logs.aggregate(pipeline))
    items = []
    for r in results:
        items.append({
            "rank": int(r["rank"]),
            "emp_code": r["emp_code"],
            "name": r["name"],
            "department": r["department"],
            "total_late_minutes": int(r["total_late_minutes"]),
            "late_count": int(r["late_count"]),
        })
    return {"month": month, "items": items}


def build_department_trend_pipeline(department: str, from_date: str, to_date: str):
    from_dt = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    to_dt = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    to_dt_next = to_dt + timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "date": {"$gte": from_date, "$lte": to_date}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
        {
            "$match": {
                "emp.department": department
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0.0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date_dt": {"$dateFromString": {"dateString": "$_id"}},
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$unionWith": {
                "coll": "attendance_logs",
                "pipeline": [
                    {"$limit": 1},
                    {
                        "$project": {
                            "_id": 0,
                            "date_dt": {"$literal": from_dt},
                            "present_count": {"$literal": 0.0},
                            "late_count": {"$literal": 0},
                        }
                    },
                ],
            }
        },
        {
            "$group": {
                "_id": "$date_dt",
                "present_count": {"$sum": "$present_count"},
                "late_count": {"$sum": "$late_count"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "date_dt": "$_id",
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$densify": {
                "field": "date_dt",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [from_dt, to_dt_next],
                },
            }
        },
        {"$sort": {"date_dt": 1}},
        {
            "$project": {
                "date_dt": 1,
                "date_str": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_dt"}},
                "is_working_day": {"$lte": [{"$isoDayOfWeek": "$date_dt"}, 5]},
                "present_count": {"$ifNull": ["$present_count", 0.0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"cur_date": "$date_str"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$cur_date"]},
                                ]
                            }
                        }
                    },
                    {"$count": "cnt"},
                ],
                "as": "hc",
            }
        },
        {
            "$project": {
                "date": "$date_str",
                "date_dt": 1,
                "is_working_day": 1,
                "headcount": {
                    "$cond": [
                        {"$gt": [{"$size": "$hc"}, 0]},
                        {"$arrayElemAt": ["$hc.cnt", 0]},
                        0,
                    ]
                },
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$project": {
                "date": 1,
                "date_dt": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": {
                    "$cond": [
                        {"$gt": ["$headcount", 0]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                },
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date_dt": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, "current"],
                        },
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]
    return pipeline


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str = Path(...),
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
):
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="from must not be after to")

    # Check if department exists
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    pipeline = build_department_trend_pipeline(department, from_date, to_date)
    results = list(db.attendance_logs.aggregate(pipeline))
    items = []
    for r in results:
        rate = r["attendance_rate"]
        if rate is not None:
            rate = round_half_up(rate, 4)
        m_avg = r["moving_avg_7d"]
        if m_avg is not None:
            m_avg = round_half_up(m_avg, 4)
        items.append({
            "date": r["date"],
            "is_working_day": bool(r["is_working_day"]),
            "headcount": int(r["headcount"]),
            "present_count": float(r["present_count"]),
            "late_count": int(r["late_count"]),
            "attendance_rate": rate,
            "moving_avg_7d": m_avg,
        })
    return {"department": department, "items": items}


def clean_mongo_doc(obj: Any) -> Any:
    return json.loads(json_util.dumps(obj))


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str = Path(..., pattern=r"^(attendance_list|employee_monthly|department_summary|late_leaderboard|department_trend)$"),
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    status: Optional[Status] = None,
    from_date: Optional[str] = Query(None, alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date: Optional[str] = Query(None, alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        q: dict[str, Any] = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            date_q = {}
            if date_from:
                date_q["$gte"] = date_from
            if date_to:
                date_q["$lte"] = date_to
            q["date"] = date_q
        if status:
            q["status"] = status.value

        cmd = {
            "explain": {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        }
        res = db.command(cmd)
        return clean_mongo_doc(res)

    elif endpoint == "employee_monthly":
        effective_emp = emp_code or "EMP0001"
        effective_month = month or "2026-07"
        year, month_num = map(int, effective_month.split("-"))
        last_day_num = calendar.monthrange(year, month_num)[1]
        start_date_str = f"{effective_month}-01"
        end_date_str = f"{effective_month}-{last_day_num:02d}"

        pipeline = [
            {
                "$match": {
                    "emp_code": effective_emp,
                    "date": {"$gte": start_date_str, "$lte": end_date_str},
                }
            },
            {
                "$project": {
                    "status": 1,
                    "half_day": 1,
                    "late_minutes": 1,
                    "overtime_minutes": 1,
                    "iso_day": {"$isoDayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {"$lte": ["$iso_day", 5]},
                                    ]
                                },
                                {"$cond": ["$half_day", 0.5, 1.0]},
                                0.0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                    },
                    "late_count": {
                        "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                    },
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "total_overtime_minutes": {"$sum": "$overtime_minutes"},
                }
            },
        ]

        cmd = {
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(cmd)
        return clean_mongo_doc(res)

    elif endpoint == "department_summary":
        effective_month = month or "2026-07"
        pipeline = build_department_summary_pipeline(effective_month, department)
        cmd = {
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(cmd)
        return clean_mongo_doc(res)

    elif endpoint == "late_leaderboard":
        effective_month = month or "2026-07"
        pipeline = build_late_leaderboard_pipeline(effective_month, limit, department)
        cmd = {
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(cmd)
        return clean_mongo_doc(res)

    elif endpoint == "department_trend":
        effective_dept = department or "Engineering"
        effective_from = from_date or "2026-07-01"
        effective_to = to_date or "2026-07-07"
        pipeline = build_department_trend_pipeline(effective_dept, effective_from, effective_to)
        cmd = {
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(cmd)
        return clean_mongo_doc(res)
