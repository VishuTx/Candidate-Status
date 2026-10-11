# Employee Attendance & Analytics API

FastAPI and MongoDB backend service providing employee attendance tracking, manual regularization with an immutable audit trail, and analytics aggregation pipelines.

## Prerequisites
* Python 3.11+
* MongoDB 6.0+ (Tested on MongoDB 8.x; fully compatible with MongoDB 7.0 and Atlas)

## How to Run
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt

# Run the API server (MONGO_URI and MONGO_DB are read from the environment or .env):
uvicorn app.main:app --port 8000
```

To load sample data:
```bash
python sample_seed.py
```

## Key Highlights & Design Guarantees
* **All code lives in `app/main.py`**; boots cleanly with `uvicorn app.main:app --port 8000`.
* **Idempotent Indexing**: All unique and compound indexes are created automatically at application startup via FastAPI lifespan.
* **Native Aggregations**: All analytics endpoints (`/analytics/*`) execute purely in MongoDB aggregation pipelines (utilizing `$setWindowFields` for competition ranking and `$densify` for gap filling).
* **Explain Endpoint**: `GET /admin/explain/{endpoint}` reports execution stats verifying index scans (`IXSCAN`) and absence of collection scans.
* **Documentation**:
  - `REVIEW.md`: Starter code defect audit and resolutions.
  - `DECISIONS.md`: Design rationale for indexing, concurrency, ranking, and scalability.
  - `detail.md`: Complete architectural guide and workflow breakdown.
