# DECISIONS.md

1. **Indexes.**
   We created a unique index on `employees.emp_code` to enforce business identity and fast lookups, alongside compound indexes `(department, joined_on)` and `(joined_on, department)` to power department summary filtering and daily headcount counting. On `attendance_logs`, we created a unique compound index on `(emp_code, date)` to guarantee rule R1 (one record per employee/day). We added `(date DESC, emp_code ASC)` for attendance list streaming pagination, `(emp_code, punch_in)` for swift punch-out matching, and `(date, late_minutes)` for leaderboard filtering. We considered and rejected a standalone index on `status` due to low cardinality and poor query selectivity.

2. **Punch-in race.**
   Both concurrent requests compute the attendance date and invoke `insert_one`. MongoDB's unique index on `(emp_code, date)` serializes the write atomically at the storage engine level. The winning request inserts cleanly and returns 201. The second request raises a `DuplicateKeyError`, which our exception handler intercepts to return 409 Conflict.

3. **Ties.**
   Using MongoDB `$setWindowFields` with `$rank`, tied employees share the exact same rank while skipping subsequent ranks (standard competition ranking: 1, 2, 2, 4). Tied rows are sorted deterministically by `emp_code ASC`. Because `limit` is applied after ranking, only the row with the earlier `emp_code` fitting within the limit boundary is returned.

4. **Headcount.**
   The pipeline originates from `employees` rather than `attendance_logs`, filtering `joined_on <= month_end` and looking up attendance logs. Because employees form the root document stream, employees with zero attendance logs still generate a document with an empty logs array, counting 1 toward departmental headcount.

5. **One thing you would change for 100x data.**
   Pre-aggregate daily and monthly departmental roll-ups into summary collections during the punch-out and regularization write paths, eliminating heavy cross-collection aggregations in favor of indexed point reads.
