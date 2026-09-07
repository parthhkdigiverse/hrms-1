from bson import ObjectId
from datetime import datetime, timedelta, timezone, date
from typing import List, Optional, Dict
import asyncio
import re
import schemas
import auth
import calendar
import google_auth
import google_calendar
import pytz
from websocket import manager as ws_manager
from email_utils import send_caution_email

import time
import urllib.request
import threading

IST = pytz.timezone('Asia/Kolkata')
_real_time_anchor = None
_mono_anchor = None
_sync_lock = threading.Lock()
_sync_thread_started = False

def _sync_time_from_network():
    """Fetch real time from Google in a background thread (non-blocking)."""
    global _real_time_anchor, _mono_anchor
    try:
        req = urllib.request.Request('http://www.google.com', method='HEAD')
        with urllib.request.urlopen(req, timeout=3) as response:
            date_str = response.headers.get('Date')
            dt = datetime.strptime(date_str, '%a, %d %b %Y %H:%M:%S %Z')
            with _sync_lock:
                _real_time_anchor = dt.replace(tzinfo=timezone.utc).astimezone(IST)
                _mono_anchor = time.monotonic()
    except Exception:
        # Silently fall back to system time if network unavailable
        pass

def _start_sync_thread():
    """Start a daemon thread that syncs time every 30 minutes."""
    global _sync_thread_started
    if _sync_thread_started:
        return
    _sync_thread_started = True
    
    def _loop():
        while True:
            _sync_time_from_network()
            time.sleep(1800)  # Re-sync every 30 minutes
    
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

def get_now():
    global _real_time_anchor, _mono_anchor
    
    # Start the background sync thread on first call
    if not _sync_thread_started:
        _start_sync_thread()
        # Give the first sync a moment to complete
        time.sleep(0.1)
    
    with _sync_lock:
        if _real_time_anchor is None:
            # Sync hasn't completed yet — use system time
            return datetime.now(IST)
        elapsed = time.monotonic() - _mono_anchor
        return _real_time_anchor + timedelta(seconds=elapsed)

_PLACEHOLDER_TIMES = {"", "--", "--:--", "-"}

def _is_placeholder_time(time_str) -> bool:
    if time_str is None:
        return True
    return str(time_str).strip() in _PLACEHOLDER_TIMES

def _record_to_date(date_val) -> date:
    if isinstance(date_val, datetime):
        if date_val.tzinfo is not None:
            return date_val.astimezone(IST).date()
        return date_val.date()
    if isinstance(date_val, date):
        return date_val
    date_str = str(date_val).split('T')[0].split(' ')[0]
    return datetime.strptime(date_str[:10], "%Y-%m-%d").date()

def _resolve_punch_start_time(record: dict) -> Optional[str]:
    candidates = [
        record.get("lastPunchIn"),
        record.get("checkIn"),
    ]
    punches = record.get("punches") or []
    for punch in reversed(punches):
        candidates.append(punch.get("punchIn"))
    for candidate in candidates:
        if candidate and not _is_placeholder_time(candidate):
            return str(candidate).strip()
    return None

def parse_datetime(date_val, time_str):
    if _is_placeholder_time(time_str):
        return get_now()
    time_str = str(time_str).strip()
    if isinstance(date_val, (date, datetime)):
        date_str = date_val.strftime("%Y-%m-%d")
    else:
        date_str = str(date_val).split('T')[0].split(' ')[0]
    combined_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
    ]
    for fmt in combined_formats:
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", fmt)
            return IST.localize(dt)
        except ValueError:
            continue
    return get_now()

def fix_id(doc):
    from datetime import datetime, date
    if doc is None:
        return None
    if isinstance(doc, list):
        return [fix_id(x) for x in doc]
    if isinstance(doc, dict):
        new_doc = {}
        for k, v in doc.items():
            if k == "_id":
                new_doc["id"] = str(v)
            elif isinstance(v, ObjectId):
                new_doc[k] = str(v)
            elif isinstance(v, (datetime, date)):
                if isinstance(v, datetime) and (v.hour != 0 or v.minute != 0 or v.second != 0):
                    if v.tzinfo is None:
                        new_doc[k] = v.replace(tzinfo=timezone.utc).isoformat()
                    else:
                        new_doc[k] = v.isoformat()
                else:
                    new_doc[k] = v.strftime("%Y-%m-%d")
            elif isinstance(v, float):
                import math
                if math.isnan(v) or math.isinf(v):
                    new_doc[k] = 0.0
                else:
                    new_doc[k] = v
            elif isinstance(v, (dict, list)):
                new_doc[k] = fix_id(v)
            else:
                new_doc[k] = v
        return new_doc
    elif isinstance(doc, ObjectId):
        return str(doc)
    elif isinstance(doc, float):
        import math
        if math.isnan(doc) or math.isinf(doc):
            return 0.0
    return doc

async def archive_and_delete_many(db, collection_name: str, query: dict):
    docs = await db[collection_name].find(query).to_list(length=None)
    if docs:
        now = get_now()
        for doc in docs:
            doc["archived_at"] = now
        archive_collection = f"archive_{collection_name}"
        await db[archive_collection].insert_many(docs)
    await db[collection_name].delete_many(query)

async def archive_and_delete_one(db, collection_name: str, query: dict):
    doc = await db[collection_name].find_one(query)
    if doc:
        doc["archived_at"] = get_now()
        archive_collection = f"archive_{collection_name}"
        await db[archive_collection].insert_one(doc)
    await db[collection_name].delete_one(query)

async def delete_employee(db, employee_id: str, performed_by: str = "System", user_name: str = "System User"):
    existing = await db.employees.find_one({"_id": ObjectId(employee_id)})
    if existing:
        await log_activity(
            db=db,
            action="Employee Deleted",
            performedBy=performed_by,
            userName=user_name,
            details=f"Employee '{existing.get('name')}' (ID: {existing.get('employeeId', 'N/A')}) was deleted from the system.",
            employeeId=employee_id
        )
        old_photo = existing.get("profilePhoto")
        if old_photo and not old_photo.startswith("http"):
            import os
            filename = old_photo.split('/')[-1]
            old_photo_path = os.path.join("uploads", filename)
            if os.path.exists(old_photo_path) and os.path.isfile(old_photo_path):
                try:
                    os.remove(old_photo_path)
                except Exception as e:
                    print(f"Error removing deleted employee's photo: {e}")
    # Cascade deletes for employee
    await archive_and_delete_many(db, "attendance", {"employeeId": employee_id})
    await archive_and_delete_many(db, "leave_requests", {"employee_id": employee_id})
    await archive_and_delete_many(db, "payroll", {"employeeId": employee_id})
    await archive_and_delete_many(db, "salary_structures", {"employeeId": employee_id})
    await archive_and_delete_many(db, "employee_daily_reports", {"employeeId": employee_id})
    try:
        emp_obj_id = ObjectId(employee_id)
        await archive_and_delete_many(db, "notifications", {"$or": [{"employee_id": employee_id}, {"employee_id": emp_obj_id}, {"employeeId": employee_id}]})
    except Exception:
        await archive_and_delete_many(db, "notifications", {"$or": [{"employee_id": employee_id}, {"employeeId": employee_id}]})
    await archive_and_delete_many(db, "remarks", {"employeeId": employee_id})
    await archive_and_delete_many(db, "bonus_deductions", {"employeeId": employee_id})
    await archive_and_delete_many(db, "sales_targets", {"employeeId": employee_id})
    
    # Unassign tasks
    await db.wm_tasks.update_many(
        {"assignedToId": employee_id},
        {"$set": {"assignedToId": None, "assignedToName": "Unassigned"}}
    )

    await archive_and_delete_one(db, "employees", {"_id": ObjectId(employee_id)})
    
    try:
        await ws_manager.broadcast_all("data_refresh", {"entity": "employees"})
    except Exception:
        pass
        
    return True

async def get_employee(db, employee_id: str):
    try:
        doc = await db.employees.find_one({"_id": ObjectId(employee_id)})
        return fix_id(doc)
    except Exception:
        return None

async def sync_employee_salary_to_structure(db, employee_id: str, salary_amount: float):
    if salary_amount is None:
        return
    try:
        existing = await db.salary_structures.find_one({"employeeId": employee_id})
        if existing:
            if existing.get("monthlyGross") != salary_amount or existing.get("basic") != salary_amount:
                await db.salary_structures.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"monthlyGross": salary_amount, "basic": salary_amount}}
                )
        else:
            new_structure = {
                "employeeId": employee_id,
                "basic": salary_amount,
                "hra": 0.0,
                "conveyance": 0.0,
                "medical": 0.0,
                "specialAllowance": 0.0,
                "pf": 0.0,
                "esi": 0.0,
                "professionalTax": 0.0,
                "tds": 0.0,
                "monthlyGross": salary_amount
            }
            await db.salary_structures.insert_one(new_structure)
    except Exception as e:
        print(f"Error syncing salary to structure: {e}")


async def update_employee(db, employee_id: str, employee_update: schemas.EmployeeUpdate, performed_by: str = "System", user_name: str = "System User"):
    # Fetch existing employee first to handle name recalculation
    existing = await db.employees.find_one({"_id": ObjectId(employee_id)})
    if not existing:
        return None
    
    update_data = employee_update.dict(exclude_unset=True)

    # Ensure sub_department is synced in both naming styles
    if "sub_department" in update_data or "subDepartment" in update_data:
        sub_dept_val = update_data.get("sub_department") or update_data.get("subDepartment") or ""
        update_data["sub_department"] = sub_dept_val
        update_data["subDepartment"] = sub_dept_val

    sync_active_bond(update_data)
    
    # If a new profile photo is uploaded/edited, delete the old one from the uploads folder
    if "profilePhoto" in update_data and update_data["profilePhoto"] != existing.get("profilePhoto"):
        import os
        old_photo = existing.get("profilePhoto")
        if old_photo and not old_photo.startswith("http"):
            filename = old_photo.split('/')[-1]
            old_photo_path = os.path.join("uploads", filename)
            if os.path.exists(old_photo_path) and os.path.isfile(old_photo_path):
                try:
                    os.remove(old_photo_path)
                except Exception as e:
                    print(f"Error removing old photo: {e}")
    
    # Recalculate name if name components are updated
    if any(field in update_data for field in ["firstName", "middleName", "lastName"]):
        first = update_data.get("firstName", existing.get("firstName", ""))
        middle = update_data.get("middleName", existing.get("middleName", ""))
        last = update_data.get("lastName", existing.get("lastName", ""))
        
        name = f"{first} {last}"
        if middle:
            name = f"{first} {middle} {last}"
    # Single Team Leader per department validation
    target_dept = update_data.get("department", existing.get("department", ""))
    target_desig = update_data.get("designation", existing.get("designation", ""))
    target_role = update_data.get("role", existing.get("role", ""))
    await validate_single_team_leader_per_dept(
        db,
        department=target_dept,
        designation=target_desig,
        role=target_role,
        exclude_employee_id=employee_id
    )

    # Diff updates
    log_details, diffs = format_field_changes(existing, update_data, f"Employee '{existing.get('name')}'")

    await db.employees.update_one(
        {"_id": ObjectId(employee_id)},
        {"$set": update_data}
    )
    
    if "salary" in update_data and update_data["salary"] is not None:
        await sync_employee_salary_to_structure(db, employee_id, update_data["salary"])
    
    if diffs:
        await log_activity(
            db=db,
            action="Employee Updated",
            performedBy=performed_by,
            userName=user_name,
            details=log_details,
            diffs=diffs,
            employeeId=employee_id
        )

    try:
        await ws_manager.broadcast_all("data_refresh", {"entity": "employees"})
    except Exception:
        pass
        
    updated_doc = await db.employees.find_one({"_id": ObjectId(employee_id)})
    return fix_id(updated_doc)

async def get_employees(db, skip: int = 0, limit: int = 100, include_inactive: bool = False):
    query = {}
    if not include_inactive:
        query["status"] = {
            "$nin": ["inactive", "Inactive", "INACTIVE", "terminated", "Terminated", "TERMINATED", "resigned", "Resigned", "RESIGNED"]
        }
    cursor = db.employees.find(query).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_attendance(db, employee_id: Optional[str] = None, skip: int = 0, limit: int = 100):
    query = {}
    if employee_id:
        query["employeeId"] = employee_id
    cursor = db.attendance.find(query).sort([("date", -1), ("_id", -1)]).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_leave_requests(db, skip: int = 0, limit: int = 100):
    cursor = db.leave_requests.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_announcements(db, skip: int = 0, limit: int = 100):
    cursor = db.announcements.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_dashboard_stats(db):
    doc = await db.dashboard_stats.find_one()
    if not doc:
        doc = {
            "id": "default",
            "totalEmployees": 0,
            "presentToday": 0,
            "onLeave": 0,
            "newJoinees": 0,
            "pendingLeaves": 0,
            "upcomingBirthdays": 0,
            "upcomingAnniversaries": 0,
            "lateToday": 0
        }
        
    total_employees = await db.employees.count_documents({"status": "active"})
    
    today = get_now()
    today_str = today.strftime("%Y-%m-%d")
    today_dt_naive = datetime.strptime(today_str, "%Y-%m-%d")
    today_dt_aware = today_dt_naive.replace(tzinfo=IST)
    
    present_today = await db.attendance.count_documents({
        "date": {"$in": [today_dt_naive, today_dt_aware]},
        "status": {"$in": ["Logged", "Active", "On Break"]}
    })
    
    on_leave = await db.attendance.count_documents({
        "date": {"$in": [today_dt_naive, today_dt_aware]},
        "status": "Leave"
    })
    
    pending_leaves = await db.leave_requests.count_documents({
        "status": {"$in": ["Pending", "pending", "Awaiting Approval"]}
    })
    
    late_today = await db.remarks.count_documents({
        "date": {"$in": [today_dt_naive, today_dt_aware]},
        "type": "Late Punch-in",
        "isDeleted": {"$nin": [True, "true", "True"]}
    })
    
    doc["totalEmployees"] = total_employees
    doc["presentToday"] = present_today
    doc["onLeave"] = on_leave
    doc["pendingLeaves"] = pending_leaves
    doc["lateToday"] = late_today
    doc["newJoinees"] = doc.get("newJoinees", 0)
    doc["upcomingBirthdays"] = doc.get("upcomingBirthdays", 0)
    doc["upcomingAnniversaries"] = doc.get("upcomingAnniversaries", 0)
    
    return fix_id(doc)

async def get_analytics_overview(db, months: int = 6):
    today = get_now()
    
    # Department Distribution
    dept_pipeline = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": "$department", "employees": {"$sum": 1}}}
    ]
    dept_data = await db.employees.aggregate(dept_pipeline).to_list(length=100)
    department_distribution = [{"name": d["_id"] or "Unknown", "employees": d["employees"]} for d in dept_data if d["_id"]]

    # Leave Distribution
    leave_pipeline = [
        {"$group": {"_id": "$type", "value": {"$sum": 1}}}
    ]
    leave_data = await db.leave_requests.aggregate(leave_pipeline).to_list(length=100)
    leave_distribution = [{"name": l["_id"] or "Other", "value": l["value"]} for l in leave_data]
    
    # Add colors to leave distribution for charts
    colors = ['#3b82f6', '#ef4444', '#f59e0b', '#6b7280', '#10b981', '#8b5cf6']
    for i, l in enumerate(leave_distribution):
        l["color"] = colors[i % len(colors)]

    # Performance Distribution
    perf_pipeline = [
        {"$group": {"_id": "$rating", "count": {"$sum": 1}}}
    ]
    perf_data = await db.kpi_records.aggregate(perf_pipeline).to_list(length=100)
    # Map back to capitalize
    perf_map = {p["_id"].capitalize() if p["_id"] else "Unknown": p["count"] for p in perf_data}
    performance_distribution = [
        {"rating": r, "count": perf_map.get(r, 0)} 
        for r in ["Excellent", "Good", "Average", "Poor"]
    ]

    # Time-based trends (Attendance & Hiring)
    attendance_trend = []
    hiring_trend = []
    
    for i in range(months - 1, -1, -1):
        target_date = today - timedelta(days=30*i)
        month_name = target_date.strftime("%b")
        year = target_date.year
        month_num = target_date.month
        
        # Start and end of that month
        start_dt = datetime(year, month_num, 1)
        if month_num == 12:
            end_dt = datetime(year + 1, 1, 1)
        else:
            end_dt = datetime(year, month_num + 1, 1)
            
        # Make them timezone aware based on IST like the rest of the app
        start_dt = IST.localize(start_dt)
        end_dt = IST.localize(end_dt)
            
        # Attendance Trend
        present = await db.attendance.count_documents({
            "date": {"$gte": start_dt, "$lt": end_dt},
            "status": {"$in": ["Logged", "Active", "On Break", "Present"]}
        })
        absent = await db.attendance.count_documents({
            "date": {"$gte": start_dt, "$lt": end_dt},
            "status": "Absent"
        })
        late = await db.attendance.count_documents({
            "date": {"$gte": start_dt, "$lt": end_dt},
            "isLate": True
        })
        attendance_trend.append({
            "month": month_name,
            "present": present,
            "absent": absent,
            "late": late
        })
        
        # Hiring Trend
        hires = await db.employees.count_documents({
            "joinDate": {"$gte": start_dt, "$lt": end_dt}
        })
        # Simplified exits (employees who are inactive and updated in that month)
        exits = await db.employees.count_documents({
            "status": "inactive",
            "updated_at": {"$gte": start_dt, "$lt": end_dt}
        })
        hiring_trend.append({
            "month": month_name,
            "hires": hires,
            "exits": exits
        })

    # Summary Stats
    total_employees = await db.employees.count_documents({"status": "active"})
    total_attendance = sum([t["present"] + t["absent"] for t in attendance_trend])
    avg_attendance = (sum([t["present"] for t in attendance_trend]) / total_attendance * 100) if total_attendance > 0 else 0
    
    total_perf_scores = await db.kpi_records.aggregate([{"$group": {"_id": None, "avg": {"$avg": "$score"}}}]).to_list(length=1)
    avg_perf = total_perf_scores[0]["avg"] if total_perf_scores else 0
    # Normalize score from 0-100 to 0-5
    avg_perf_5 = round(avg_perf / 20, 1) if avg_perf else 0.0

    total_exits = sum([t["exits"] for t in hiring_trend])
    attrition = round((total_exits / total_employees * 100), 1) if total_employees > 0 else 0

    return {
        "departmentDistribution": department_distribution,
        "attendanceTrend": attendance_trend,
        "leaveDistribution": leave_distribution,
        "hiringTrend": hiring_trend,
        "performanceDistribution": performance_distribution,
        "summaryStats": {
            "totalEmployees": total_employees,
            "avgAttendanceRate": round(avg_attendance),
            "avgPerformanceScore": avg_perf_5,
            "attritionRate": attrition
        }
    }

async def get_payroll(db, skip: int = 0, limit: int = 100):
    cursor = db.payroll.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_salary_structures(db, skip: int = 0, limit: int = 100):
    cursor = db.salary_structures.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_salary_structure_by_employee(db, employee_id: str):
    # Try finding directly by the ID provided (could be custom ID or MongoDB ID)
    doc = await db.salary_structures.find_one({"employeeId": employee_id})
    if doc:
        return fix_id(doc)
    
    # If not found, check if employee_id is a MongoDB ID and look up the custom ID
    try:
        emp = await db.employees.find_one({"_id": ObjectId(employee_id)})
        if emp:
            doc = await db.salary_structures.find_one({"employeeId": emp.get("employeeId")})
            if doc:
                return fix_id(doc)
    except Exception:
        pass
    return None

async def create_or_update_salary_structure(db, salary: schemas.SalaryStructureCreate):
    salary_dict = salary.dict()
    await db.salary_structures.update_one(
        {"employeeId": salary.employeeId},
        {"$set": salary_dict},
        upsert=True
    )
    
    # Sync monthlyGross back to the employee's salary field
    try:
        emp_filter = {}
        if len(salary.employeeId) == 24:
            from bson import ObjectId
            try:
                emp_filter = {"_id": ObjectId(salary.employeeId)}
            except Exception:
                emp_filter = {"employeeId": salary.employeeId}
        else:
            emp_filter = {"employeeId": salary.employeeId}
            
        await db.employees.update_one(
            emp_filter,
            {"$set": {"salary": salary.monthlyGross}}
        )
    except Exception as e:
        print(f"Error syncing structure monthlyGross back to employee: {e}")

    doc = await db.salary_structures.find_one({"employeeId": salary.employeeId})
    return fix_id(doc)

async def get_bonus_deductions(db, month: str = None, year: int = None):
    query = {}
    if month: query["month"] = month
    if year: query["year"] = year
    cursor = db.bonus_deductions.find(query).sort("_id", -1)
    rows = await cursor.to_list(length=1000)
    return [fix_id(row) for row in rows]

async def get_bonus_deductions_with_remarks(db, month: str = None, year: int = None):
    # 1. Get manual adjustments
    adjustments = await get_bonus_deductions(db, month, year)
    
    # 2. Get penalty types for lookup
    penalty_types = await get_penalty_types(db)
    penalty_names = [p["name"] for p in penalty_types]
    
    # 3. Get remarks
    remark_query = {"isDeleted": {"$nin": [True, "true", "True"]}}
    if month and year:
        try:
            month_num = list(calendar.month_name).index(month)
        except ValueError:
            month_map = {m: i for i, m in enumerate(calendar.month_name)}
            month_num = month_map.get(month.capitalize(), 1)
        
        _, num_days = calendar.monthrange(year, month_num)
        start_dt_naive = datetime(year, month_num, 1)
        end_dt_naive = datetime(year, month_num, num_days, 23, 59, 59)
        start_dt_aware = start_dt_naive.replace(tzinfo=IST)
        end_dt_aware = end_dt_naive.replace(tzinfo=IST)
        remark_query["$or"] = [
            {"date": {"$gte": start_dt_naive, "$lte": end_dt_naive}},
            {"date": {"$gte": start_dt_aware, "$lte": end_dt_aware}}
        ]
    
    cursor = db.remarks.find(remark_query).sort("_id", -1)
    remarks = await cursor.to_list(length=1000)
    
    # 4. Merge remarks that are deductions
    for r in remarks:
        is_penalty = r["type"] in penalty_names or r["type"] == "Late Punch-in"
        if not is_penalty:
            continue
            
        # Avoid duplication if already in bonus_deductions (e.g. Work Rejected)
        # Check by reason/details matching
        if any(a["reason"] in r["details"] or r["details"] in a["reason"] for a in adjustments if a["employeeId"] == r["employeeId"]):
            continue

        r_date = r.get("date")
        r_month = month or "Unknown"
        r_year = year or 2026

        if isinstance(r_date, datetime):
            r_month = r_date.strftime("%B")
            r_year = r_date.year
        elif isinstance(r_date, str):
            try:
                if "T" in r_date or "-" in r_date:
                    parts = r_date.split("T")[0].split("-")
                    if len(parts[0]) == 4:
                        r_year = int(parts[0])
                        month_num = int(parts[1])
                    else:
                        r_year = int(parts[2])
                        month_num = int(parts[1])
                    r_month = calendar.month_name[month_num]
                else:
                    date_parts = r_date.split(" ")
                    r_month = date_parts[0].replace(",", "")
                    r_year = int(date_parts[-1])
            except Exception:
                pass

        # Always calculate one-day salary dynamically for Late Punch-in
        if r["type"] == "Late Punch-in":
            salary_struct = await get_salary_structure_by_employee(db, r["employeeId"])
            if salary_struct:
                try:
                    if isinstance(r_date, datetime):
                        month_num = r_date.month
                        curr_year = r_date.year
                    else:
                        if "T" in r_date or "-" in r_date:
                            parts = r_date.split("T")[0].split("-")
                            if len(parts[0]) == 4:
                                curr_year = int(parts[0])
                                month_num = int(parts[1])
                            else:
                                curr_year = int(parts[2])
                                month_num = int(parts[1])
                        else:
                            date_parts = r_date.split(" ")
                            r_month_name = date_parts[0].replace(",", "")
                            curr_year = int(date_parts[-1])
                            try:
                                month_num = list(calendar.month_abbr).index(r_month_name)
                            except ValueError:
                                month_num = list(calendar.month_name).index(r_month_name)
                        
                    _, num_days = calendar.monthrange(curr_year, month_num)
                    
                    sundays = sum(1 for d in range(1, num_days + 1) if calendar.weekday(curr_year, month_num, d) == 6)
                    
                    # Count Holidays for the company
                    try:
                        from bson import ObjectId
                        emp = await db.employees.find_one({"_id": ObjectId(r["employeeId"])} if len(r["employeeId"]) == 24 else {"employeeId": r["employeeId"]})
                    except Exception:
                        emp = await db.employees.find_one({"employeeId": r["employeeId"]})
                    emp_company = emp.get("company") if emp else None
                    start_dt_naive = datetime(curr_year, month_num, 1)
                    end_dt_naive = datetime(curr_year, month_num, num_days, 23, 59, 59)
                    start_dt_aware = start_dt_naive.replace(tzinfo=IST)
                    end_dt_aware = end_dt_naive.replace(tzinfo=IST)
                    holiday_query = {
                        "$or": [
                            {"date": {"$gte": start_dt_naive, "$lte": end_dt_naive}},
                            {"date": {"$gte": start_dt_aware, "$lte": end_dt_aware}}
                        ]
                    }
                    if emp_company:
                        holiday_query["$or"] = [
                            {"$and": [{"company": emp_company}, {"$or": [{"date": {"$gte": start_dt_naive, "$lte": end_dt_naive}}, {"date": {"$gte": start_dt_aware, "$lte": end_dt_aware}}]}]},
                            {"$and": [{"company": {"$in": [None, "", "null"]}}, {"$or": [{"date": {"$gte": start_dt_naive, "$lte": end_dt_naive}}, {"date": {"$gte": start_dt_aware, "$lte": end_dt_aware}}]}]}
                        ]
                    month_holidays = await db.holidays.find(holiday_query).to_list(length=31)
                    
                    unique_holidays = 0
                    for h in month_holidays:
                        h_date = h.get("date")
                        h_date_str = h_date.strftime("%Y-%m-%d") if isinstance(h_date, (date, datetime)) else str(h_date)
                        if h_date_str not in [f"{curr_year}-{str(month_num).zfill(2)}-{str(d).zfill(2)}" for d in range(1, num_days + 1) if calendar.weekday(curr_year, month_num, d) == 6]:
                            unique_holidays += 1
                            
                    total_working_days = max(1, num_days - sundays - unique_holidays)
                    p_amount = int(round(salary_struct["monthlyGross"] / total_working_days))
                except Exception as e:
                    print(f"Error calculating p_amount in merged list: {e}")
                    p_amount = int(round(salary_struct["monthlyGross"] / 30))
        else:
            p_amount = next((p["amount"] for p in penalty_types if p["name"] == r["type"]), 0)

        r_date_str = None
        if isinstance(r_date, datetime):
            r_date_str = r_date.strftime("%Y-%m-%d")
        elif isinstance(r_date, str):
            if "T" in r_date:
                r_date_str = r_date.split("T")[0]
            elif "-" in r_date:
                r_date_str = r_date
            else:
                try:
                    dt = datetime.strptime(r_date, "%b %d, %Y")
                    r_date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    try:
                        dt = datetime.strptime(r_date, "%B %d, %Y")
                        r_date_str = dt.strftime("%Y-%m-%d")
                    except Exception:
                        r_date_str = r_date

        adjustments.append({
            "id": f"remark_{str(r['_id'])}",
            "employeeId": r["employeeId"],
            "month": r_month,
            "year": r_year,
            "type": "deduction",
            "amount": p_amount, # Note: Late punch will show 0 here as it's calculated at payroll
            "reason": f"[Remark] {r['type']}: {r['details']}",
            "status": "active",
            "date": r_date_str
        })
        
    # Sort by date descending so newest are on top
    adjustments.sort(key=lambda x: str(x.get("date", "")), reverse=True)
    return adjustments
async def create_bonus_deduction(db, item: schemas.BonusDeductionCreate):
    item_dict = item.dict()
    result = await db.bonus_deductions.insert_one(item_dict)
    doc = await db.bonus_deductions.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def run_payroll_processing(db, month: str, year: int, performed_by: str = "System", user_name: str = "System User"):
    # 1. Get all employees
    employees = await get_employees(db, limit=1000)
    
    # 2. Get days in month
    try:
        month_num = list(calendar.month_name).index(month)
    except ValueError:
        # Fallback if month is abbreviated or case-different
        month_map = {m: i for i, m in enumerate(calendar.month_name)}
        month_num = month_map.get(month.capitalize(), 1)
        
    _, num_days = calendar.monthrange(year, month_num)
    
    penalty_types = await get_penalty_types(db)
    system_settings = await get_system_settings(db)
    late_punch_deduction_enabled = system_settings.get("latePunchDeductionEnabled", True)
    daily_progress_reject_deduction_enabled = system_settings.get("dailyProgressRejectDeductionEnabled", False)
    payroll_results = []
    
    for emp in employees:
        emp_id = emp["id"]
        # Skip inactive and admin employees to avoid generating unnecessary payroll records
        if emp.get("status", "").lower() == "inactive" or emp.get("role", "").lower() == "admin":
            await db.payroll.delete_many({"employeeId": emp_id, "month": month, "year": year})
            continue
        # Get salary structure
        salary = await get_salary_structure_by_employee(db, emp_id)
        if not salary:
            continue 
            
        # Start/End date strings
        start_date_str = f"{year}-{str(month_num).zfill(2)}-01"
        end_date_str = f"{year}-{str(month_num).zfill(2)}-{num_days}"
        
        # Count Weekends (Sundays only)
        sundays = 0
        sunday_dates = []
        for d in range(1, num_days + 1):
            if calendar.weekday(year, month_num, d) == 6: # Sunday
                sundays += 1
                sunday_dates.append(f"{year}-{str(month_num).zfill(2)}-{str(d).zfill(2)}")
        
        # Count Holidays for this employee's company
        emp_company = emp.get("company")
        holiday_start_dt = datetime(year, month_num, 1, tzinfo=IST)
        holiday_end_dt = datetime(year, month_num, num_days, 23, 59, 59, tzinfo=IST)
        holiday_query = {
            "date": {"$gte": holiday_start_dt, "$lte": holiday_end_dt}
        }
        if emp_company:
            # Match specific company holidays OR global holidays (where company is null/empty)
            holiday_query["$or"] = [
                {"company": emp_company},
                {"company": None},
                {"company": ""}
            ]
        
        holidays_cursor = db.holidays.find(holiday_query)
        month_holidays = await holidays_cursor.to_list(length=31)
        
        unique_holidays = 0
        for h in month_holidays:
            # Only count holiday if it doesn't fall on a Sunday
            h_date_str = h["date"].strftime("%Y-%m-%d") if isinstance(h["date"], (date, datetime)) else h["date"]
            if h_date_str not in sunday_dates:
                unique_holidays += 1

        # Check employment start date restriction
        emp_start_day = 1
        adjusted_gross = salary["monthlyGross"]
        is_future_employment = False
        post_employment_working_days = 0

        # Helper to parse employmentStartDate
        def parse_emp_date(val):
            if not val:
                return None
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, date):
                return val
            val_str = str(val).strip().split('T')[0].split(' ')[0]
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(val_str, fmt).date()
                except ValueError:
                    continue
            return None

        if emp.get("hasEmployment") and emp.get("employmentStartDate"):
            emp_start = parse_emp_date(emp["employmentStartDate"])
            if emp_start:
                month_start_date = date(year, month_num, 1)
                month_end_date = date(year, month_num, num_days)
                if emp_start > month_end_date:
                    is_future_employment = True
                    adjusted_gross = 0.0
                elif emp_start >= month_start_date:
                    emp_start_day = emp_start.day
                    
                    # Calculate working days in this month before/after employment start
                    pre_employment_working_days = 0
                    post_employment_working_days = 0
                    
                    sunday_dates_set_tmp = set(sunday_dates)
                    holiday_dates_set_tmp = {
                        h["date"].strftime("%Y-%m-%d") if isinstance(h["date"], (date, datetime)) else h["date"]
                        for h in month_holidays if "date" in h
                    }
                    
                    for d in range(1, num_days + 1):
                        d_str = f"{year}-{str(month_num).zfill(2)}-{str(d).zfill(2)}"
                        if d_str not in sunday_dates_set_tmp and d_str not in holiday_dates_set_tmp:
                            if d < emp_start_day:
                                pre_employment_working_days += 1
                            else:
                                post_employment_working_days += 1
                                
                    total_wd = pre_employment_working_days + post_employment_working_days
                    if total_wd > 0:
                        adjusted_gross = salary["monthlyGross"] * (post_employment_working_days / total_wd)
                    else:
                        adjusted_gross = 0.0

        if is_future_employment:
            salary = dict(salary)
            salary["monthlyGross"] = 0.0
            total_working_days = 1
            per_day_gross = 0.0
        elif emp_start_day > 1:
            salary = dict(salary)
            salary["monthlyGross"] = adjusted_gross
            total_working_days = post_employment_working_days if post_employment_working_days > 0 else 1
            per_day_gross = adjusted_gross / total_working_days
        else:
            total_working_days = num_days - sundays - unique_holidays
            per_day_gross = salary["monthlyGross"] / total_working_days

        # 1. Map out approved leaves for this month with deduction factors
        # Robust query checking both camelCase and snake_case for employee ID, and case-insensitive status
        leave_cursor = db.leave_requests.find({
            "$or": [
                {"employee_id": emp_id},
                {"employeeId": emp_id}
            ],
            "status": {"$in": ["Approved", "approved"]}
        })
        all_emp_leaves = await leave_cursor.to_list(length=100)
        
        leave_map = {} # date -> {"factor": factor, "type": leave_type}
        for l in all_emp_leaves:
            try:
                # Robust day type & half day detection (handles strings, booleans, case differences)
                day_type_str = str(l.get("day_type") or l.get("dayType") or "").strip().lower()
                is_half = (
                    l.get("half_day") == True or 
                    l.get("halfDay") == True or 
                    l.get("half_day") in [True, "true", "True"] or
                    l.get("halfDay") in [True, "true", "True"] or
                    day_type_str in ["half day", "first half", "second half", "half-day"]
                )
                factor = 0.5 if is_half else 1.0
                l_type = str(l.get("type") or l.get("leaveType") or "annual").strip().lower()
                
                # Check both snake_case and camelCase for dates
                raw_start = l.get("start_date") or l.get("startDate")
                raw_end = l.get("end_date") or l.get("endDate")
                
                if not raw_start or not raw_end:
                    continue

                # Try to parse different date formats robustly (handles datetime, date, str)
                def parse_date(val):
                    if not val:
                        return None
                    if isinstance(val, datetime):
                        return val
                    if isinstance(val, date):
                        return datetime.combine(val, datetime.min.time())
                    if not isinstance(val, str):
                        return None
                    val_str = val.strip()
                    if "T" in val_str:
                        val_str = val_str.split("T")[0]
                    elif " " in val_str:
                        val_str = val_str.split(" ")[0]
                    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
                        try:
                            return datetime.strptime(val_str.strip(), fmt)
                        except ValueError:
                            continue
                    return None

                s = parse_date(raw_start)
                e = parse_date(raw_end)
                
                if not s or not e:
                    continue

                # Clean tzinfo to avoid comparison crashes
                if s.tzinfo is not None:
                    s = s.replace(tzinfo=None)
                if e.tzinfo is not None:
                    e = e.replace(tzinfo=None)

                curr = s
                while curr <= e:
                    # Only map if it falls within the requested month
                    if curr.month == month_num and curr.year == year:
                        d_str = curr.strftime("%Y-%m-%d")
                        if d_str in leave_map:
                            if factor > leave_map[d_str]["factor"]:
                                leave_map[d_str] = {"factor": factor, "type": l_type}
                        else:
                            leave_map[d_str] = {"factor": factor, "type": l_type}
                    curr += timedelta(days=1)
                    # Safety break to prevent infinite loops
                    if (curr - s).days > 365:
                        break
            except Exception as ex:
                print(f"Robust leave parse error: {ex}")
                pass

        # 2. Map out actual presence
        att_start_dt_naive = datetime.strptime(start_date_str, "%Y-%m-%d")
        att_end_dt_naive = datetime.strptime(end_date_str, "%Y-%m-%d") + timedelta(days=1)
        att_start_dt_aware = att_start_dt_naive.replace(tzinfo=IST)
        att_end_dt_aware = (att_end_dt_naive - timedelta(seconds=1)).replace(tzinfo=IST)
        
        emp_id_query = [{"employeeId": emp_id}]
        if ObjectId.is_valid(emp_id):
            emp_id_query.append({"employeeId": ObjectId(emp_id)})

        attendance_cursor = db.attendance.find({
            "$and": [
                {"$or": emp_id_query},
                {"$or": [
                    {"date": {"$gte": att_start_dt_naive, "$lt": att_end_dt_naive}},
                    {"date": {"$gte": att_start_dt_aware, "$lte": att_end_dt_aware}}
                ]}
            ]
        })
        attendance_records = await attendance_cursor.to_list(length=100)
        attendance_dates = {
            att["date"].strftime("%Y-%m-%d") if isinstance(att["date"], (date, datetime)) else att["date"]
            for att in attendance_records
        }
        
        # 3. Calculate worked/absent based on priority: Leave > Attendance
        sunday_dates_set = set(sunday_dates)
        holiday_dates_set = {
            h["date"].strftime("%Y-%m-%d") if isinstance(h["date"], (date, datetime)) else h["date"]
            for h in month_holidays if "date" in h
        }
        
        actual_worked_days = 0.0
        half_day_leave_days = 0.0
        full_day_leave_days = 0.0
        monthly_leave_days = 0.0  # Only "annual" type leaves
        paid_monthly_leave_days = 0.0
        unapproved_absent_days = 0.0
        attendance_present_days = 0.0
 
        for d in range(emp_start_day, num_days + 1):
            date_str = f"{year}-{str(month_num).zfill(2)}-{str(d).zfill(2)}"
            
            # We only care about scheduled working days
            if date_str not in sunday_dates_set and date_str not in holiday_dates_set:
                if not emp.get("activelyUsingHRMS", True):
                    attendance_present_days += 1.0
                elif date_str in leave_map:
                    leave_info = leave_map[date_str]
                    factor = leave_info["factor"]
                    l_type = leave_info["type"]
                    
                    if factor == 0.5:
                        half_day_leave_days += 0.5
                        attendance_present_days += 0.5 # Worked the other half of the day
                        if l_type in ["annual", "monthly leave", "monthly_leave"]:
                            monthly_leave_days += 0.5
                        # Half-day monthly leaves are not allowed/counted as paid monthly leaves
                    else:
                        full_day_leave_days += 1.0
                        if l_type in ["annual", "monthly leave", "monthly_leave"]:
                            monthly_leave_days += 1.0
                        if l_type in ["monthly leave", "monthly_leave"]:
                            paid_monthly_leave_days += 1.0
                elif date_str in attendance_dates:
                    attendance_present_days += 1.0
                else:
                    unapproved_absent_days += 1.0
        
        allowed_leaves = float(system_settings.get("allowedMonthlyPaidLeaves", 1.0))
        paid_monthly_leave_days = min(paid_monthly_leave_days, allowed_leaves)
        total_leaves_taken = full_day_leave_days + half_day_leave_days
        
        # Calculate Loss of Pay (LOP) days (excluding paid monthly leaves)
        lop_days = max(0.0, total_leaves_taken - paid_monthly_leave_days) + unapproved_absent_days
        actual_worked_days = attendance_present_days
        deducted_leaves = 0.0
        
        total_working_days = num_days - sundays - unique_holidays
        per_day_gross = salary["monthlyGross"] / total_working_days
        lop_amount = lop_days * per_day_gross
        
        deduction_details = []
        if deducted_leaves > 0:
            deduction_details.append(f"Paid Leave Allowance - {round(deducted_leaves, 1)} day(s) applied")
            
        if lop_days > unapproved_absent_days:
            unpaid_leaves = lop_days - unapproved_absent_days
            deduction_details.append(f"Unpaid Leave - {round(unpaid_leaves, 1)} day(s): ₹{round(unpaid_leaves * per_day_gross, 2)}")
            
        if unapproved_absent_days > 0:
            deduction_details.append(f"Unpaid Absence - {round(unapproved_absent_days, 1)} day(s): ₹{round(unapproved_absent_days * per_day_gross, 2)}")

        # Ad-hoc Bonus/Deductions
        adjustments = await get_bonus_deductions(db, month, year)
        emp_adjustments = [a for a in adjustments if a["employeeId"] == emp_id and a["status"] == "active"]
        
        # Recalculate Sales Targets to ensure fresh data for payroll
        try:
            tgt_month_num = list(calendar.month_name).index(month)
        except ValueError:
            tgt_month_map = {m: i for i, m in enumerate(calendar.month_name)}
            tgt_month_num = tgt_month_map.get(month.capitalize(), 1)
        _, tgt_num_days = calendar.monthrange(year, tgt_month_num)
        
        month_start_str = f"{year}-{tgt_month_num:02d}-01"
        month_end_str = f"{year}-{tgt_month_num:02d}-{tgt_num_days:02d}"

        emp_obj_id = ObjectId(emp_id) if len(emp_id) == 24 else None
        emp_query = [emp_id]
        if emp_obj_id:
            emp_query.append(emp_obj_id)
            
        targets_cursor = db.sales_targets.find({
            "employeeId": {"$in": emp_query},
            "$or": [
                {
                    "month": month,
                    "year": year
                },
                {
                    "type": "Custom",
                    "startDate": {"$lte": month_end_str},
                    "endDate": {"$gte": month_start_str}
                }
            ]
        })
        targets = await targets_cursor.to_list(length=100)
        for t in targets:
            await recalculate_sales_target(
                db, 
                emp_id, 
                month, 
                year, 
                t.get("type", "Monthly"), 
                t.get("week"),
                startDate=t.get("startDate"),
                endDate=t.get("endDate")
            )

        # Fetch Sales Incentives (all targets relevant to this month)
        updated_targets_cursor = db.sales_targets.find({
            "employeeId": {"$in": emp_query},
            "$or": [
                {
                    "month": month,
                    "year": year
                },
                {
                    "type": "Custom",
                    "startDate": {"$lte": month_end_str},
                    "endDate": {"$gte": month_start_str}
                }
            ]
        })
        updated_targets = await updated_targets_cursor.to_list(length=100)
        
        incentive_amount = 0.0
        incentive_details_list = []
        combined_breakdown = []
        for t in updated_targets:
            t_inc = t.get("incentiveAmount", 0.0)
            incentive_amount += t_inc
            
            t_type = t.get("type", "Monthly")
            t_target = t.get("targetAmount", 0.0)
            t_achieved = t.get("currentAchievement", 0.0)
            
            if t_type == "Custom":
                sd_raw = t.get("startDate", "")
                ed_raw = t.get("endDate", "")
                try:
                    sd_dt = datetime.strptime(sd_raw, "%Y-%m-%d")
                    ed_dt = datetime.strptime(ed_raw, "%Y-%m-%d")
                    date_desc = f"{sd_dt.strftime('%d')} to {ed_dt.strftime('%d %b')}"
                except:
                    date_desc = f"{sd_raw} to {ed_raw}"
            elif t_type == "Weekly":
                date_desc = f"Week {t.get('week')} ({month})"
            else:
                date_desc = f"Monthly ({month})"
                
            incentive_details_list.append(
                f"• {date_desc}: Target ₹{int(t_target)}, Achieved ₹{int(t_achieved)} (Incentive: ₹{int(t_inc)})"
            )
            
            t_breakdown = t.get("breakdown", [])
            if t_breakdown:
                combined_breakdown.extend(t_breakdown)
        
        incentive_details_str = "; ".join(incentive_details_list)

        total_bonus = 0
        total_adhoc_deduction = 0
        penalty_total = 0
        for a in emp_adjustments:
            if a["type"] == "bonus":
                total_bonus += a["amount"]
            elif a["type"] == "deduction":
                if "was rejected. Automatic full-day salary deduction applied" in a["reason"] or "Work rejected by TL" in a["reason"]:
                    penalty_total += per_day_gross
                    deduction_details.append(f"{a['reason']}: ₹{round(per_day_gross, 2)}")
                else:
                    total_adhoc_deduction += a["amount"]
                    deduction_details.append(f"{a['reason']}: ₹{a.get('amount', 0)}")
        
        # Add sales incentive to allowances (previously bonus)
        # We will track it in deduction_details but it will be added to allowances field
        if incentive_amount > 0:
            deduction_details.append(f"Sales Incentive: ₹{incentive_amount}")
        
        # Fetch Penalties from Remarks
        try:
            rem_month_num = list(calendar.month_name).index(month)
        except ValueError:
            rem_month_map = {m: i for i, m in enumerate(calendar.month_name)}
            rem_month_num = rem_month_map.get(month.capitalize(), 1)
        
        _, rem_num_days = calendar.monthrange(year, rem_month_num)
        rem_start_dt_naive = datetime(year, rem_month_num, 1)
        rem_end_dt_naive = datetime(year, rem_month_num, rem_num_days, 23, 59, 59)
        rem_start_dt_aware = rem_start_dt_naive.replace(tzinfo=IST)
        rem_end_dt_aware = rem_end_dt_naive.replace(tzinfo=IST)
        
        remark_query = {
            "employeeId": emp_id,
            "isDeleted": {"$nin": [True, "true", "True"]}
        }
        all_emp_remarks = await db.remarks.find(remark_query).to_list(length=1000)
        
        emp_remarks = []
        for mr in all_emp_remarks:
            mr_dt = mr.get("date")
            mr_m = None
            mr_y = None
            if mr_dt:
                try:
                    if isinstance(mr_dt, datetime):
                        mr_m = mr_dt.month
                        mr_y = mr_dt.year
                    elif isinstance(mr_dt, str):
                        if "T" in mr_dt or "-" in mr_dt:
                            parts = mr_dt.split("T")[0].split("-")
                            if len(parts[0]) == 4:
                                mr_y = int(parts[0])
                                mr_m = int(parts[1])
                            else:
                                mr_y = int(parts[2])
                                mr_m = int(parts[1])
                        else:
                            date_parts = mr_dt.split(" ")
                            r_month_name = date_parts[0].replace(",", "")
                            mr_y = int(date_parts[-1])
                            try:
                                mr_m = list(calendar.month_abbr).index(r_month_name)
                            except ValueError:
                                mr_m = list(calendar.month_name).index(r_month_name)
                except Exception:
                    pass
            
            if mr_m == rem_month_num and mr_y == year:
                # Normalize date for sorting
                if isinstance(mr_dt, str):
                    try:
                        if "-" in mr_dt and len(mr_dt.split("-")[0]) != 4:
                            mr["_sort_date"] = datetime.strptime(mr_dt, "%d-%m-%Y")
                        else:
                            from dateutil.parser import parse
                            mr["_sort_date"] = parse(mr_dt)
                    except:
                        mr["_sort_date"] = datetime.min
                elif isinstance(mr_dt, datetime):
                    mr["_sort_date"] = mr_dt
                else:
                    mr["_sort_date"] = datetime.min
                emp_remarks.append(mr)
                
        # Sort by parsed date
        emp_remarks.sort(key=lambda x: x["_sort_date"])
            
        emp_all_time_remarks_cache = {}
        
        for r in emp_remarks:
            remark_type = r.get("type")
            remark_type_lower = remark_type.lower()
            r_date_val = r.get("date")
            r_date_str = r_date_val.strftime("%d-%m-%Y") if isinstance(r_date_val, (date, datetime)) else str(r_date_val)
            
            try:
                r_amount = float(r.get("amount", 0) or 0)
            except:
                r_amount = 0
                
            is_penalty = r_amount > 0 or remark_type in [p["name"] for p in penalty_types] or remark_type == "Late Punch-in"
            
            if is_penalty:
                if remark_type == "Late Punch-in" and not late_punch_deduction_enabled:
                    continue
                    
                if remark_type_lower not in emp_all_time_remarks_cache:
                    try:
                        emp_obj_id = ObjectId(str(emp_id))
                    except:
                        emp_obj_id = str(emp_id)
                    q_all = {
                        "employeeId": {"$in": [str(emp_id), emp_obj_id]},
                        "type": {"$regex": f"^{remark_type}$", "$options": "i"},
                        "isDeleted": {"$nin": [True, "true", "True"]}
                    }
                    all_time_rems = await db.remarks.find(q_all).to_list(length=10000)
                    
                    for atr in all_time_rems:
                        atr_dt = atr.get("date")
                        if isinstance(atr_dt, str):
                            try:
                                if "-" in atr_dt and len(atr_dt.split("-")[0]) != 4:
                                    atr["_sort_date"] = datetime.strptime(atr_dt, "%d-%m-%Y")
                                else:
                                    from dateutil.parser import parse
                                    atr["_sort_date"] = parse(atr_dt)
                            except:
                                atr["_sort_date"] = datetime.min
                        elif isinstance(atr_dt, datetime):
                            atr["_sort_date"] = atr_dt
                        else:
                            atr["_sort_date"] = datetime.min
                    all_time_rems.sort(key=lambda x: x["_sort_date"])
                    emp_all_time_remarks_cache[remark_type_lower] = [str(x.get("id") or x.get("_id")) for x in all_time_rems]
                
                try:
                    r_id_str = str(r.get("id") or r.get("_id"))
                    current_type_rank = emp_all_time_remarks_cache[remark_type_lower].index(r_id_str) + 1
                except ValueError:
                    current_type_rank = 999

                amount_to_deduct = r_amount
                if amount_to_deduct <= 0:
                    amount_to_deduct = next((p["amount"] for p in penalty_types if p["name"] == remark_type), 0)
                
                if remark_type == "Late Punch-in" and amount_to_deduct <= 0:
                    amount_to_deduct = per_day_gross if per_day_gross > 0 else 0
                
                if remark_type == "Late Punch-in":
                    amount_to_deduct = int(round(amount_to_deduct))
                
                if amount_to_deduct > 0:
                    pt_obj = next((p for p in penalty_types if p["name"] == remark_type), None)
                    type_warning_limit = pt_obj.get("warningLimit", 3) if pt_obj else 3
                    
                    if current_type_rank <= type_warning_limit:
                        deduction_details.append(f"Warning: {remark_type} ({r_date_str})")
                    else:
                        penalty_total += amount_to_deduct
                        deduction_details.append(f"{remark_type} ({r_date_str}): ₹{round(amount_to_deduct, 2)}")
        
        # Fetch existing payroll to preserve custom security deposit and return amounts
        existing_payroll = await db.payroll.find_one({"employeeId": emp_id, "month": month, "year": year})
        deposit_deduction = existing_payroll.get("securityDeposit", 0.0) if existing_payroll else 0.0
        returned_deposit = existing_payroll.get("returnedDeposit", 0.0) if existing_payroll else 0.0
        
        if deposit_deduction > 0:
            deduction_details.append(f"Security Deposit: ₹{deposit_deduction}")

        net_salary = (salary["monthlyGross"] - lop_amount + total_bonus + incentive_amount + returned_deposit - total_adhoc_deduction - penalty_total) - (salary["pf"] + salary["esi"] + salary["professionalTax"] + salary["tds"] + deposit_deduction)
        
        payroll_record = {
            "employeeId": emp_id,
            "employeeName": emp["name"],
            "month": month,
            "year": year,
            "totalWorkingDays": int(total_working_days),
            "workedDays": round(actual_worked_days, 1),
            "leaveDays": round(half_day_leave_days + full_day_leave_days - monthly_leave_days, 1),
            "monthlyLeaveDays": round(monthly_leave_days, 1),
            "lopDays": round(lop_days, 1),
            "basicSalary": salary["basic"],
            "allowances": salary["hra"] + salary["conveyance"] + salary["medical"] + salary["specialAllowance"] + incentive_amount,
            "bonus": total_bonus,
            "incentiveAmount": round(incentive_amount, 2),
            "deductions": salary["pf"] + salary["esi"] + salary["professionalTax"] + salary["tds"] + lop_amount + total_adhoc_deduction + penalty_total + deposit_deduction,
            "penalty": penalty_total,
            "securityDeposit": deposit_deduction,
            "returnedDeposit": returned_deposit,
            "netSalary": round(net_salary, 2),
            "status": existing_payroll.get("status", "processed") if existing_payroll else "processed",
            "deductionRemarks": "; ".join(deduction_details),
            "incentiveDetails": incentive_details_str,
            "incentiveBreakdown": combined_breakdown
        }
        
        await db.payroll.update_one(
            {"employeeId": emp_id, "month": month, "year": year},
            {"$set": payroll_record},
            upsert=True
        )
        
        doc = await db.payroll.find_one({"employeeId": emp_id, "month": month, "year": year})
        payroll_results.append(fix_id(doc))
        
    try:
        for p_doc in payroll_results:
            target_emp_id = p_doc.get("employeeId")
            if target_emp_id:
                await create_notification(db, schemas.NotificationCreate(
                    employee_id=target_emp_id,
                    title="Payslip Released",
                    message=f"Your payroll for {month} {year} has been processed. You can view your payslip in the Payroll section.",
                    type="payroll",
                    reference_id=p_doc.get("id")
                ))
    except Exception as e_notif:
        print(f"Error sending payslip notifications: {e_notif}")

    await log_activity(
        db=db,
        action="Payroll Processed",
        performedBy=performed_by,
        userName=user_name,
        details=f"Processed payroll for {month} {year}."
    )

    return payroll_results

def sync_active_bond(data: dict):
    # Parse bondsHistory if present
    bonds = data.get("bondsHistory")
    if bonds is not None:
        def parse_date(val):
            if not val:
                return None
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, date):
                return val
            val_str = str(val).strip().split('T')[0].split(' ')[0]
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(val_str, fmt).date()
                except ValueError:
                    continue
            return None

        # Filter active bonds or sort them
        parsed_bonds = []
        for b in bonds:
            s_date = parse_date(b.get("startDate"))
            e_date = parse_date(b.get("endDate"))
            if s_date and e_date:
                parsed_bonds.append((s_date, e_date, b))
        
        if parsed_bonds:
            # Sort by startDate, endDate
            parsed_bonds.sort(key=lambda x: (x[0], x[1]))
            # Let's find if there is an active one covering today's date
            today = date.today()
            active_bond = None
            for s, e, b in parsed_bonds:
                if b.get("status") == "active" or (s <= today <= e):
                    active_bond = (s, e, b)
            
            # If no active bond matches today's date, pick the latest one in the list
            if not active_bond:
                active_bond = parsed_bonds[-1]
            
            # Update base fields for backward compatibility
            if data.get("hasBond") is not False and data.get("hasBond") != "false" and data.get("hasBond") != "False":
                data["hasBond"] = True
                data["bondStartDate"] = datetime.combine(active_bond[0], datetime.min.time())
                data["bondEndDate"] = datetime.combine(active_bond[1], datetime.min.time())
            else:
                data["hasBond"] = False
                data["bondStartDate"] = datetime.combine(active_bond[0], datetime.min.time())
                data["bondEndDate"] = datetime.combine(active_bond[1], datetime.min.time())
        else:
            data["hasBond"] = False
            data["bondStartDate"] = None
            data["bondEndDate"] = None

async def validate_single_team_leader_per_dept(db, department: str = None, designation: str = None, role: str = None, exclude_employee_id: str = None):
    desig_str = (designation or "").strip().lower()
    role_str = (role or "").strip().lower()
    is_tl = any(k in desig_str or k in role_str for k in ["team leader", "tl", "team lead", "lead", "head"])
    
    if not is_tl or not department or not department.strip():
        return

    dept_clean = department.strip()
    query = {
        "status": {"$ne": "inactive"},
        "department": {"$regex": f"^{re.escape(dept_clean)}$", "$options": "i"},
        "$or": [
            {"designation": {"$regex": "team leader|tl|team lead|lead|head", "$options": "i"}},
            {"role": {"$regex": "team leader|tl|team lead|lead|head", "$options": "i"}}
        ]
    }
    if exclude_employee_id:
        try:
            query["_id"] = {"$ne": ObjectId(exclude_employee_id)}
        except Exception:
            query["_id"] = {"$ne": exclude_employee_id}

    existing_tl = await db.employees.find_one(query)
    if existing_tl:
        tl_name = existing_tl.get("name") or f"{existing_tl.get('firstName', '')} {existing_tl.get('lastName', '')}".strip() or "Employee"
        raise ValueError(f"Department '{dept_clean}' already has an active Team Leader ({tl_name}). Only 1 Team Leader per department is allowed.")

async def create_employee(db, employee: schemas.EmployeeCreate, performed_by: str = "System", user_name: str = "System User"):
    await validate_single_team_leader_per_dept(
        db,
        department=employee.department,
        designation=employee.designation,
        role=employee.role
    )
    is_admin = employee.role and employee.role.lower() == "admin"
    
    if not is_admin:
        # Atomic counter to prevent duplicate IDs under concurrent requests
        counter = await db.counters.find_one_and_update(
            {"_id": "employee_id"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True
        )
        next_id = f"EMP{str(counter['seq']).zfill(3)}"
    else:
        next_id = employee.employeeId
    
    # Calculate full name
    name = f"{employee.firstName} {employee.lastName}"
    if employee.middleName:
        name = f"{employee.firstName} {employee.middleName} {employee.lastName}"
    
    employee_dict = employee.dict()
    employee_dict["name"] = name

    # Ensure sub_department is synced in both naming styles
    sub_dept_val = employee_dict.get("sub_department") or employee_dict.get("subDepartment") or ""
    employee_dict["sub_department"] = sub_dept_val
    employee_dict["subDepartment"] = sub_dept_val

    sync_active_bond(employee_dict)
    
    if next_id:
        employee_dict["employeeId"] = next_id # Override frontend generation unless it's admin with no ID provided
    else:
        # If admin and no ID provided, just don't set or keep whatever is in employee.employeeId
        pass

    
    result = await db.employees.insert_one(employee_dict)
    employee_dict["id"] = str(result.inserted_id)
    if "_id" in employee_dict:
        employee_dict.pop("_id")
    
    if employee.salary is not None and employee.salary > 0:
        await sync_employee_salary_to_structure(db, employee_dict["id"], employee.salary)
        
    await log_activity(
        db=db,
        action="Employee Created",
        performedBy=performed_by,
        userName=user_name,
        details=f"Employee '{name}' (ID: {next_id or 'N/A'}) was created.",
        employeeId=employee_dict["id"]
    )

    try:
        await ws_manager.broadcast_all("data_refresh", {"entity": "employees"})
    except Exception:
        pass
        
    return employee_dict


# Department CRUD
async def get_departments(db, skip: int = 0, limit: int = 100):
    cursor = db.departments.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    results = []
    for row in rows:
        row = fix_id(row)
        # Automatic employee count based on department name
        row["employeeCount"] = await db.employees.count_documents({"department": row["name"]})
        results.append(row)
    return results

async def create_department(db, department: schemas.DepartmentCreate):
    department_dict = department.dict()
    result = await db.departments.insert_one(department_dict)
    department_dict["id"] = str(result.inserted_id)
    if "_id" in department_dict:
        department_dict.pop("_id")
    return department_dict

async def update_department(db, department_id: str, department_update: schemas.DepartmentUpdate):
    update_data = department_update.dict(exclude_unset=True)
    
    # We need the old department name to cascade changes
    old_doc = await db.departments.find_one({"_id": ObjectId(department_id)})
    
    await db.departments.update_one(
        {"_id": ObjectId(department_id)},
        {"$set": update_data}
    )
    
    if old_doc and "name" in update_data and old_doc.get("name") != update_data["name"]:
        old_name = old_doc.get("name")
        new_name = update_data["name"]
        # cascade update to employees, clients, tasks, projects
        await db.employees.update_many({"department": old_name}, {"$set": {"department": new_name}})
        await db.clients.update_many({"department": old_name}, {"$set": {"department": new_name}})
        await db.tasks.update_many({"department": old_name}, {"$set": {"department": new_name}})
        await db.projects.update_many({"department": old_name}, {"$set": {"department": new_name}})
        await db.designations.update_many({"department": old_name}, {"$set": {"department": new_name}})
        await db.sub_departments.update_many({"department": old_name}, {"$set": {"department": new_name}})
        
    updated_doc = await db.departments.find_one({"_id": ObjectId(department_id)})
    return fix_id(updated_doc)

async def delete_department(db, department_id: str):
    await db.departments.delete_one({"_id": ObjectId(department_id)})
    return True

# SubDepartment CRUD
async def get_sub_departments(db, skip: int = 0, limit: int = 100):
    cursor = db.sub_departments.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    result = []
    for row in rows:
        r = fix_id(row)
        if 'department' not in r or r['department'] is None:
            r['department'] = ''
        result.append(r)
    return result

async def create_sub_department(db, sub_department: schemas.SubDepartmentCreate):
    sub_department_dict = sub_department.dict()
    result = await db.sub_departments.insert_one(sub_department_dict)
    sub_department_dict["id"] = str(result.inserted_id)
    if "_id" in sub_department_dict:
        sub_department_dict.pop("_id")
    return sub_department_dict

async def update_sub_department(db, sub_department_id: str, sub_department_update: schemas.SubDepartmentUpdate):
    update_data = sub_department_update.dict(exclude_unset=True)
    
    old_doc = await db.sub_departments.find_one({"_id": ObjectId(sub_department_id)})
    
    await db.sub_departments.update_one(
        {"_id": ObjectId(sub_department_id)},
        {"$set": update_data}
    )
    
    if old_doc and "name" in update_data and old_doc.get("name") != update_data["name"]:
        old_name = old_doc.get("name")
        new_name = update_data["name"]
        await db.employees.update_many({"sub_department": old_name}, {"$set": {"sub_department": new_name}})
        await db.designations.update_many({"sub_department": old_name}, {"$set": {"sub_department": new_name}})
        
    updated_doc = await db.sub_departments.find_one({"_id": ObjectId(sub_department_id)})
    return fix_id(updated_doc)

async def delete_sub_department(db, sub_department_id: str):
    await db.sub_departments.delete_one({"_id": ObjectId(sub_department_id)})
    return True


# Designation CRUD
async def get_designations(db, skip: int = 0, limit: int = 100):
    cursor = db.designations.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    result = []
    for row in rows:
        r = fix_id(row)
        if 'sub_department' not in r or r['sub_department'] is None:
            r['sub_department'] = ''
        result.append(r)
    return result

async def create_designation(db, designation: schemas.DesignationCreate):
    designation_dict = designation.dict()
    result = await db.designations.insert_one(designation_dict)
    designation_dict["id"] = str(result.inserted_id)
    if "_id" in designation_dict:
        designation_dict.pop("_id")

    title = designation_dict.get("title")
    if title:
        # Automatically generate this new designation's preset across ALL existing department presets
        dept_presets = await db.permission_presets.find({"presetType": "department"}).to_list(length=1000)
        for dept_preset in dept_presets:
            dept_name = dept_preset.get("department") or dept_preset.get("name")
            if not dept_name:
                continue
                
            permissions = dept_preset.get("permissions", [])
            preset_name = f"{dept_name} - {title}"
            
            existing_preset = await db.permission_presets.find_one({
                "presetType": "designation",
                "department": dept_name,
                "designation": title
            })
            
            if not existing_preset:
                await db.permission_presets.insert_one({
                    "name": preset_name,
                    "description": f"Designation preset for {title} in {dept_name}",
                    "presetType": "designation",
                    "department": dept_name,
                    "designation": title,
                    "permissions": permissions
                })

    return designation_dict

async def update_designation(db, designation_id: str, designation_update: schemas.DesignationUpdate):
    old_doc = await db.designations.find_one({"_id": ObjectId(designation_id)})
    update_data = designation_update.dict(exclude_unset=True)
    await db.designations.update_one(
        {"_id": ObjectId(designation_id)},
        {"$set": update_data}
    )
    updated_doc = await db.designations.find_one({"_id": ObjectId(designation_id)})

    if old_doc and updated_doc:
        old_dept = old_doc.get("department")
        old_title = old_doc.get("title")
        new_dept = updated_doc.get("department")
        new_title = updated_doc.get("title")
        
        if old_dept != new_dept or old_title != new_title:
            await db.permission_presets.update_many(
                {"presetType": "designation", "department": old_dept, "designation": old_title},
                {"$set": {
                    "name": f"{new_dept} - {new_title}",
                    "department": new_dept,
                    "designation": new_title
                }}
            )

    return fix_id(updated_doc)

async def delete_designation(db, designation_id: str):
    desig = await db.designations.find_one({"_id": ObjectId(designation_id)})
    if desig:
        dept = desig.get("department")
        title = desig.get("title")
        if dept and title:
            await db.permission_presets.delete_many({
                "presetType": "designation",
                "department": dept,
                "designation": title
            })
    await db.designations.delete_one({"_id": ObjectId(designation_id)})
    return True

# Generic CRUD Helpers
async def get_items(db, collection_name: str, skip: int = 0, limit: int = 100):
    cursor = db[collection_name].find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def create_item(db, collection_name: str, item_data: dict):
    result = await db[collection_name].insert_one(item_data)
    item_data["id"] = str(result.inserted_id)
    if "_id" in item_data:
        item_data.pop("_id")
    return item_data

async def update_item(db, collection_name: str, item_id: str, update_data: dict):
    await db[collection_name].update_one(
        {"_id": ObjectId(item_id)},
        {"$set": update_data}
    )
    updated_doc = await db[collection_name].find_one({"_id": ObjectId(item_id)})
    return fix_id(updated_doc)

async def delete_item(db, collection_name: str, item_id: str):
    await db[collection_name].delete_one({"_id": ObjectId(item_id)})
    return True

# Specific Collection Implementations
async def get_companies(db, skip: int = 0, limit: int = 100): return await get_items(db, "companies", skip, limit)
async def create_company(db, company: schemas.CompanyCreate): return await create_item(db, "companies", company.dict())
async def update_company(db, company_id: str, update: schemas.CompanyUpdate): return await update_item(db, "companies", company_id, update.dict(exclude_unset=True))
async def delete_company(db, company_id: str): return await delete_item(db, "companies", company_id)

async def get_roles(db, skip: int = 0, limit: int = 100): return await get_items(db, "roles", skip, limit)
async def create_role(db, role: schemas.RoleCreate): return await create_item(db, "roles", role.dict())
async def update_role(db, role_id: str, update: schemas.RoleUpdate): return await update_item(db, "roles", role_id, update.dict(exclude_unset=True))
async def delete_role(db, role_id: str): return await delete_item(db, "roles", role_id)

async def get_relations(db, skip: int = 0, limit: int = 100): return await get_items(db, "relations", skip, limit)
async def create_relation(db, relation: schemas.RelationCreate): return await create_item(db, "relations", relation.dict())
async def update_relation(db, relation_id: str, update: schemas.RelationUpdate): return await update_item(db, "relations", relation_id, update.dict(exclude_unset=True))
async def delete_relation(db, relation_id: str): return await delete_item(db, "relations", relation_id)


async def get_job_openings(db, skip: int = 0, limit: int = 100): return await get_items(db, "job_openings", skip, limit)
async def create_job_opening(db, job: schemas.JobOpeningCreate): return await create_item(db, "job_openings", job.dict())
async def update_job_opening(db, job_id: str, update: schemas.JobOpeningUpdate): return await update_item(db, "job_openings", job_id, update.dict(exclude_unset=True))
async def delete_job_opening(db, job_id: str): return await delete_item(db, "job_openings", job_id)

async def get_applications(db, skip: int = 0, limit: int = 100): return await get_items(db, "applications", skip, limit)
async def create_application(db, app: schemas.ApplicationCreate): 
    app_dict = app.dict()
    performedBy = app_dict.pop("performedBy", "Unknown")
    userName = app_dict.pop("userName", "Unknown User")
    
    result = await create_item(db, "applications", app_dict)
    
    # Increment the application count for the job opening
    job_title = app_dict.get("jobTitle")
    if job_title:
        await db.job_openings.update_one(
            {"title": job_title},
            {"$inc": {"applications": 1}}
        )
    
    await log_activity(db, "Created", performedBy, userName, f"Candidate '{app_dict.get('candidateName')}' was added to the system.", applicationId=result["id"])
    
    return result

async def update_application(db, app_id: str, update: schemas.ApplicationUpdate):
    # Fetch existing to check for status change and job title change
    existing = await db.applications.find_one({"_id": ObjectId(app_id)})
    update_dict = update.dict(exclude_unset=True)
    
    performedBy = update_dict.pop("performedBy", "Unknown")
    userName = update_dict.pop("userName", "Unknown User")
    
    await db.applications.update_one(
        {"_id": ObjectId(app_id)},
        {"$set": update_dict}
    )
    
    if existing:
        details = []
        if "status" in update_dict and existing.get("status") != update_dict["status"]:
            details.append(f"Status changed from '{existing.get('status')}' to '{update_dict['status']}'")
            
            # Map hiring board stage back to referral status
            STAGE_TO_REFERRAL_STATUS = {
                "new": "Contacted",
                "tl_approved": "TL Approved",
                "interview": "Scheduled Interview",
                "selected": "Selected",
                "rejected": "Rejected"
            }
            ref_status = STAGE_TO_REFERRAL_STATUS.get(update_dict["status"])
            if ref_status:
                await db.referrals.update_many(
                    {
                        "candidateName": existing.get("candidateName"),
                        "phone": existing.get("phone")
                    },
                    {"$set": {"status": ref_status}}
                )

            # Auto close Job Opening when candidate is selected
            if update_dict["status"] == "selected":
                job_title = update_dict.get("jobTitle") or existing.get("jobTitle")
                if job_title:
                    await db.job_openings.update_one(
                        {"title": job_title},
                        {"$set": {"status": "closed"}}
                    )
            # Auto reopen Job Opening when candidate is moved from Selected to Rejected (or other stages)
            elif existing.get("status") == "selected" and update_dict["status"] != "selected":
                job_title = update_dict.get("jobTitle") or existing.get("jobTitle")
                if job_title:
                    await db.job_openings.update_one(
                        {"title": job_title},
                        {"$set": {"status": "open"}}
                    )

        # Handle job title changes and update counts
        if "jobTitle" in update_dict and existing.get("jobTitle") != update_dict["jobTitle"]:
            old_job = existing.get("jobTitle")
            new_job = update_dict["jobTitle"]
            if old_job:
                await db.job_openings.update_one({"title": old_job}, {"$inc": {"applications": -1}})
            if new_job:
                await db.job_openings.update_one({"title": new_job}, {"$inc": {"applications": 1}})

        if "interviewerId" in update_dict and existing.get("interviewerId") != update_dict["interviewerId"]:
            details.append(f"Interviewer assigned: {update_dict.get('interviewerName')}")
        if "interviewDate" in update_dict and existing.get("interviewDate") != update_dict["interviewDate"]:
            details.append(f"Interview scheduled for {update_dict['interviewDate']} at {update_dict.get('interviewTime')}")
            
        if details:
            log_details = f"Candidate '{existing.get('candidateName')}': " + ", ".join(details)
            await log_activity(db, "Updated", performedBy, userName, log_details, applicationId=app_id)
    
    # If moved to interview and has an interviewer, notify them
    if existing and update_dict.get("status") == "interview" and existing.get("status") != "interview":
        interviewer_id = update_dict.get("interviewerId") or existing.get("interviewerId")
        if interviewer_id:
            await create_notification(db, schemas.NotificationCreate(
                employee_id=interviewer_id,
                title="New Interview Assigned",
                message=f"You have been assigned an interview for {update_dict.get('candidateName') or existing.get('candidateName')} on {update_dict.get('interviewDate') or existing.get('interviewDate')} at {update_dict.get('interviewTime') or existing.get('interviewTime')}.",
                type="recruitment",
                created_at=get_now().strftime("%d-%m-%Y %H:%M")
            ))
            
    updated_doc = await db.applications.find_one({"_id": ObjectId(app_id)})
    return fix_id(updated_doc)
async def delete_application(db, app_id: str):
    existing = await db.applications.find_one({"_id": ObjectId(app_id)})
    if existing:
        job_title = existing.get("jobTitle")
        if job_title:
            await db.job_openings.update_one(
                {"title": job_title},
                {"$inc": {"applications": -1}}
            )
    return await delete_item(db, "applications", app_id)

async def get_interns(db, skip: int = 0, limit: int = 100): return await get_items(db, "interns", skip, limit)
async def create_intern(db, intern: schemas.InternCreate): return await create_item(db, "interns", intern.dict())
async def update_intern(db, intern_id: str, update: schemas.InternUpdate): return await update_item(db, "interns", intern_id, update.dict(exclude_unset=True))
async def delete_intern(db, intern_id: str): return await delete_item(db, "interns", intern_id)

async def get_assets(db, skip: int = 0, limit: int = 100):
    cursor = db.assets.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    results = []
    for row in rows:
        row = fix_id(row)
        # Compatibility mapping
        if "type" in row and "category" not in row:
            row["category"] = row.pop("type")
        if "assetId" not in row:
            row["assetId"] = f"ASSET-{row['id'][-4:].upper()}"
        results.append(row)
    return results
async def create_asset(db, asset: schemas.AssetCreate):
    asset_dict = asset.dict()
    performedBy = asset_dict.pop("performedBy", "System")
    userName = asset_dict.pop("userName", "System User")
    
    result = await create_item(db, "assets", asset_dict)
    await log_activity(db, "Created", performedBy, userName, f"Asset '{asset_dict['name']}' ({asset_dict.get('assetId', 'N/A')}) was created and added to inventory.", assetId=result["id"])
    return result

async def update_asset(db, asset_id: str, update: schemas.AssetUpdate):
    existing = await db.assets.find_one({"_id": ObjectId(asset_id)})
    update_dict = update.dict(exclude_unset=True)
    performedBy = update_dict.pop("performedBy", "System")
    userName = update_dict.pop("userName", "System User")
    
    await db.assets.update_one(
        {"_id": ObjectId(asset_id)},
        {"$set": update_dict}
    )
    
    if existing:
        changes = []
        if "status" in update_dict and existing.get("status") != update_dict["status"]:
            changes.append(f"Status changed from '{existing.get('status')}' to '{update_dict['status']}'")
        if "assignedTo" in update_dict and existing.get("assignedTo") != update_dict["assignedTo"]:
            old_assign = existing.get("assignedTo") or "Unassigned"
            new_assign = update_dict["assignedTo"] or "Unassigned"
            changes.append(f"Assignment changed from '{old_assign}' to '{new_assign}'")
        if "condition" in update_dict and existing.get("condition") != update_dict["condition"]:
            changes.append(f"Condition updated from '{existing.get('condition')}' to '{update_dict['condition']}'")
        if "location" in update_dict and existing.get("location") != update_dict["location"]:
            changes.append(f"Location changed from '{existing.get('location')}' to '{update_dict['location']}'")
            
        if not changes:
            changes.append("General asset details updated")
            
        log_details = f"Asset '{existing.get('name')}' ({existing.get('assetId', 'N/A')}): " + ", ".join(changes)
        await log_activity(db, "Updated", performedBy, userName, log_details, assetId=asset_id)
        
    updated_doc = await db.assets.find_one({"_id": ObjectId(asset_id)})
    return fix_id(updated_doc)

async def delete_asset(db, asset_id: str, performedBy: str = None, userName: str = None):
    existing = await db.assets.find_one({"_id": ObjectId(asset_id)})
    asset_name = existing.get("name", "Unknown Asset") if existing else "Unknown Asset"
    asset_code = existing.get("assetId", "N/A") if existing else "N/A"
    
    result = await delete_item(db, "assets", asset_id)
    if result:
        await log_activity(db, "Deleted", performedBy or "System", userName or "System User", f"Asset '{asset_name}' ({asset_code}) was deleted from inventory.", assetId=asset_id)
        if existing and existing.get("category"):
            await db.asset_categories.update_one(
                {"name": existing.get("category")},
                {"$inc": {"totalItems": -1}}
            )
    return result

async def get_expense_claims(db, skip: int = 0, limit: int = 100): return await get_items(db, "expense_claims", skip, limit)
async def create_expense_claim(db, claim: schemas.ExpenseClaimCreate): return await create_item(db, "expense_claims", claim.dict())
async def update_expense_claim(db, claim_id: str, update: schemas.ExpenseClaimUpdate): return await update_item(db, "expense_claims", claim_id, update.dict(exclude_unset=True))
async def delete_expense_claim(db, claim_id: str): return await delete_item(db, "expense_claims", claim_id)

async def get_holidays(db, skip: int = 0, limit: int = 100): return await get_items(db, "holidays", skip, limit)
async def create_holiday(db, holiday: schemas.HolidayCreate): return await create_item(db, "holidays", holiday.dict())
async def create_holidays_bulk(db, payload: schemas.HolidayBulkCreate):
    holidays_data = [h.dict() for h in payload.holidays]
    if not holidays_data:
        return {"inserted": 0}
        
    # Find the years of the holidays being inserted
    years = set()
    for h_data in holidays_data:
        date_val = h_data.get("date")
        if isinstance(date_val, datetime):
            years.add(date_val.year)
        elif isinstance(date_val, date):
            years.add(date_val.year)
        elif isinstance(date_val, str):
            try:
                # Basic parsing assuming it starts with year like YYYY-MM-DD
                years.add(int(date_val[:4]))
            except ValueError:
                pass
                
    # Delete existing National public holidays for these years to prevent duplicates
    for year in years:
        year_str = str(year)
        
        # 1. Delete if date is stored as datetime
        await db.holidays.delete_many({
            "type": "National",
            "$or": [
                {"company": ""},
                {"company": None},
                {"company": {"$exists": False}}
            ],
            "date": {"$gte": datetime(year, 1, 1), "$lte": datetime(year, 12, 31, 23, 59, 59)}
        })
        
        # 2. Delete if date is stored as string
        await db.holidays.delete_many({
            "type": "National",
            "$or": [
                {"company": ""},
                {"company": None},
                {"company": {"$exists": False}}
            ],
            "date": {"$type": "string", "$regex": f"^{year_str}"}
        })
        
    # using insert_many directly from Motor on our timestamped db
    result = await db.holidays.insert_many(holidays_data)
    return {"inserted": len(result.inserted_ids)}
async def update_holiday(db, holiday_id: str, update: schemas.HolidayUpdate): return await update_item(db, "holidays", holiday_id, update.dict(exclude_unset=True))
async def delete_holiday(db, holiday_id: str): return await delete_item(db, "holidays", holiday_id)
async def delete_all_holidays(db): return await db.holidays.delete_many({})

async def get_kpi_records(db, skip: int = 0, limit: int = 100): return await get_items(db, "kpi_records", skip, limit)
async def create_kpi_record(db, kpi: schemas.KPICreate): return await create_item(db, "kpi_records", kpi.dict())
async def update_kpi_record(db, kpi_id: str, update: schemas.KPIUpdate): return await update_item(db, "kpi_records", kpi_id, update.dict(exclude_unset=True))
async def delete_kpi_record(db, kpi_id: str): return await delete_item(db, "kpi_records", kpi_id)

async def get_reviews(db, employee_id: str = None, skip: int = 0, limit: int = 100):
    query = {}
    if employee_id:
        query["employeeId"] = employee_id
    cursor = db.reviews.find(query).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]
async def create_review(db, review: schemas.ReviewCreate): 
    review_dict = review.dict()
    if not review_dict.get("date"):
        review_dict["date"] = get_now().strftime("%Y-%m-%d")
    updated_by = review_dict.pop("updatedBy", "Unknown User") or "Unknown User"
    review_dict["logs"] = [{
        "timestamp": datetime.now(IST).isoformat(),
        "action": "Remark created",
        "details": f"Summary: '{review_dict.get('summary')}', Rating: {review_dict.get('rating')} stars",
        "userName": updated_by
    }]
    return await create_item(db, "reviews", review_dict)

async def update_review(db, review_id: str, update: schemas.ReviewUpdate):
    if not ObjectId.is_valid(review_id):
        return None
        
    existing = await db.reviews.find_one({"_id": ObjectId(review_id)})
    if not existing:
        return None
        
    update_data = update.dict(exclude_unset=True)
    if hasattr(update, "adminReply") and update.adminReply is not None:
        update_data["adminReply"] = update.adminReply
    if hasattr(update, "query") and update.query is not None:
        update_data["query"] = update.query
    changes = []
    updated_by = update_data.pop("updatedBy", "Unknown User") or "Unknown User"
    
    for key, val in update_data.items():
        if key not in ["logs", "id", "_id", "updated_at", "created_at", "updatedAt", "createdAt"]:
            old_val = existing.get(key)
            if old_val != val:
                if key == "summary":
                    changes.append(f"Summary updated to '{val}'")
                elif key == "rating":
                    changes.append(f"Rating updated to {val} stars")
                else:
                    changes.append(f"'{key}' updated to '{val}'")
                
    if changes:
        logs = existing.get("logs")
        if not logs:
            orig_date = existing.get("date")
            if hasattr(orig_date, "isoformat"):
                timestamp = orig_date.isoformat()
            elif isinstance(orig_date, str):
                timestamp = orig_date
            else:
                timestamp = datetime.now(IST).isoformat()
                
            creation_log = {
                "timestamp": timestamp,
                "action": "Remark created",
                "details": f"Remark created. Summary: '{existing.get('summary')}', Rating: {existing.get('rating')} stars",
                "userName": "System"
            }
            logs = [creation_log]
        else:
            logs = list(logs)
            
        new_log = {
            "timestamp": datetime.now(IST).isoformat(),
            "action": "Remark updated",
            "details": ", ".join(changes),
            "userName": updated_by
        }
        logs.append(new_log)
        update_data["logs"] = logs
        
    await db.reviews.update_one(
        {"_id": ObjectId(review_id)},
        {"$set": update_data}
    )
    updated = await db.reviews.find_one({"_id": ObjectId(review_id)})
    return fix_id(updated)
async def delete_review(db, review_id: str): return await delete_item(db, "reviews", review_id)

async def get_remarks(db, skip: int = 0, limit: int = 100):
    items = await get_items(db, "remarks", skip, limit)
    # Enrich with penalty amount
    penalty_types = await get_penalty_types(db)

    # Cache salary structures to avoid repeated DB calls
    salary_cache = {}
    # Cache for employee month remarks to compute warnings
    emp_month_remarks_cache = {}

    for item in items:
        remark_type_lower = item.get("type", "").lower() if item.get("type") else ""
        p_amount = next((p["amount"] for p in penalty_types if p["name"].lower() == remark_type_lower), 0)
        
        month_num = None
        r_year = None
        emp_id = item.get("employeeId")
        
        # Parse date to find month and year
        itm_date = item.get("date")
        if itm_date:
            try:
                if isinstance(itm_date, datetime):
                    month_num = itm_date.month
                    r_year = itm_date.year
                elif isinstance(itm_date, str):
                    if "T" in itm_date or "-" in itm_date:
                        parts = itm_date.split("T")[0].split("-")
                        if len(parts[0]) == 4:
                            r_year = int(parts[0])
                            month_num = int(parts[1])
                        else:
                            r_year = int(parts[2])
                            month_num = int(parts[1])
                    else:
                        date_parts = itm_date.split(" ")
                        r_month_name = date_parts[0].replace(",", "")
                        r_year = int(date_parts[-1])
                        try:
                            month_num = list(calendar.month_abbr).index(r_month_name)
                        except ValueError:
                            month_num = list(calendar.month_name).index(r_month_name)
            except Exception:
                pass

        # Calculate one-day salary for Late Punch-in if amount is 0
        if remark_type_lower == "late punch-in" and p_amount == 0 and month_num and r_year:
            if emp_id not in salary_cache:
                salary_cache[emp_id] = await get_salary_structure_by_employee(db, emp_id)
            
            salary_struct = salary_cache[emp_id]
            if salary_struct:
                try:
                    _, num_days = calendar.monthrange(r_year, month_num)
                    sundays = sum(1 for d in range(1, num_days + 1) if calendar.weekday(r_year, month_num, d) == 6)
                    total_working_days = max(1, num_days - sundays)
                    p_amount = int(round(salary_struct["monthlyGross"] / total_working_days))
                except Exception as e:
                    print(f"Error calculating p_amount in remarks list: {e}")
                    p_amount = int(round(salary_struct["monthlyGross"] / 30))
        
        is_warning = False
        remark_type = item.get("type")
        pt_names = [p["name"].lower() for p in penalty_types]
        is_in_penalty_types = remark_type_lower in pt_names
        is_penalty = p_amount > 0 or is_in_penalty_types or remark_type_lower == "late punch-in"
        
        if (is_in_penalty_types or remark_type_lower == "late punch-in") and emp_id:
            cache_key = (str(emp_id), remark_type.lower())
            if cache_key not in emp_month_remarks_cache:
                try:
                    emp_obj_id = ObjectId(str(emp_id))
                except:
                    emp_obj_id = str(emp_id)

                q = {
                    "employeeId": {"$in": [str(emp_id), emp_obj_id]},
                    "type": {"$regex": f"^{remark_type}$", "$options": "i"},
                    "isDeleted": {"$nin": [True, "true", "True"]}
                }
                all_remarks = await db.remarks.find(q).to_list(length=10000)
                
                for mr in all_remarks:
                    mr_dt = mr.get("date")
                    if isinstance(mr_dt, str):
                        try:
                            if "-" in mr_dt and len(mr_dt.split("-")[0]) != 4:
                                mr["_sort_date"] = datetime.strptime(mr_dt, "%d-%m-%Y")
                            else:
                                from dateutil.parser import parse
                                mr["_sort_date"] = parse(mr_dt)
                        except:
                            mr["_sort_date"] = datetime.min
                    elif isinstance(mr_dt, datetime):
                        mr["_sort_date"] = mr_dt
                    else:
                        mr["_sort_date"] = datetime.min
                
                all_remarks.sort(key=lambda x: x["_sort_date"])
                emp_month_remarks_cache[cache_key] = [str(r.get("id") or r.get("_id")) for r in all_remarks]
                
            all_remark_ids = emp_month_remarks_cache[cache_key]
            try:
                item_id = item.get("id") or item.get("_id")
                rank = all_remark_ids.index(str(item_id)) + 1
            except ValueError:
                rank = 999
                
            pt_obj = next((p for p in penalty_types if p["name"].lower() == remark_type_lower), None)
            type_warning_limit = pt_obj.get("warningLimit", 3) if pt_obj else 3
            if rank <= type_warning_limit:
                is_warning = True

        if is_warning:
            item["amount"] = 0
            item["computedAmount"] = 0
        else:
            item["amount"] = p_amount
            item["computedAmount"] = p_amount
        item["isWarning"] = is_warning
        
    return items
async def create_remark(db, remark: schemas.RemarkCreate, performed_by: str = "System", user_name: str = "System User"): 
    remark_dict = remark.dict()
    if not remark_dict.get("date"):
        remark_dict["date"] = get_now().strftime("%d-%m-%Y")
    res = await create_item(db, "remarks", remark_dict)
    if res:
        await log_activity(
            db=db,
            action="Remark Created",
            performedBy=performed_by,
            userName=user_name,
            details=f"Remark of type '{remark_dict.get('type')}' was created for '{remark_dict.get('employeeName')}' by '{remark_dict.get('addedBy', 'System')}' details: {remark_dict.get('details')}",
            remarkId=res.get("id")
        )
    return res

async def update_remark(db, remark_id: str, update: schemas.RemarkUpdate, performed_by: str = "System", user_name: str = "System User"): 
    existing = await db.remarks.find_one({"_id": ObjectId(remark_id)})
    update_data = update.dict(exclude_unset=True)
    res = await update_item(db, "remarks", remark_id, update_data)
    if existing and res:
        log_details, diffs = format_field_changes(existing, update_data, f"Remark for '{existing.get('employeeName')}'")
        if diffs:
            await log_activity(
                db=db,
                action="Remark Updated",
                performedBy=performed_by,
                userName=user_name,
                details=log_details,
                diffs=diffs,
                remarkId=remark_id
            )
    return res

async def delete_remark(db, remark_id: str, performed_by: str = "System", user_name: str = "System User"):
    existing = await db.remarks.find_one({"_id": ObjectId(remark_id)})
    if existing:
        await log_activity(
            db=db,
            action="Remark Deleted",
            performedBy=performed_by,
            userName=user_name,
            details=f"Remark of type '{existing.get('type')}' for '{existing.get('employeeName')}' was deleted (soft delete).",
            remarkId=remark_id
        )
    await db.remarks.update_one({"_id": ObjectId(remark_id)}, {"$set": {"isDeleted": True}})
    return True

async def restore_remark(db, remark_id: str, performed_by: str = "System", user_name: str = "System User"):
    existing = await db.remarks.find_one({"_id": ObjectId(remark_id)})
    if existing:
        await log_activity(
            db=db,
            action="Remark Restored",
            performedBy=performed_by,
            userName=user_name,
            details=f"Remark of type '{existing.get('type')}' for '{existing.get('employeeName')}' was restored.",
            remarkId=remark_id
        )
    await db.remarks.update_one({"_id": ObjectId(remark_id)}, {"$set": {"isDeleted": False}})
    return True

async def permanently_delete_remark(db, remark_id: str, performed_by: str = "System", user_name: str = "System User"):
    existing = await db.remarks.find_one({"_id": ObjectId(remark_id)})
    if existing:
        await log_activity(
            db=db,
            action="Remark Permanently Deleted",
            performedBy=performed_by,
            userName=user_name,
            details=f"Remark of type '{existing.get('type')}' for '{existing.get('employeeName')}' was permanently deleted.",
            remarkId=remark_id
        )
    return await delete_item(db, "remarks", remark_id)

async def get_events(db, skip: int = 0, limit: int = 100): return await get_items(db, "events", skip, limit)
async def create_event(db, event: schemas.EventCreate): return await create_item(db, "events", event.dict())
async def update_event(db, event_id: str, update: schemas.EventUpdate): return await update_item(db, "events", event_id, update.dict(exclude_unset=True))
async def delete_event(db, event_id: str): return await delete_item(db, "events", event_id)

async def authenticate_user(db, login_data: schemas.LoginRequest):
    user = await db.employees.find_one({"email": login_data.email})
    if not user:
        return None
        
    stored_password = user.get("password", "")
    password_match = (stored_password == login_data.password)
            
    if password_match:
        user_id = str(user["_id"])
        
        user_fixed = fix_id(user)
        
        # Fetch permissions
        permissions_doc = await db.user_permissions.find_one({"employeeId": user_id})
        if permissions_doc:
            user_fixed["permissions"] = fix_id(permissions_doc).get("permissions", [])
        else:
            user_fixed["permissions"] = []
            
        # Generate JWT token
        token = auth.create_access_token(data={"sub": user_id, "role": user.get("role", "")})
        
        # The frontend expects {user: ...} right now, we'll wrap it in main.py
        # Actually main.py returns {"message": "...", "user": user}
        # Let's add token to the returned object
        user_fixed["token"] = token
            
        return user_fixed
    return None


async def _apply_punch_out_to_record(db, record: dict, close_dt: datetime, auto_remark: Optional[str] = None):
    """Close an open attendance record at close_dt. Works with raw MongoDB docs."""
    start_time_str = _resolve_punch_start_time(record)
    if not start_time_str:
        now_time_str = close_dt.strftime("%H:%M:%S")
        await db.attendance.update_one(
            {"_id": record["_id"]},
            {"$set": {
                "checkOut": now_time_str,
                "status": "Logged",
                "workHours": "0h 0m",
                "accumulatedWorkSeconds": 0,
                "remarks": auto_remark or record.get("remarks") or "-",
            }}
        )
        return await db.attendance.find_one({"_id": record["_id"]})

    check_in_time = parse_datetime(record['date'], start_time_str)
    if close_dt < check_in_time:
        close_dt = check_in_time + timedelta(minutes=1)

    breaks = record.get("breaks", [])
    total_break_seconds = 0
    updated_breaks = []

    for b in breaks:
        b_copy = dict(b)
        b_start_str = b_copy.get("startTime")
        if b_start_str and not _is_placeholder_time(b_start_str):
            b_start = parse_datetime(record['date'], b_start_str)
            if b_start >= check_in_time:
                b_end_str = b_copy.get("endTime")
                if b_end_str and not _is_placeholder_time(b_end_str):
                    b_end = parse_datetime(record['date'], b_end_str)
                    if b_end < b_start:
                        b_end += timedelta(days=1)
                    break_dur = (b_end - b_start).total_seconds()
                else:
                    b_end = close_dt
                    break_dur = (b_end - b_start).total_seconds()
                    b_copy["endTime"] = close_dt.strftime("%H:%M:%S")
                    b_copy["duration"] = f"{int(break_dur // 60)}m"
                total_break_seconds += break_dur
        updated_breaks.append(b_copy)

    raw_session_seconds = (close_dt - check_in_time).total_seconds()
    session_work_seconds = max(0.0, raw_session_seconds - total_break_seconds)

    accumulated_seconds = record.get("accumulatedWorkSeconds") or 0
    total_seconds = accumulated_seconds + session_work_seconds

    hours, remainder = divmod(int(total_seconds), 3600)
    minutes, _ = divmod(remainder, 60)
    work_hours = f"{hours}h {minutes}m"
    now_time_str = close_dt.strftime("%H:%M:%S")

    punches = record.get("punches", [])
    update_data = {
        "checkOut": now_time_str,
        "workHours": work_hours,
        "status": "Logged",
        "accumulatedWorkSeconds": total_seconds,
        "breaks": updated_breaks,
    }

    if punches:
        punches_copy = [dict(p) for p in punches]
        if punches_copy[-1].get("punchOut") in [None, ""]:
            punches_copy[-1]["punchOut"] = now_time_str
        update_data["punches"] = punches_copy
    else:
        update_data["punches"] = [{"punchIn": start_time_str, "punchOut": now_time_str}]

    if auto_remark:
        existing_remarks = record.get("remarks") or ""
        if not existing_remarks or existing_remarks == "-":
            update_data["remarks"] = auto_remark
        elif auto_remark not in existing_remarks:
            update_data["remarks"] = f"{existing_remarks}; {auto_remark}"

    await db.attendance.update_one({"_id": record["_id"]}, {"$set": update_data})
    return await db.attendance.find_one({"_id": record["_id"]})


async def auto_close_stale_open_sessions(db, employee_id: str) -> int:
    """Auto-close open attendance sessions from previous days (forgotten punch-out)."""
    closed_count = 0
    today_str = get_now().strftime("%Y-%m-%d")
    today_dt_naive = datetime.strptime(today_str, "%Y-%m-%d")
    today_dt_aware = today_dt_naive.replace(tzinfo=IST)
    query_or = [{"employeeId": employee_id}]
    if ObjectId.is_valid(employee_id):
        query_or.append({"employeeId": ObjectId(employee_id)})
    cursor = db.attendance.find({
        "$or": query_or,
        "checkOut": None,
        "date": {"$nin": [today_str, today_dt_naive, today_dt_aware]}
    })
    async for record in cursor:
        try:
            default_end = parse_datetime(record["date"], "20:00:00")
            close_dt = default_end if default_end else get_now()
            if close_dt > get_now():
                close_dt = get_now()
            await _apply_punch_out_to_record(db, record, close_dt, auto_remark="Auto closed (forgotten punch-out)")
            closed_count += 1
        except Exception as e:
            print(f"Error auto-closing old session for {employee_id}: {e}")
    return closed_count


async def get_attendance_status(db, employee_id: str):
    await auto_close_stale_open_sessions(db, employee_id)
    query_or = [{"employeeId": employee_id}]
    if ObjectId.is_valid(employee_id):
        query_or.append({"employeeId": ObjectId(employee_id)})
        
    cursor = db.attendance.find({
        "$or": query_or,
        "checkOut": None
    }).sort("date", -1).limit(1)
    records = await cursor.to_list(length=1)
    record = records[0] if records else None
    return fix_id(record)

async def punch_in(db, employee_id: str, punch_in_time: Optional[str] = None, performed_by: str = "System", user_name: str = "System User", punch_in_activity_type: Optional[str] = None, punch_in_activity_subtype: Optional[str] = None, punch_in_activity_value: Optional[str] = None, punch_in_task_id: Optional[str] = None):
    employee = await get_employee(db, employee_id)
    if not employee or employee.get("status", "").lower() == "inactive":
        return None

    await auto_close_stale_open_sessions(db, employee_id)
    
    today = get_now()
    today_str = today.strftime("%Y-%m-%d")
    today_dt_naive = datetime.strptime(today_str, "%Y-%m-%d")
    today_dt_aware = today_dt_naive.replace(tzinfo=IST)
    if punch_in_time:
        now_time_str = punch_in_time
        if len(now_time_str.split(':')) == 2:
            now_time_str = f"{now_time_str}:00"
    else:
        now_time_str = today.strftime("%H:%M:%S")

    # Check if there is an approved half-day leave for today
    half_day_type = None
    try:
        leave_cursor = db.leave_requests.find({
            "$or": [
                {"employeeId": employee_id},
                {"employee_id": employee_id}
            ],
            "status": {"$in": ["Approved", "approved"]}
        })
        all_emp_leaves = await leave_cursor.to_list(length=100)
        for l in all_emp_leaves:
            raw_s = l.get("startDate") or l.get("start_date")
            raw_e = l.get("endDate") or l.get("end_date")
            if not raw_s or not raw_e:
                continue
            
            def robust_parse(val):
                if not val:
                    return None
                if isinstance(val, datetime):
                    return val
                if isinstance(val, date):
                    return datetime.combine(val, datetime.min.time()).replace(tzinfo=IST)
                if not isinstance(val, str):
                    return None
                val_str = val.strip()
                if "T" in val_str:
                    val_str = val_str.split("T")[0]
                elif " " in val_str:
                    val_str = val_str.split(" ")[0]
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
                    try:
                        return datetime.strptime(val_str, fmt).replace(tzinfo=IST)
                    except ValueError:
                        continue
                return None
            
            s = robust_parse(raw_s)
            e = robust_parse(raw_e)
            if not s or not e:
                continue
            
            s_date = s.date()
            e_date = e.date()
            today_date = today.date()
            
            if s_date <= today_date <= e_date:
                day_type_str = str(l.get("day_type") or l.get("dayType") or "").strip().lower()
                is_half = (
                    l.get("half_day") == True or 
                    l.get("halfDay") == True or 
                    l.get("half_day") in [True, "true", "True"] or
                    l.get("halfDay") in [True, "true", "True"] or
                    day_type_str in ["half day", "first half", "second half", "half-day"]
                )
                if is_half:
                    display_day_type = "Half Day"
                    if day_type_str in ["first half", "second half"]:
                        display_day_type = day_type_str.title()
                    elif day_type_str:
                        display_day_type = day_type_str.title()
                    half_day_type = display_day_type
                    break
    except Exception as e_leave:
        print(f"Error checking leave for punch-in: {e_leave}")

    # Check for existing record for today to consolidate - supports naive and aware dates
    query_or = [{"employeeId": employee_id}]
    if ObjectId.is_valid(employee_id):
        query_or.append({"employeeId": ObjectId(employee_id)})
        
    existing_record = await db.attendance.find_one({
        "$or": query_or,
        "date": {"$in": [today_dt_naive, today_dt_aware]}
    })

    # Pre-calculate late punch status for the first actual punch of the day
    is_late_punch = False
    late_remark_data = None
    try:
        sys_settings = await get_system_settings(db)
        start_time_str = employee.get("startTime") or sys_settings.get("officeStartTime", "09:30")
        buffer_mins = sys_settings.get("lateBufferMins", 10)
        
        # Robust time parsing supporting both 24-hour ("09:30") and 12-hour AM/PM ("09:30 AM") formats
        try:
            start_time_obj = datetime.strptime(start_time_str.strip(), "%H:%M")
        except ValueError:
            try:
                start_time_obj = datetime.strptime(start_time_str.strip(), "%I:%M %p")
            except ValueError:
                # Fallback if both formats fail
                office_start = sys_settings.get("officeStartTime", "09:30")
                try:
                    start_time_obj = datetime.strptime(office_start.strip(), "%H:%M")
                except ValueError:
                    start_time_obj = datetime.strptime(office_start.strip(), "%I:%M %p")
                    
        limit_time_obj = start_time_obj + timedelta(minutes=buffer_mins)
        current_time_str = now_time_str[:5]
        current_time_obj = datetime.strptime(current_time_str, "%H:%M")
        
        if current_time_obj > limit_time_obj:
            is_late_punch = True
            late_remark_data = {
                "employeeId": employee_id,
                "employeeName": employee["name"],
                "role": employee.get("designation", "Staff"),
                "avatar": employee.get("profilePhoto", ""),
                "type": "Late Punch-in",
                "details": f"Late Punch-in detected at {current_time_str}. Shift starts at {start_time_str} with a {buffer_mins}-minute buffer (Limit: {limit_time_obj.strftime('%H:%M')}).",
                "addedBy": "System",
                "date": today_dt_naive
            }
    except Exception as e:
        print(f"Error computing late punch-in status: {e}")

    if existing_record:
        # If already active, check if there's a new activity type passed, and if so, change activity instead of just returning.
        if existing_record.get("status") == "Active" and existing_record.get("checkOut") is None:
            if punch_in_activity_type is not None:
                punches = existing_record.get("punches", [])
                if punches and punches[-1].get("punchOut") is None:
                    punches[-1]["punchOut"] = now_time_str
                
                new_punch = {
                    "punchIn": now_time_str, 
                    "punchOut": None,
                    "activityType": punch_in_activity_type,
                    "activitySubtype": punch_in_activity_subtype,
                    "activityValue": punch_in_activity_value,
                    "taskId": punch_in_task_id
                }
                punches.append(new_punch)
                
                await db.attendance.update_one(
                    {"_id": existing_record["_id"]},
                    {
                        "$set": {
                            "punches": punches,
                            "punchInActivityType": punch_in_activity_type,
                            "punchInActivitySubtype": punch_in_activity_subtype,
                            "punchInActivityValue": punch_in_activity_value,
                            "punchInTaskId": punch_in_task_id
                        }
                    }
                )
                

                updated_doc = await db.attendance.find_one({"_id": existing_record["_id"]})
                await log_activity(
                    db=db,
                    action="Activity Changed",
                    performedBy=performed_by if performed_by != "System" else employee_id,
                    userName=user_name if user_name != "System User" else employee.get("name", "Staff"),
                    details=f"Employee changed activity to {punch_in_activity_type} at {now_time_str}.",
                    attendanceId=str(updated_doc["_id"])
                )
                
                # Auto-create research if it doesn't exist
                if punch_in_activity_type == "Research" and punch_in_activity_value:
                    try:
                        existing_res = await db.research.find_one({"title": punch_in_activity_value, "createdBy": employee_id})
                        if not existing_res:
                            new_research = {
                                "title": punch_in_activity_value,
                                "description": "",
                                "link": "",
                                "createdBy": employee_id,
                                "createdByName": employee.get("name", ""),
                                "sharedWith": [],
                                "projectId": "",
                                "createdAt": get_now()
                            }
                            await db.research.insert_one(new_research)
                    except Exception as e_res:
                        print(f"Error auto-creating Research: {e_res}")
                        
                return fix_id(updated_doc)
            return fix_id(existing_record)
            
        if existing_record.get("status") == "On Break" and existing_record.get("checkOut") is None:
            return fix_id(existing_record)
        
        # If logged out, resume this record instead of creating a new one
        new_punch = {
            "punchIn": now_time_str, 
            "punchOut": None,
            "activityType": punch_in_activity_type,
            "activitySubtype": punch_in_activity_subtype,
            "activityValue": punch_in_activity_value,
            "taskId": punch_in_task_id
        }
        set_values = {
            "status": "Active",
            "checkOut": None,
            "lastPunchIn": now_time_str,
            "punchInActivityType": punch_in_activity_type,
            "punchInActivitySubtype": punch_in_activity_subtype,
            "punchInActivityValue": punch_in_activity_value,
            "punchInTaskId": punch_in_task_id
        }
        # If the existing checkIn is a placeholder or empty, set it to the actual first punch-in time!
        if existing_record.get("checkIn") in [None, "--", "--:--", "", "-"]:
            set_values["checkIn"] = now_time_str
            set_values["isLate"] = is_late_punch
            if is_late_punch and late_remark_data:
                try:
                    await db.remarks.insert_one(late_remark_data)
                except Exception as e_rem:
                    print(f"Error inserting late remark for existing record: {e_rem}")
            
        if half_day_type:
            remark_str = f"On Leave: {half_day_type}"
            existing_remarks = existing_record.get("remarks") or ""
            if not existing_remarks or existing_remarks == "-":
                set_values["remarks"] = remark_str
            elif remark_str not in existing_remarks:
                set_values["remarks"] = f"{existing_remarks}; {remark_str}"

        await db.attendance.update_one(
            {"_id": existing_record["_id"]},
            {
                "$set": set_values,
                "$push": {"punches": new_punch}
            }
        )
        updated_doc = await db.attendance.find_one({"_id": existing_record["_id"]})
        await log_activity(
            db=db,
            action="Clock In",
            performedBy=performed_by if performed_by != "System" else employee_id,
            userName=user_name if user_name != "System User" else employee.get("name", "Staff"),
            details=f"Employee clocked in at {now_time_str} (Resumed existing session).",
            attendanceId=str(updated_doc["_id"])
        )
        
        # Auto-create research if it doesn't exist
        if punch_in_activity_type == "Research" and punch_in_activity_value:
            try:
                existing_res = await db.research.find_one({"title": punch_in_activity_value, "createdBy": employee_id})
                if not existing_res:
                    new_research = {
                        "title": punch_in_activity_value,
                        "description": "",
                        "link": "",
                        "createdBy": employee_id,
                        "createdByName": employee.get("name", ""),
                        "sharedWith": [],
                        "projectId": "",
                        "createdAt": get_now()
                    }
                    await db.research.insert_one(new_research)
            except Exception as e_res:
                print(f"Error auto-creating Research: {e_res}")
                
        return fix_id(updated_doc)
    
    # First punch of the day: create new record (use naive date so MongoDB stores it as exactly today's midnight UTC date)
    attendance_data = {
        "employeeId": employee_id,
        "employeeName": employee["name"],
        "date": today_dt_naive,
        "checkIn": now_time_str,
        "lastPunchIn": now_time_str,
        "checkOut": None,
        "status": "Active",
        "workHours": None,
        "accumulatedWorkSeconds": 0,
        "punches": [{
            "punchIn": now_time_str, 
            "punchOut": None,
            "activityType": punch_in_activity_type,
            "activitySubtype": punch_in_activity_subtype,
            "activityValue": punch_in_activity_value,
            "taskId": punch_in_task_id
        }],
        "remarks": f"On Leave: {half_day_type}" if half_day_type else "-",
        "isLate": is_late_punch,
        "punchInActivityType": punch_in_activity_type,
        "punchInActivitySubtype": punch_in_activity_subtype,
        "punchInActivityValue": punch_in_activity_value,
        "punchInTaskId": punch_in_task_id
    }
    
    # If late, insert the remark
    if is_late_punch and late_remark_data:
        try:
            await db.remarks.insert_one(late_remark_data)
        except Exception as e:
            print(f"Error processing late remark: {e}")
    
    result = await db.attendance.insert_one(attendance_data)
    attendance_data["id"] = str(result.inserted_id)
    await log_activity(
        db=db,
        action="Clock In",
        performedBy=performed_by if performed_by != "System" else employee_id,
        userName=user_name if user_name != "System User" else employee.get("name", "Staff"),
        details=f"Employee clocked in at {now_time_str}.",
        attendanceId=attendance_data["id"]
    )
    
    # Auto-create research if it doesn't exist
    if punch_in_activity_type == "Research" and punch_in_activity_value:
        try:
            existing_res = await db.research.find_one({"title": punch_in_activity_value, "createdBy": employee_id})
            if not existing_res:
                new_research = {
                    "title": punch_in_activity_value,
                    "description": "",
                    "link": "",
                    "createdBy": employee_id,
                    "createdByName": employee.get("name", ""),
                    "sharedWith": [],
                    "projectId": "",
                    "createdAt": get_now()
                }
                await db.research.insert_one(new_research)
        except Exception as e_res:
            print(f"Error auto-creating Research: {e_res}")

    if "_id" in attendance_data:
        attendance_data.pop("_id")
    return attendance_data

async def punch_out(db, employee_id: str, punch_out_time: Optional[str] = None, performed_by: str = "System", user_name: str = "System User"):
    status = await get_attendance_status(db, employee_id)
    if not status:
        return None

    record = await db.attendance.find_one({"_id": ObjectId(status["id"])})
    if not record:
        return None

    if punch_out_time:
        if len(punch_out_time.split(':')) == 2:
            punch_out_time = f"{punch_out_time}:00"
        close_dt = parse_datetime(record['date'], punch_out_time)
    else:
        close_dt = get_now()

    updated = await _apply_punch_out_to_record(db, record, close_dt)
    if updated:
        emp_name = record.get("employeeName", "Staff")
        time_str = close_dt.strftime("%H:%M:%S")
        await log_activity(
            db=db,
            action="Clock Out",
            performedBy=performed_by if performed_by != "System" else employee_id,
            userName=user_name if user_name != "System User" else emp_name,
            details=f"Employee clocked out at {time_str}.",
            attendanceId=str(record["_id"])
        )
    return fix_id(updated)

async def create_manual_attendance(db, attendance: schemas.AttendanceCreate):
    attendance_dict = attendance.dict()
    
    # Ensure workHours and accumulatedWorkSeconds are calculated if times are provided
    if attendance_dict.get("checkIn") and attendance_dict.get("checkOut"):
        try:
            date_val = attendance_dict.get("date")
            if isinstance(date_val, (datetime, date)):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                date_str = str(date_val).split('T')[0].split(' ')[0]
                
            ci = attendance_dict['checkIn']
            co = attendance_dict['checkOut']
            
            # Pad seconds if not present
            if len(ci.split(':')) == 2: ci = f"{ci}:00"
            if len(co.split(':')) == 2: co = f"{co}:00"
            
            start = datetime.strptime(f"{date_str} {ci}", "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(f"{date_str} {co}", "%Y-%m-%d %H:%M:%S")
            if end < start:
                end += timedelta(days=1)
            duration = end - start
            total_seconds = max(0.0, duration.total_seconds())
            
            hours, remainder = divmod(int(total_seconds), 3600)
            minutes, _ = divmod(remainder, 60)
            
            attendance_dict["workHours"] = f"{hours}h {minutes}m"
            attendance_dict["accumulatedWorkSeconds"] = total_seconds
            attendance_dict["lastPunchIn"] = attendance_dict["checkIn"]
            attendance_dict["punches"] = [{"punchIn": attendance_dict["checkIn"], "punchOut": attendance_dict["checkOut"]}]
        except Exception as e:
            print(f"Error creating manual attendance calculation: {e}")

    result = await db.attendance.insert_one(attendance_dict)
    attendance_dict["id"] = str(result.inserted_id)
    if "_id" in attendance_dict:
        attendance_dict.pop("_id")
    return attendance_dict

async def update_attendance(db, attendance_id: str, attendance_update: schemas.AttendanceUpdate, performed_by: str = "System", user_name: str = "System User"):
    existing = await db.attendance.find_one({"_id": ObjectId(attendance_id)})
    if not existing:
        return None

    update_data = attendance_update.dict(exclude_unset=True)
    
    # Recalculate workHours if checkIn or checkOut are updated
    if "checkIn" in update_data or "checkOut" in update_data or "date" in update_data:
        date_val = update_data.get("date") or existing.get("date")
        ci_val = update_data.get("checkIn") or existing.get("checkIn")
        co_val = update_data.get("checkOut") or existing.get("checkOut")
        
        if ci_val and co_val:
            try:
                if isinstance(date_val, (datetime, date)):
                    date_str = date_val.strftime("%Y-%m-%d")
                else:
                    date_str = str(date_val).split('T')[0].split(' ')[0]
                
                # Pad seconds if not present
                if len(ci_val.split(':')) == 2: ci_val = f"{ci_val}:00"
                if len(co_val.split(':')) == 2: co_val = f"{co_val}:00"
                
                start = datetime.strptime(f"{date_str} {ci_val}", "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(f"{date_str} {co_val}", "%Y-%m-%d %H:%M:%S")
                if end < start:
                    end += timedelta(days=1)
                duration = end - start
                total_seconds = max(0.0, duration.total_seconds())
                
                hours, remainder = divmod(int(total_seconds), 3600)
                minutes, _ = divmod(remainder, 60)
                
                update_data["workHours"] = f"{hours}h {minutes}m"
                update_data["accumulatedWorkSeconds"] = total_seconds
                update_data["punches"] = [{"punchIn": ci_val, "punchOut": co_val}]
            except Exception as e:
                print(f"Error updating work hours calculation: {e}")

    log_details, diffs = format_field_changes(existing, update_data, f"Attendance for '{existing.get('employeeName')}'")

    await db.attendance.update_one(
        {"_id": ObjectId(attendance_id)},
        {"$set": update_data}
    )
    
    if diffs:
        await log_activity(
            db=db,
            action="Attendance Updated",
            performedBy=performed_by,
            userName=user_name,
            details=log_details,
            diffs=diffs,
            attendanceId=attendance_id
        )

    updated = await db.attendance.find_one({"_id": ObjectId(attendance_id)})
    return fix_id(updated)

async def delete_attendance(db, attendance_id: str):
    await db.attendance.delete_one({"_id": ObjectId(attendance_id)})
    return True

async def delete_multiple_attendance(db, attendance_ids: List[str]):
    await db.attendance.delete_many({
        "_id": {"$in": [ObjectId(aid) for aid in attendance_ids]}
    })
    return True



async def break_in(db, employee_id: str):
    status = await get_attendance_status(db, employee_id)
    if not status or status.get("status") == "On Break":
        return None
    
    now_str = get_now().strftime("%H:%M:%S")
    new_break = {
        "startTime": now_str,
        "endTime": None,
        "duration": None
    }
    
    update_data = {
        "$push": {"breaks": new_break},
        "$set": {"status": "On Break"}
    }
    
    # Close the current punch so its time stops
    if status.get("punches") and len(status["punches"]) > 0:
        last_punch_idx = len(status["punches"]) - 1
        last_punch = status["punches"][last_punch_idx]
        if not last_punch.get("punchOut"):
            update_data["$set"] = update_data.get("$set", {})
            update_data["$set"][f"punches.{last_punch_idx}.punchOut"] = now_str
            
    await db.attendance.update_one(
        {"_id": ObjectId(status["id"])},
        update_data
    )
    result = await db.attendance.find_one({"_id": ObjectId(status["id"])})
    return fix_id(result)

async def break_out(db, employee_id: str, resume_task: bool = False):
    await auto_close_stale_open_sessions(db, employee_id)
    query_or = [{"employeeId": employee_id}]
    if ObjectId.is_valid(employee_id):
        query_or.append({"employeeId": ObjectId(employee_id)})
        
    cursor = db.attendance.find({
        "$or": query_or,
        "status": "On Break",
        "checkOut": None
    }).sort("date", -1).limit(1)
    records = await cursor.to_list(length=1)
    record = records[0] if records else None
    
    if not record or not record.get("breaks"):
        return None
    
    # Get the last break which should have no endTime
    last_break_idx = len(record["breaks"]) - 1
    last_break = record["breaks"][last_break_idx]
    
    now = get_now()
    start_time = parse_datetime(record['date'], last_break['startTime'])
    
    if last_break.get("endTime") is not None:
        return None
        
    duration_delta = now - start_time
    minutes = int(duration_delta.total_seconds()) // 60
    duration_str = f"{minutes}m"
    
    update_data = {
        "$set": {
            f"breaks.{last_break_idx}.endTime": now.strftime("%H:%M:%S"),
            f"breaks.{last_break_idx}.duration": duration_str,
            "status": "Active"
        }
    }
    
    if resume_task and record.get("punches") and len(record["punches"]) > 0:
        last_punch = record["punches"][-1]
        now_time_str = now.strftime("%H:%M:%S")
        new_punch = {
            "punchIn": now_time_str,
            "punchOut": None,
            "activityType": last_punch.get("activityType"),
            "activitySubtype": last_punch.get("activitySubtype"),
            "activityValue": last_punch.get("activityValue"),
            "taskId": last_punch.get("taskId")
        }
        update_data["$push"] = update_data.get("$push", {})
        update_data["$push"]["punches"] = new_punch
        update_data["$set"]["punchInActivityType"] = last_punch.get("activityType")
        update_data["$set"]["punchInActivitySubtype"] = last_punch.get("activitySubtype")
        update_data["$set"]["punchInActivityValue"] = last_punch.get("activityValue")
        update_data["$set"]["punchInTaskId"] = last_punch.get("taskId")

    await db.attendance.update_one(
        {"_id": ObjectId(record["_id"])},
        update_data
    )
    result = await db.attendance.find_one({"_id": ObjectId(record["_id"])})
    return fix_id(result)

# Leave Request CRUD
async def create_leave_request(db, leave: schemas.LeaveRequestCreate, performed_by: str = "System", user_name: str = "System User"):
    leave_dict = leave.dict()
    leave_dict["status"] = "Pending"
    leave_dict["requested_on"] = get_now().strftime("%d-%m-%Y %H:%M")
    result = await db.leave_requests.insert_one(leave_dict)
    leave_dict["id"] = str(result.inserted_id)
    if "_id" in leave_dict:
        leave_dict.pop("_id")

    await log_activity(
        db=db,
        action="Leave Requested",
        performedBy=performed_by,
        userName=user_name,
        details=f"{leave_dict['employee_name']} requested {leave_dict['type']} leave from {leave_dict['start_date']} to {leave_dict['end_date']}. Duration: {leave_dict['duration']}. Reason: {leave_dict['reason']}",
        leaveId=leave_dict["id"]
    )

    # Notify HR and Admin
    try:
        # Find all HR and Admin users
        admins_and_hr = await db.employees.find({
            "role": {"$regex": "^(Admin|HR)$", "$options": "i"}
        }).to_list(length=100)

        for staff in admins_and_hr:
            staff_id = str(staff["_id"])
            # Don't notify the person who requested the leave if they are an admin/hr
            if staff_id == leave_dict["employee_id"]:
                continue
                
            notification = {
                "employee_id": staff_id,
                "title": "New Leave Request",
                "message": f"{leave_dict['employee_name']} has requested {leave_dict['type']} leave for {leave_dict['duration']}. Reason: {leave_dict['reason']}",
                "type": "leave",
                "reference_id": leave_dict["id"],
                "is_read": False,
                "created_at": get_now().strftime("%d-%m-%Y %H:%M")
            }
            await db.notifications.insert_one(notification)
    except Exception as e:
        print(f"Error creating notifications for leave request: {e}")

    return leave_dict

async def get_all_leave_requests(db, skip: int = 0, limit: int = 100):
    cursor = db.leave_requests.find().sort("requested_on", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    leaves = [fix_id(row) for row in rows]
    for leave in leaves:
        approved_by_id = leave.get("approved_by_id")
        if approved_by_id:
            try:
                employee = await db.employees.find_one({"_id": ObjectId(approved_by_id)})
                if employee:
                    leave["approved_by_photo"] = employee.get("profilePhoto") or None
            except Exception:
                pass
    return leaves

async def get_user_leave_requests(db, employee_id: str, skip: int = 0, limit: int = 100):
    from bson import ObjectId
    try:
        obj_id = ObjectId(employee_id)
        query = {"$or": [{"employee_id": employee_id}, {"employee_id": obj_id}]}
    except Exception:
        query = {"employee_id": employee_id}
    
    cursor = db.leave_requests.find(query).sort("requested_on", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    leaves = [fix_id(row) for row in rows]
    for leave in leaves:
        approved_by_id = leave.get("approved_by_id")
        if approved_by_id:
            try:
                employee = await db.employees.find_one({"_id": ObjectId(approved_by_id)})
                if employee:
                    leave["approved_by_photo"] = employee.get("profilePhoto") or None
            except Exception:
                pass
    return leaves

async def auto_create_leave_attendance(db, employee_id: str, start_date, end_date, leave_type: str = "leave", is_half: bool = False, day_type: str = "Half Day"):
    def robust_parse(val):
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        if isinstance(val, date):
            return datetime.combine(val, datetime.min.time()).replace(tzinfo=IST)
        # Parse string
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(val, fmt).replace(tzinfo=IST)
            except ValueError:
                continue
        return None

    s = robust_parse(start_date)
    e = robust_parse(end_date)
    if not s or not e:
        return

    employee = await get_employee(db, employee_id)
    if not employee:
        return

    curr = s
    if is_half:
        remark_str = f"On Leave: {day_type}"
    else:
        remark_str = f"Auto-marked leave - {leave_type.lower()} approved"

    while curr <= e:
        date_naive = curr.replace(tzinfo=None)
        date_aware = curr.replace(tzinfo=IST)
        
        existing = await db.attendance.find_one({
            "employeeId": employee_id,
            "date": {"$in": [date_naive, date_aware]}
        })

        if existing:
            if is_half:
                # For half-day leaves, do NOT overwrite check-in/out times, punches, status etc.
                # Just append remark and ensure status isn't "Leave"
                new_status = existing.get("status")
                if new_status == "Leave":
                    new_status = "--"
                
                existing_remarks = existing.get("remarks") or ""
                if remark_str not in existing_remarks:
                    new_remarks = f"{existing_remarks}; {remark_str}" if existing_remarks else remark_str
                else:
                    new_remarks = existing_remarks

                await db.attendance.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "status": new_status,
                        "remarks": new_remarks
                    }}
                )
            else:
                await db.attendance.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "status": "Leave",
                        "checkIn": "--",
                        "lastPunchIn": "--",
                        "checkOut": "--",
                        "workHours": "--",
                        "accumulatedWorkSeconds": 0,
                        "breaks": [],
                        "punches": [],
                        "remarks": remark_str
                    }}
                )
        else:
            if not is_half:
                record = {
                    "employeeId": employee_id,
                    "employeeName": employee["name"],
                    "date": date_naive,
                    "checkIn": "--",
                    "lastPunchIn": "--",
                    "checkOut": "--",
                    "status": "Leave",
                    "workHours": "--",
                    "accumulatedWorkSeconds": 0,
                    "breaks": [],
                    "punches": [],
                    "remarks": remark_str
                }
                await db.attendance.insert_one(record)

        curr += timedelta(days=1)
        if (curr - s).days > 365:
            break

async def update_leave_request(db, leave_id: str, update_data: dict, performed_by: str = "System", user_name: str = "System User"):
    # Fetch current leave request
    leave = await db.leave_requests.find_one({"_id": ObjectId(leave_id)})
    if not leave:
        return None
    
    log_details, diffs = format_field_changes(leave, update_data, f"Leave Request for '{leave.get('employee_name')}'")

    await db.leave_requests.update_one(
        {"_id": ObjectId(leave_id)},
        {"$set": update_data}
    )

    # Check if the status has become Approved
    if "status" in update_data and update_data["status"] == "Approved":
        emp_id = leave.get("employee_id") or leave.get("employeeId")
        raw_start = leave.get("start_date") or leave.get("startDate")
        raw_end = leave.get("end_date") or leave.get("endDate")
        leave_type = leave.get("leaveType") or leave.get("type") or "leave"
        if emp_id and raw_start and raw_end:
            try:
                # Detect half day and day type
                day_type_str = str(leave.get("day_type") or leave.get("dayType") or "").strip().lower()
                is_half = (
                    leave.get("half_day") == True or 
                    leave.get("halfDay") == True or 
                    leave.get("half_day") in [True, "true", "True"] or
                    leave.get("halfDay") in [True, "true", "True"] or
                    day_type_str in ["half day", "first half", "second half", "half-day"]
                )
                
                display_day_type = "Half Day"
                if day_type_str in ["first half", "second half"]:
                    display_day_type = day_type_str.title()
                elif day_type_str:
                    display_day_type = day_type_str.title()
                    
                await auto_create_leave_attendance(db, emp_id, raw_start, raw_end, leave_type, is_half=is_half, day_type=display_day_type)
            except Exception as e:
                print(f"Error auto creating leave attendance: {e}")
    
    # Create notification if status changed
    if "status" in update_data and update_data["status"] in ["Approved", "Rejected", "Cancelled"]:
        msg = f"Your {leave['type']} request from {leave['start_date']} to {leave['end_date']} has been {update_data['status'].lower()}."
        if update_data["status"] == "Rejected" and update_data.get("reject_reason"):
            msg += f" Reason: {update_data['reject_reason']}"
        elif update_data["status"] == "Approved" and update_data.get("approve_reason"):
            msg += f" Message: {update_data['approve_reason']}"
        
        await create_notification(db, schemas.NotificationCreate(
            employee_id=leave["employee_id"],
            title=f"Leave Request {update_data['status']}",
            message=msg,
            type="leave",
            created_at=get_now().strftime("%d-%m-%Y %H:%M")
        ))

    if diffs:
        await log_activity(
            db=db,
            action="Leave Updated",
            performedBy=performed_by,
            userName=user_name,
            details=log_details,
            diffs=diffs,
            leaveId=leave_id
        )
        
    result = await db.leave_requests.find_one({"_id": ObjectId(leave_id)})
    return fix_id(result)

async def update_leave_request_status(db, leave_id: str, status: str, approved_by: str = None, approved_by_role: str = None, approved_by_id: str = None, approved_by_photo: str = None, reject_reason: str = None, approve_reason: str = None, performed_by: str = "System", user_name: str = "System User"):
    update_data = {"status": status}
    if approved_by:
        update_data["approved_by"] = approved_by
    if approved_by_role:
        update_data["approved_by_role"] = approved_by_role
    if approved_by_id:
        update_data["approved_by_id"] = approved_by_id
    if approved_by_photo:
        update_data["approved_by_photo"] = approved_by_photo
    if reject_reason:
        update_data["reject_reason"] = reject_reason
    if approve_reason:
        update_data["approve_reason"] = approve_reason
    return await update_leave_request(db, leave_id, update_data, performed_by=performed_by, user_name=user_name)


# Notification CRUD
async def create_notification(db, notification: schemas.NotificationCreate):
    notification_dict = notification.dict()
    if not notification_dict.get("created_at"):
        notification_dict["created_at"] = get_now().strftime("%d-%m-%Y %H:%M")
    result = await db.notifications.insert_one(notification_dict)
    notification_dict["id"] = str(result.inserted_id)
    if "_id" in notification_dict:
        notification_dict.pop("_id")
        
    try:
        await ws_manager.send_personal_message(notification_dict["employee_id"], "new_notification", notification_dict)
    except Exception as e:
        print(f"Error broadcasting new notification: {e}")
        
    return notification_dict

async def notify_work_assignment(db, employee_id: str, title: str, message: str, work_type: str, reference_id: str):
    try:
        if employee_id and employee_id != "System" and employee_id != "unassigned":
            await create_notification(db, schemas.NotificationCreate(
                employee_id=employee_id,
                title=title,
                message=message,
                type=work_type,
                reference_id=reference_id
            ))
    except Exception as e:
        print(f"Error sending assignment notification: {e}")

async def get_notifications_by_user(db, employee_id: str, skip: int = 0, limit: int = 50):
    try:
        emp_obj_id = ObjectId(employee_id)
        query = {"$or": [{"employee_id": employee_id}, {"employee_id": emp_obj_id}]}
    except Exception:
        query = {"employee_id": employee_id}
        
    cursor = db.notifications.find(query).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def mark_notification_as_read(db, notification_id: str):
    await db.notifications.update_one(
        {"_id": ObjectId(notification_id)},
        {"$set": {"is_read": True}}
    )
    result = await db.notifications.find_one({"_id": ObjectId(notification_id)})
    return fix_id(result)

async def mark_all_notifications_as_read(db, employee_id: str):
    try:
        emp_obj_id = ObjectId(employee_id)
        query = {"$or": [{"employee_id": employee_id}, {"employee_id": emp_obj_id}], "is_read": False}
    except Exception:
        query = {"employee_id": employee_id, "is_read": False}

    await db.notifications.update_many(
        query,
        {"$set": {"is_read": True}}
    )
    return {"message": "All notifications marked as read"}

async def delete_leave_request(db, leave_id: str, performed_by: str = "System", user_name: str = "System User"):
    leave = await db.leave_requests.find_one({"_id": ObjectId(leave_id)})
    if leave:
        await log_activity(
            db=db,
            action="Leave Request Deleted",
            performedBy=performed_by,
            userName=user_name,
            details=f"Leave request for '{leave.get('employee_name')}' from {leave.get('start_date')} to {leave.get('end_date')} was deleted.",
            leaveId=leave_id
        )
    result = await db.leave_requests.delete_one({"_id": ObjectId(leave_id)})
    return result.deleted_count > 0

# Helper for checking full entity access permission
async def user_has_full_entity_access(db, user_id: str, role: str, target_module: str = None) -> bool:
    if not role and not user_id:
        return False
    role_lower = str(role or "").lower().strip()
    full_roles = {"admin", "manager", "social media manager", "smm", "director", "head", "super admin", "digital marketer", "digital marketing", "hr", "team leader", "tl"}
    if role_lower in full_roles or "social media" in role_lower or "digital marketing" in role_lower:
        return True
        
    if user_id:
        user = await get_employee(db, user_id)
        if user:
            ur = str(user.get("role", "")).lower().strip()
            udes = str(user.get("designation", "")).lower().strip()
            udept = str(user.get("department", "")).lower().strip()
            if ur in full_roles or udes in full_roles or "social media" in ur or "social media" in udes or "digital marketing" in ur or "digital marketing" in udes:
                return True
                
        # Check permissions table
        perm_doc = await db.user_permissions.find_one({"employeeId": user_id})
        if perm_doc:
            modules_to_check = {"projects", "smm", "clients", "digital-marketing", "work-management"}
            if target_module:
                modules_to_check.add(target_module)
            for p in perm_doc.get("permissions", []):
                if p.get("moduleName") in modules_to_check and (p.get("canView") or p.get("canEdit") or p.get("canAdd")):
                    return True
    return False

# Client CRUD
async def get_clients(db, skip: int = 0, limit: int = 10000, user_info: dict = None):
    query = {}
    if user_info:
        role = str(user_info.get("role", "")).lower()
        user_id = user_info.get("sub")
        if not await user_has_full_entity_access(db, user_id, role, "clients"):
            user_id = user_info.get("sub")
            # Fetch user's projects to get associated clients
            user_projects = await get_projects(db, userId=user_id, role=role, skip=0, limit=10000)
            project_client_ids = [str(p.get("clientId")) for p in user_projects if p.get("clientId")]
            
            query["$or"] = [
                {"assignedEmployeeId": user_id},
                {"_id": {"$in": [ObjectId(cid) for cid in project_client_ids if ObjectId.is_valid(cid)]}}
            ]
            
            user = await get_employee(db, user_id)
            if user and user.get("department"):
                import re
                dept_regex = re.compile(f".*{re.escape(user.get('department'))}.*", re.IGNORECASE)
                query["$or"].append({"department": dept_regex})

            # If the user is HR, also give them access to the Creative department's clients for SMM access
            if role == "hr" or (user and str(user.get("role", "")).lower().strip() == "hr"):
                import re
                creative_regex = re.compile(".*Creative.*", re.IGNORECASE)
                query["$or"].append({"department": creative_regex})
            
    cursor = db.clients.find(query).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_client(db, client_id: str):
    doc = await db.clients.find_one({"_id": ObjectId(client_id)})
    if doc:
        return fix_id(doc)
    return None

async def calculate_next_followup_date(db, start_date_str: str, config: dict) -> str:
    if not start_date_str or not config:
        return None
        
    try:
        current_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    except ValueError:
        return None
        
    followup_type = config.get("followupType", "Interval")
    
    holiday_query = {
        "$or": [
            {"company": {"$in": [None, "", "null"]}}
        ]
    }
    
    all_holidays = await db.holidays.find(holiday_query).to_list(length=1000)
    
    holiday_dates = set()
    for h in all_holidays:
        h_date = h.get("date")
        if h_date:
            try:
                h_date_str = h_date.strftime("%Y-%m-%d") if isinstance(h_date, (date, datetime)) else str(h_date)[:10]
                holiday_dates.add(h_date_str)
            except:
                pass
                
    def is_valid_day(d: datetime) -> bool:
        if d.weekday() == 6: # Sunday
            return False
        if d.strftime("%Y-%m-%d") in holiday_dates:
            return False
        return True

    current_date += timedelta(days=1)
    
    if followup_type == "Interval":
        interval_days = config.get("followupIntervalDays") or 0
        if interval_days <= 0: return None
        
        current_date -= timedelta(days=1)
        days_added = 0
        while days_added < interval_days:
            current_date += timedelta(days=1)
            if not is_valid_day(current_date): continue
            days_added += 1
        return current_date.strftime("%Y-%m-%d")
        
    elif followup_type == "Weekly":
        days_of_week = config.get("followupDaysOfWeek") or []
        if not days_of_week: return None
        
        for _ in range(30):
            if current_date.weekday() in days_of_week:
                target_date = current_date
                while not is_valid_day(target_date):
                    target_date += timedelta(days=1)
                return target_date.strftime("%Y-%m-%d")
            current_date += timedelta(days=1)
            
    elif followup_type == "Monthly":
        import calendar
        dates_of_month = config.get("followupDatesOfMonth") or []
        if not dates_of_month: return None
        
        for _ in range(60):
            last_day_of_month = calendar.monthrange(current_date.year, current_date.month)[1]
            
            is_match = False
            for target_dom in dates_of_month:
                if current_date.day == target_dom:
                    is_match = True
                    break
                elif target_dom > last_day_of_month and current_date.day == last_day_of_month:
                    is_match = True
                    break
                    
            if is_match:
                target_date = current_date
                while not is_valid_day(target_date):
                    target_date += timedelta(days=1)
                return target_date.strftime("%Y-%m-%d")
            current_date += timedelta(days=1)
            
    return None

async def bulk_assign_leads(db, lead_ids: List[str], assigned_to, performed_by: str = None, user_name: str = None):
    obj_ids = [ObjectId(lid) if ObjectId.is_valid(lid) else lid for lid in lead_ids]
    log_entry = {
        "action": "Assigned (Bulk)",
        "performedBy": performed_by,
        "userName": user_name,
        "timestamp": datetime.now(),
        "details": f"Bulk assigned to {assigned_to}"
    }

    # Extract plain data if assigned_to is a pydantic model (it might be RobustAssignedTo, which is a Union, so it could be str or dict)
    assigned_val = assigned_to
    if hasattr(assigned_to, 'model_dump'):
        assigned_val = assigned_to.model_dump()
    elif isinstance(assigned_to, list):
        assigned_val = [v.model_dump() if hasattr(v, 'model_dump') else v for v in assigned_to]

    result = await db.leads.update_many(
        {"_id": {"$in": obj_ids}},
        {
            "$set": {"assignedTo": assigned_val, "updated_at": datetime.now()},
            "$push": {"logs": log_entry}
        }
    )
    return result.modified_count

async def bulk_delete_leads(db, lead_ids: List[str]):
    obj_ids = [ObjectId(lid) if ObjectId.is_valid(lid) else lid for lid in lead_ids]
    result = await db.leads.delete_many({"_id": {"$in": obj_ids}})
    return result.deleted_count

async def create_client(db, client: schemas.ClientCreate):
    client_dict = client.dict()
    performedBy = client_dict.pop("performedBy", "Unknown")
    userName = client_dict.pop("userName", "Unknown User")
    
    if not client_dict.get("createdDate"):
        client_dict["createdDate"] = get_now().strftime("%Y-%m-%d")
        
    if client_dict.get("followupType") and client_dict.get("lastFollowupDate"):
        client_dict["nextFollowupDate"] = await calculate_next_followup_date(db, client_dict["lastFollowupDate"], client_dict)
        
    result = await db.clients.insert_one(client_dict)
    clientId = str(result.inserted_id)
    
    await log_activity(db, "Created", performedBy, userName, f"Client '{client_dict['companyName']}' was created.", clientId=clientId)
    
    # Notify assignments
    c_name = client_dict.get("companyName", "Unnamed Client")
    if client_dict.get("assignedEmployeeId"):
        await notify_work_assignment(db, client_dict["assignedEmployeeId"], "New Client Assigned", f"You have been assigned to client '{c_name}'.", "client", clientId)
    
    creative_fields = [
        "assignedScriptwriterId", "assignedReelEditorId", "assignedPostDesignerId",
        "assignedShooterId", "assignedApproverId", "assignedPosterId",
        "assignedCaptionWriterId", "assignedThumbnailDesignerId"
    ]
    for field in creative_fields:
        if client_dict.get(field):
            role_display = field.replace("assigned", "").replace("Id", "")
            await notify_work_assignment(db, client_dict[field], "Client Creative Role Assigned", f"You have been assigned as {role_display} for client '{c_name}'.", "client", clientId)
    
    try:
        await ws_manager.broadcast_all("data_refresh", {"entity": "clients"})
    except Exception:
        pass
        
    doc = await db.clients.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def update_client(db, client_id: str, client_update: schemas.ClientUpdate):
    update_data = client_update.dict(exclude_unset=True)
    performedBy = update_data.pop("performedBy", "Unknown")
    userName = update_data.pop("userName", "Unknown User")
    
    if update_data:
        old_client = await db.clients.find_one({"_id": ObjectId(client_id)})
        
        # Handle followup calculation
        followup_fields = ["followupType", "followupIntervalDays", "followupDaysOfWeek", "followupDatesOfMonth", "lastFollowupDate"]
        if any(f in update_data for f in followup_fields):
            new_config = {
                "followupType": update_data.get("followupType", old_client.get("followupType")),
                "followupIntervalDays": update_data.get("followupIntervalDays", old_client.get("followupIntervalDays")),
                "followupDaysOfWeek": update_data.get("followupDaysOfWeek", old_client.get("followupDaysOfWeek")),
                "followupDatesOfMonth": update_data.get("followupDatesOfMonth", old_client.get("followupDatesOfMonth"))
            }
            new_last_date = update_data.get("lastFollowupDate", old_client.get("lastFollowupDate"))
            if new_last_date:
                update_data["nextFollowupDate"] = await calculate_next_followup_date(db, new_last_date, new_config)
            else:
                update_data["nextFollowupDate"] = None
                
        try:
            client_id_obj = ObjectId(client_id)
            query_client_id = {"$in": [client_id, client_id_obj]}
        except Exception:
            query_client_id = client_id

        if update_data.get("status") == "on-hold":
            await db.projects.update_many(
                {"clientId": query_client_id},
                {
                    "$set": {"status": "on-hold"},
                    "$push": {"statusHistory": {"status": "on-hold", "timestamp": get_now().isoformat()}}
                }
            )
        elif update_data.get("status") == "active":
            await db.projects.update_many(
                {"clientId": query_client_id},
                {
                    "$set": {"status": "in-progress"},
                    "$push": {"statusHistory": {"status": "in-progress", "timestamp": get_now().isoformat()}}
                }
            )
            
        await db.clients.update_one({"_id": ObjectId(client_id)}, {"$set": update_data})
        
        log_details, diffs = format_field_changes(old_client, update_data, f"Client '{old_client.get('companyName')}'")
        await log_activity(db, "Updated", performedBy, userName, log_details, diffs=diffs, clientId=client_id)
        
        # Check if client assignments changed and notify
        c_name = old_client.get("companyName", "Unnamed Client")
        new_emp = update_data.get("assignedEmployeeId")
        old_emp = old_client.get("assignedEmployeeId")
        if new_emp and new_emp != old_emp:
            await notify_work_assignment(db, new_emp, "New Client Assigned", f"You have been assigned to client '{c_name}'.", "client", client_id)

        creative_fields = [
            "assignedScriptwriterId", "assignedReelEditorId", "assignedPostDesignerId",
            "assignedShooterId", "assignedApproverId", "assignedPosterId",
            "assignedCaptionWriterId", "assignedThumbnailDesignerId"
        ]
        for field in creative_fields:
            new_assignee = update_data.get(field)
            old_assignee = old_client.get(field)
            if new_assignee and new_assignee != old_assignee:
                role_display = field.replace("assigned", "").replace("Id", "")
                await notify_work_assignment(db, new_assignee, "Client Creative Role Assigned", f"You have been assigned as {role_display} for client '{c_name}'.", "client", client_id)
        
        try:
            await ws_manager.broadcast_all("data_refresh", {"entity": "clients"})
            if update_data.get("status") in ["on-hold", "active"]:
                await ws_manager.broadcast_all("data_refresh", {"entity": "projects"})
        except Exception:
            pass
            
    doc = await db.clients.find_one({"_id": ObjectId(client_id)})
    return fix_id(doc)

async def add_client_meeting(db, client_id: str, meeting: schemas.Meeting, performedBy: str = "Unknown", userName: str = "Unknown User"):
    meeting_dict = meeting.dict()
    if not meeting_dict.get("date"):
        meeting_dict["date"] = get_now().strftime("%Y-%m-%d %H:%M")
        
    await db.clients.update_one(
        {"_id": ObjectId(client_id)},
        {"$push": {"meetings": meeting_dict}}
    )
    
    # Log activity
    await log_activity(db, "Client Meeting Added", performedBy, userName, f"Added meeting: {meeting_dict.get('note', 'No notes provided')}", clientId=client_id)
    
    # Auto-create schedules for attendees
    client = await db.clients.find_one({"_id": ObjectId(client_id)})
    client_name = client.get("companyName", "Client") if client else "Client"
    
    attendee_ids = meeting_dict.get("attendeeIds", [])
    if attendee_ids:
        # Try to parse start and end times
        start_time, end_time = "09:30", "10:00"
        if " - " in meeting_dict.get("duration", ""):
            parts = meeting_dict["duration"].split(" - ")
            start_time, end_time = parts[0].strip(), parts[1].strip()
        
        date_str = meeting_dict["date"].split(" ")[0] if " " in meeting_dict.get("date", "") else meeting_dict.get("date", "").split("T")[0]
        
        for emp_id in attendee_ids:
            if not emp_id: continue
            q = {"_id": ObjectId(emp_id)} if ObjectId.is_valid(emp_id) else {"id": emp_id}
            emp = await db.employees.find_one(q)
            emp_name = emp.get("name", "Unknown") if emp else "Unknown"
            
            schedule_data = {
                "title": f"Meeting: {client_name}",
                "description": meeting_dict.get("note", ""),
                "employeeId": str(emp.get("_id", emp_id)) if emp else emp_id,
                "employeeName": emp_name,
                "date": date_str,
                "startTime": start_time,
                "endTime": end_time,
                "type": "meeting",
                "attendees": [a.strip() for a in meeting_dict.get("attendees", "").split(",")] if meeting_dict.get("attendees") else [],
                "createdBy": userName
            }
            # create_schedule will handle Google Calendar syncing if enabled
            try:
                await create_schedule(db, schedule_data)
            except Exception as e:
                print(f"Error auto-creating schedule for meeting: {e}")

    doc = await db.clients.find_one({"_id": ObjectId(client_id)})
    return fix_id(doc)

async def update_client_meeting(db, client_id: str, meeting_idx: int, meeting: schemas.Meeting, performedBy: str = "Unknown", userName: str = "Unknown User"):
    meeting_dict = meeting.dict()
    
    await db.clients.update_one(
        {"_id": ObjectId(client_id)},
        {"$set": {f"meetings.{meeting_idx}": meeting_dict}}
    )
    
    # Log activity
    await log_activity(db, "Client Meeting Updated", performedBy, userName, f"Updated meeting at index {meeting_idx}: {meeting_dict.get('note', 'No notes provided')}", clientId=client_id)
    
    doc = await db.clients.find_one({"_id": ObjectId(client_id)})
    return fix_id(doc)

async def delete_client_meeting(db, client_id: str, meeting_idx: int, performedBy: str = "Unknown", userName: str = "Unknown User"):
    client = await db.clients.find_one({"_id": ObjectId(client_id)})
    if client and "meetings" in client and len(client["meetings"]) > meeting_idx:
        meetings = client["meetings"]
        deleted_meeting = meetings.pop(meeting_idx)
        await db.clients.update_one(
            {"_id": ObjectId(client_id)},
            {"$set": {"meetings": meetings}}
        )
        await log_activity(db, "Client Meeting Deleted", performedBy, userName, f"Deleted meeting: {deleted_meeting.get('note', 'No notes provided')}", clientId=client_id)
    doc = await db.clients.find_one({"_id": ObjectId(client_id)})
    return fix_id(doc)

async def delete_client(db, client_id: str):
    client = await db.clients.find_one({"_id": ObjectId(client_id)})
    
    if client:
        # Cascade deletes
        projects = await db.projects.find({"clientId": client_id}).to_list(length=1000)
        for p in projects:
            project_id = str(p["_id"])
            await archive_and_delete_many(db, "wm_tasks", {"projectId": project_id})
            await archive_and_delete_many(db, "task_logs", {"projectId": project_id})
            
        await archive_and_delete_many(db, "projects", {"clientId": client_id})
        await archive_and_delete_many(db, "marketing_daily_reports", {"clientId": client_id})
        await archive_and_delete_many(db, "marketing_monthly_reports", {"clientId": client_id})
        await archive_and_delete_many(db, "task_logs", {"clientId": client_id})
        
        company_name = client.get("companyName", "")
        if company_name:
            await db.leads.update_many(
                {"company": company_name},
                {"$set": {"company": f"[Deleted] {company_name}"}}
            )

    await archive_and_delete_one(db, "clients", {"_id": ObjectId(client_id)})
    if client:
        await log_activity(db, "Deleted", "Admin", "N/A", f"Client '{client.get('companyName')}' was deleted.", clientId=client_id)
    return True

# Project CRUD
async def get_projects(db, userId: str = None, role: str = None, skip: int = 0, limit: int = 100):
    query = {}
    if userId and not await user_has_full_entity_access(db, userId, role, "projects"):
        # Fetch user to get department
        user = await get_employee(db, userId)
        if user:
            dept = user.get("department")
            
            # Everyone else (Team Leader, Employee, etc) sees projects where they have tasks or are assigned

            task_cursor = db.wm_tasks.find({"assignedToId": userId})
            task_list = await task_cursor.to_list(length=1000)
            project_ids = list(set([t.get("projectId") for t in task_list if t.get("projectId")]))
            
            or_conditions = [
                {"teamLeaderId": userId},
                {"assignedEmployeeId": userId},
                {"assignedScriptwriterId": userId},
                {"assignedReelEditorId": userId},
                {"assignedPostDesignerId": userId},
                {"assignedShooterId": userId},
                {"assignedApproverId": userId},
                {"assignedPosterId": userId},
                {"assignedCaptionWriterId": userId},
                {"assignedThumbnailDesignerId": userId},
                {"assignedFinanceManagerId": userId}
            ]
            if project_ids:
                project_ids_as_obj = []
                for pid in project_ids:
                    try:
                        project_ids_as_obj.append(ObjectId(pid))
                    except Exception:
                        pass
                
                if project_ids_as_obj:
                    or_conditions.append({"_id": {"$in": project_ids_as_obj}})
                or_conditions.append({"id": {"$in": project_ids}}) # fallback for string IDs
            
            if dept:
                import re
                dept_regex = re.compile(f".*{re.escape(dept)}.*", re.IGNORECASE)
                or_conditions.append({"department": dept_regex})

            # If the user is HR, also give them access to the Creative department's projects for SMM access
            role_lower = str(role or "").lower().strip()
            if role_lower == "hr" or (user and str(user.get("role", "")).lower().strip() == "hr"):
                import re
                creative_regex = re.compile(".*Creative.*", re.IGNORECASE)
                or_conditions.append({"department": creative_regex})

            query["$or"] = or_conditions

    cursor = db.projects.find(query).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    
    today_date = get_now().date()
    expired_ids = []
    for row in rows:
        if row.get("isPaymentReceived") and row.get("nextPaymentDate"):
            try:
                # nextPaymentDate is typically saved as "YYYY-MM-DD"
                next_payment = datetime.strptime(row["nextPaymentDate"], "%Y-%m-%d").date()
                if today_date > next_payment:
                    row["isPaymentReceived"] = False
                    expired_ids.append(row["_id"])
            except Exception:
                pass

    if expired_ids:
        await db.projects.update_many({"_id": {"$in": expired_ids}}, {"$set": {"isPaymentReceived": False}})
                
    return [fix_id(row) for row in rows]

async def create_project(db, project: schemas.ProjectCreate):
    project_dict = project.dict()
    performedBy = project_dict.pop("performedBy", "Unknown")
    userName = project_dict.pop("userName", "Unknown User")
    
    if not project_dict.get("clientName") and project_dict.get("clientId"):
        client = await db.clients.find_one({"_id": ObjectId(project_dict["clientId"])})
        if client:
            project_dict["clientName"] = client.get("companyName")
    
    if not project_dict.get("teamLeaderName") and project_dict.get("teamLeaderId"):
        employee = await db.employees.find_one({"_id": ObjectId(project_dict["teamLeaderId"])})
        if employee:
            project_dict["teamLeaderName"] = f"{employee.get('firstName')} {employee.get('lastName')}"
            
    if "statusHistory" not in project_dict or not project_dict["statusHistory"]:
        project_dict["statusHistory"] = [{"status": project_dict.get("status", "planning"), "timestamp": get_now().isoformat()}]
            
    result = await db.projects.insert_one(project_dict)
    projectId = str(result.inserted_id)
    
    await log_activity(db, "Created", performedBy, userName, f"Project '{project_dict['title']}' was created.", projectId=projectId)
    
    # Send notifications
    p_title = project_dict.get("title", "Unnamed Project")
    if project_dict.get("assignedEmployeeId"):
        await notify_work_assignment(db, project_dict["assignedEmployeeId"], "New Project Assigned", f"You have been assigned to project '{p_title}'.", "project", projectId)
    if project_dict.get("teamLeaderId"):
        await notify_work_assignment(db, project_dict["teamLeaderId"], "New Project TL Assignment", f"You have been assigned as Team Leader for project '{p_title}'.", "project", projectId)
    
    dm_roles = {
        "revenueAssigneeId": "Revenue tracking",
        "followerAssigneeId": "Follower tracking",
        "userRemarkAssigneeId": "User Remark tracking",
        "clientRemarkAssigneeId": "Client Remark tracking"
    }
    for field, role_name in dm_roles.items():
        if project_dict.get(field):
            await notify_work_assignment(db, project_dict[field], "Project Role Assigned", f"You have been assigned to {role_name} for project '{p_title}'.", "project", projectId)

    creative_fields = [
        "assignedScriptwriterId", "assignedReelEditorId", "assignedPostDesignerId",
        "assignedShooterId", "assignedApproverId", "assignedPosterId",
        "assignedCaptionWriterId", "assignedThumbnailDesignerId"
    ]
    for field in creative_fields:
        if project_dict.get(field):
            role_display = field.replace("assigned", "").replace("Id", "")
            await notify_work_assignment(db, project_dict[field], "Project Creative Role Assigned", f"You have been assigned as {role_display} for project '{p_title}'.", "project", projectId)
    
    try:
        await ws_manager.broadcast_all("data_refresh", {"entity": "projects"})
    except Exception:
        pass
        
    doc = await db.projects.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def update_project(db, project_id: str, project_update: schemas.ProjectUpdate):
    update_data = project_update.dict(exclude_unset=True)
    performedBy = update_data.pop("performedBy", "Unknown")
    userName = update_data.pop("userName", "Unknown User")
    
    if update_data:
        old_project = await db.projects.find_one({"_id": ObjectId(project_id)})
        
        if update_data.get("clientId") and update_data.get("clientId") != old_project.get("clientId"):
            client = await db.clients.find_one({"_id": ObjectId(update_data["clientId"])})
            if client:
                update_data["clientName"] = client.get("companyName")
        
        if update_data.get("teamLeaderId") and update_data.get("teamLeaderId") != old_project.get("teamLeaderId"):
            employee = await db.employees.find_one({"_id": ObjectId(update_data["teamLeaderId"])})
            if employee:
                update_data["teamLeaderName"] = f"{employee.get('firstName')} {employee.get('lastName')}"
                
        # Reassign tasks and modules if team leader changed
        if update_data.get("teamLeaderId") and old_project.get("teamLeaderId") and update_data["teamLeaderId"] != old_project.get("teamLeaderId"):
            old_tl = old_project.get("teamLeaderId")
            new_tl = update_data["teamLeaderId"]
            new_tl_name = update_data.get("teamLeaderName", "Team Leader")
            
            # 1. Reassign modules in the project
            if "modules" in old_project and "modules" not in update_data:
                updated_modules = []
                modules_changed = False
                for mod in old_project["modules"]:
                    if mod.get("assignedToId") == old_tl:
                        mod["assignedToId"] = new_tl
                        mod["assignedToName"] = new_tl_name
                        modules_changed = True
                    updated_modules.append(mod)
                if modules_changed:
                    update_data["modules"] = updated_modules
            
            # 2. Reassign wm_tasks
            await db.wm_tasks.update_many(
                {"projectId": project_id, "assignedToId": old_tl},
                {"$set": {"assignedToId": new_tl, "assignedToName": new_tl_name}}
            )
                
        # Calculate nextFollowupDate
        followup_keys = ["followupType", "followupIntervalDays", "followupDaysOfWeek", "followupDatesOfMonth", "lastFollowupDate"]
        if any(k in update_data for k in followup_keys):
            merged_config = {**old_project, **update_data}
            
            # RobustDate parsing might make lastFollowupDate a date object
            base_date_obj = merged_config.get("lastFollowupDate")
            base_date = base_date_obj.strftime("%Y-%m-%d") if isinstance(base_date_obj, (date, datetime)) else base_date_obj
            if not base_date:
                base_date = get_now().strftime("%Y-%m-%d")
                
            next_date = await calculate_next_followup_date(db, base_date, merged_config)
            update_data["nextFollowupDate"] = next_date

        update_query = {"$set": update_data}
        if "status" in update_data and old_project.get("status") != update_data["status"]:
            update_query["$push"] = {"statusHistory": {"status": update_data["status"], "timestamp": get_now().isoformat()}}

        await db.projects.update_one({"_id": ObjectId(project_id)}, update_query)
        
        log_details, diffs = format_field_changes(old_project, update_data, f"Project '{old_project.get('title')}'")
        await log_activity(db, "Updated", performedBy, userName, log_details, diffs=diffs, projectId=project_id)

        # Check if assignments changed and send notifications
        p_title = old_project.get("title", "Unnamed Project")
        new_tl = update_data.get("teamLeaderId")
        old_tl = old_project.get("teamLeaderId")
        if new_tl and new_tl != old_tl:
            await notify_work_assignment(db, new_tl, "Project TL Assignment", f"You have been assigned as Team Leader for project '{p_title}'.", "project", project_id)

        new_emp = update_data.get("assignedEmployeeId")
        old_emp = old_project.get("assignedEmployeeId")
        if new_emp and new_emp != old_emp:
            await notify_work_assignment(db, new_emp, "New Project Assigned", f"You have been assigned to project '{p_title}'.", "project", project_id)

        dm_roles = {
            "revenueAssigneeId": "Revenue tracking",
            "followerAssigneeId": "Follower tracking",
            "userRemarkAssigneeId": "User Remark tracking",
            "clientRemarkAssigneeId": "Client Remark tracking"
        }
        for field, role_name in dm_roles.items():
            new_assignee = update_data.get(field)
            old_assignee = old_project.get(field)
            if new_assignee and new_assignee != old_assignee:
                await notify_work_assignment(db, new_assignee, "Project Role Assigned", f"You have been assigned to {role_name} for project '{p_title}'.", "project", project_id)

        creative_fields = [
            "assignedScriptwriterId", "assignedReelEditorId", "assignedPostDesignerId",
            "assignedShooterId", "assignedApproverId", "assignedPosterId",
            "assignedCaptionWriterId", "assignedThumbnailDesignerId"
        ]
        for field in creative_fields:
            new_assignee = update_data.get(field)
            old_assignee = old_project.get(field)
            if new_assignee and new_assignee != old_assignee:
                role_display = field.replace("assigned", "").replace("Id", "")
                await notify_work_assignment(db, new_assignee, "Project Creative Role Assigned", f"You have been assigned as {role_display} for project '{p_title}'.", "project", project_id)

        # Log detailed module changes
        if "modules" in update_data:
            old_modules = old_project.get("modules") or []
            new_modules = update_data.get("modules") or []
            
            # Check for additions
            added_modules = [m for m in new_modules if not any(om.get("name") == m.get("name") and om.get("phaseName") == m.get("phaseName") for om in old_modules)]
            # Check for deletions
            deleted_modules = [m for m in old_modules if not any(nm.get("name") == m.get("name") and nm.get("phaseName") == m.get("phaseName") for nm in new_modules)]
            # Check for updates
            for nm in new_modules:
                nm_tasks = nm.pop("tasks", None)
                for om in old_modules:
                    if nm.get("name") == om.get("name") and nm.get("phaseName") == om.get("phaseName"):
                        changes = []
                        for field in ["stage", "priority", "estimatedHours", "assignedToId", "dueDate"]:
                            if nm.get(field) != om.get(field):
                                old_val = om.get(field) or "None"
                                new_val = nm.get(field) or "None"
                                if field == "assignedToId":
                                    old_val = om.get("assignedToName") or "Unassigned"
                                    new_val = nm.get("assignedToName") or "Unassigned"
                                changes.append(f"{field.replace('Id', '')}: '{old_val}' -> '{new_val}'")
                        if changes:
                            await log_activity(db, "Module Updated", performedBy, userName, f"Updated module '{nm.get('name')}' in project '{old_project.get('title')}': {', '.join(changes)}", projectId=project_id)
                        
                        # Handle auto-assignment of tasks within the module to the module's assignee
                        if "assignedToId" in nm:
                            new_assignee_id = nm.get("assignedToId")
                            new_assignee_name = nm.get("assignedToName")
                            new_dept = None
                            if new_assignee_id and new_assignee_id != "unassigned":
                                try:
                                    emp = await db.employees.find_one({"_id": ObjectId(new_assignee_id)})
                                    if emp:
                                        new_assignee_name = f"{emp.get('firstName')} {emp.get('lastName')}"
                                        new_dept = emp.get("department")
                                except Exception:
                                    pass
                            
                            update_fields = {
                                "assignedToId": (new_assignee_id if new_assignee_id != "unassigned" else "") or "",
                                "assignedToName": new_assignee_name or "Unassigned"
                            }
                            if new_dept:
                                update_fields["department"] = new_dept
                            
                            await db.wm_tasks.update_many(
                                {"projectId": project_id, "moduleName": nm.get("name")},
                                {"$set": update_fields}
                            )
            
            for am in added_modules:
                tasks_to_add = am.pop("tasks", None)
                if tasks_to_add and isinstance(tasks_to_add, list):
                    for t in tasks_to_add:
                        if not t.get("title"):
                            continue
                        
                        task_assignee_id = am.get("assignedToId") or ""
                        task_assignee_name = am.get("assignedToName") or "Unassigned"
                        task_dept = None
                        if task_assignee_id:
                            try:
                                emp = await db.employees.find_one({"_id": ObjectId(task_assignee_id)})
                                if emp:
                                    task_assignee_name = f"{emp.get('firstName')} {emp.get('lastName')}"
                                    task_dept = emp.get("department")
                            except Exception:
                                pass
                                
                        new_task = {
                            "title": t.get("title"),
                            "description": t.get("description") or "",
                            "projectId": project_id,
                            "projectName": old_project.get("title"),
                            "assignedToId": task_assignee_id,
                            "assignedToName": task_assignee_name,
                            "dueDate": t.get("dueDate") or am.get("dueDate") or None,
                            "moduleName": am.get("name"),
                            "moduleDeadline": am.get("dueDate") or None,
                            "status": t.get("status") or "todo",
                            "priority": t.get("priority") or am.get("priority") or "medium",
                            "estimatedHours": float(t.get("estimatedHours") or 0),
                            "createdBy": performedBy,
                            "performedBy": performedBy,
                            "userName": userName,
                            "phase": am.get("phaseName") or None,
                            "subtasks": [],
                            "isApproved": False,
                            "createdDate": get_now().strftime("%Y-%m-%d")
                        }
                        if task_dept:
                            new_task["department"] = task_dept
                            
                        result = await db.wm_tasks.insert_one(new_task)
                        taskId = str(result.inserted_id)
                        await log_task_activity(db, taskId, "Created", performedBy, userName, f"Task '{new_task['title']}' was created automatically under module '{am.get('name')}'")

                await log_activity(db, "Module Created", performedBy, userName, f"Added module '{am.get('name')}' to project '{old_project.get('title')}'", projectId=project_id)
            
            for dm in deleted_modules:
                await log_activity(db, "Module Deleted", performedBy, userName, f"Deleted module '{dm.get('name')}' from project '{old_project.get('title')}'", projectId=project_id)
        
        try:
            await ws_manager.broadcast_all("data_refresh", {"entity": "projects"})
        except Exception:
            pass
            
    doc = await db.projects.find_one({"_id": ObjectId(project_id)})
    return fix_id(doc)

async def delete_project(db, project_id: str):
    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    
    if project:
        # Cascade deletes
        await archive_and_delete_many(db, "wm_tasks", {"projectId": project_id})
        await archive_and_delete_many(db, "task_logs", {"projectId": project_id})
        
    await archive_and_delete_one(db, "projects", {"_id": ObjectId(project_id)})
    if project:
        await log_activity(db, "Deleted", "Admin", "N/A", f"Project '{project.get('title')}' was deleted.", projectId=project_id)
    return True

async def update_module_notebook(db, project_id: str, payload: schemas.ModuleNotebookUpdate):
    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        return None
    modules = project.get("modules") or []
    updated = False
    from datetime import datetime
    import uuid
    for m in modules:
        m_phase = m.get("phaseName") or ""
        p_phase = payload.phaseName or ""
        if m.get("name") == payload.moduleName and m_phase == p_phase:
            if "researchNotes" not in m or not isinstance(m["researchNotes"], list):
                m["researchNotes"] = []
            
            if payload.noteId:
                # Edit existing note
                for note in m["researchNotes"]:
                    if note.get("id") == payload.noteId:
                        note["content"] = payload.researchWork
                        note["editedAt"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
                        updated = True
                        await log_activity(db, "Module Note Edited", payload.performedBy or "Unknown", payload.userName or "User", f"Edited a research note in module '{payload.moduleName}'", projectId=project_id)
                        break
            else:
                # Add new note
                if payload.researchWork.strip():
                    m["researchNotes"].append({
                        "id": str(uuid.uuid4()),
                        "content": payload.researchWork,
                        "userName": payload.userName or "Unknown User",
                        "userId": payload.performedBy or "Unknown",
                        "createdAt": datetime.now().strftime("%Y-%m-%d %I:%M %p")
                    })
                    updated = True
                    await log_activity(db, "Module Note Added", payload.performedBy or "Unknown", payload.userName or "User", f"Added a new research note to module '{payload.moduleName}'", projectId=project_id)
            m["researchWork"] = payload.researchWork
            break
            
    if updated:
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"modules": modules}}
        )
        await log_activity(db, "Module Research Updated", payload.performedBy or "Unknown", payload.userName or "User", f"Updated research work for module '{payload.moduleName}'", projectId=project_id)
        try:
            await ws_manager.broadcast_all("data_refresh", {"entity": "projects"})
        except Exception:
            pass
            
    doc = await db.projects.find_one({"_id": ObjectId(project_id)})
    return fix_id(doc) if doc else None

async def add_module_comment(db, project_id: str, payload: schemas.ModuleCommentCreate):
    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        return None
    modules = project.get("modules") or []
    updated = False
    new_comment = {
        "id": str(int(time.time() * 1000)),
        "userId": payload.userId,
        "userName": payload.userName,
        "userRole": payload.userRole,
        "content": payload.content,
        "createdAt": get_now().strftime("%d %b %Y, %I:%M %p")
    }
    for m in modules:
        m_phase = m.get("phaseName") or ""
        p_phase = payload.phaseName or ""
        if m.get("name") == payload.moduleName and m_phase == p_phase:
            if "comments" not in m or not isinstance(m["comments"], list):
                m["comments"] = []
            m["comments"].append(new_comment)
            updated = True
            break
            
    if updated:
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"modules": modules}}
        )
        await log_activity(db, "Module Comment Added", payload.userId, payload.userName, f"Added comment on module '{payload.moduleName}'", projectId=project_id)
        try:
            await ws_manager.broadcast_all("data_refresh", {"entity": "projects"})
        except Exception:
            pass
            
    doc = await db.projects.find_one({"_id": ObjectId(project_id)})
    return fix_id(doc) if doc else None


# General Task CRUD
async def get_tasks(db, userId: str = None, role: str = None, skip: int = 0, limit: int = 100, exclude_department: str = None):
    query = {}
    if userId and not await user_has_full_entity_access(db, userId, role, "tasks"):
        # User sees tasks assigned to them, or tasks they assigned
        query["$or"] = [
            {"assignedToId": userId},
            {"assignedToIds": userId},
            {"assignedById": userId}
        ]
    
    # Exclude tasks belonging to a specific department (e.g. "HR")
    if exclude_department:
        query["department"] = {"$nin": [exclude_department.upper(), exclude_department.lower(), exclude_department.capitalize()]}
                
    cursor = db.tasks.find(query).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def create_task(db, task: schemas.TaskCreate):
    task_dict = task.dict()
    performedBy = task_dict.pop("performedBy", "Unknown")
    userName = task_dict.pop("userName", "Unknown User")
    
    # Auto-assign assignedById and Name to the creator if not explicitly passed
    if not task_dict.get("assignedById"):
        task_dict["assignedById"] = performedBy
    if not task_dict.get("assignedByName"):
        task_dict["assignedByName"] = userName
    
    if task_dict.get("assignedToIds") and len(task_dict.get("assignedToIds")) > 0:
        names = []
        for emp_id in task_dict["assignedToIds"]:
            try:
                employee = await db.employees.find_one({"_id": ObjectId(emp_id)})
                if employee:
                    names.append(f"{employee.get('firstName')} {employee.get('lastName')}")
            except Exception:
                pass
        task_dict["assignedToNames"] = names
        
        # for backwards compatibility if only 1 user assigned
        if len(task_dict["assignedToIds"]) == 1:
            task_dict["assignedToId"] = task_dict["assignedToIds"][0]
            task_dict["assignedToName"] = task_dict["assignedToNames"][0] if names else None

    elif task_dict.get("assignedToId"):
        try:
            employee = await db.employees.find_one({"_id": ObjectId(task_dict["assignedToId"])})
            if employee:
                if not task_dict.get("assignedToName"):
                    task_dict["assignedToName"] = f"{employee.get('firstName')} {employee.get('lastName')}"
                if not task_dict.get("assignedToIds"):
                    task_dict["assignedToIds"] = [task_dict["assignedToId"]]
                    task_dict["assignedToNames"] = [task_dict["assignedToName"]]
        except Exception:
            pass

    if not task_dict.get("createdDate"):
        task_dict["createdDate"] = get_now().strftime("%Y-%m-%d")
        
    result = await db.tasks.insert_one(task_dict)
    taskId = str(result.inserted_id)
    
    task_dict["_id"] = result.inserted_id
    await log_activity(db, "Created", performedBy, userName, f"Created a new task '{task_dict.get('title')}'", taskId=taskId)
    
    # Send notification to assignee(s)
    try:
        assignee_ids = task_dict.get("assignedToIds", [])
        if task_dict.get("assignedToId") and task_dict["assignedToId"] not in assignee_ids:
            assignee_ids.append(task_dict["assignedToId"])
        for assignee_id in assignee_ids:
            if assignee_id and assignee_id != performedBy:
                await create_notification(db, schemas.NotificationCreate(
                    employee_id=assignee_id,
                    title="New Task Assigned",
                    message=f"You have been assigned task '{task_dict.get('title')}'.",
                    type="task",
                    reference_id=taskId
                ))
    except Exception as e_notif:
        print(f"Error notifying assignee on task create: {e_notif}")
    
    return fix_id(task_dict)

async def update_task(db, task_id: str, task: schemas.TaskUpdate):
    update_data = task.dict(exclude_unset=True)
    performedBy = update_data.pop("performedBy", "Unknown")
    userName = update_data.pop("userName", "Unknown User")
    
    if update_data.get("assignedToIds") is not None:
        names = []
        for emp_id in update_data["assignedToIds"]:
            try:
                employee = await db.employees.find_one({"_id": ObjectId(emp_id)})
                if employee:
                    names.append(f"{employee.get('firstName')} {employee.get('lastName')}")
            except Exception:
                pass
        update_data["assignedToNames"] = names
        if len(update_data["assignedToIds"]) == 1:
            update_data["assignedToId"] = update_data["assignedToIds"][0]
            update_data["assignedToName"] = update_data["assignedToNames"][0] if names else None
        else:
            update_data["assignedToId"] = None
            update_data["assignedToName"] = None

    elif "assignedToId" in update_data:
        assign_id = update_data.get("assignedToId")
        if assign_id:
            try:
                employee = await db.employees.find_one({"_id": ObjectId(assign_id)})
                if employee:
                    update_data["assignedToName"] = f"{employee.get('firstName')} {employee.get('lastName')}"
            except Exception:
                pass
        else:
            update_data["assignedToName"] = "Unassigned"

    if update_data:
        await db.tasks.update_one({"_id": ObjectId(task_id)}, {"$set": update_data})
        
        changes = []
        for k, v in update_data.items():
            if k == "title": changes.append(f"title to '{v}'")
            elif k == "description": changes.append(f"description")
            elif k == "dueDate": changes.append(f"due date to {v}")
            elif k == "status": changes.append(f"status to '{str(v).capitalize()}'")
            elif k == "priority": changes.append(f"priority to '{str(v).capitalize()}'")
            elif k == "assignedToId": changes.append(f"assignee to {update_data.get('assignedToName', 'another employee')}")
            
        detail_msg = f"Updated {', '.join(changes)}" if changes else "Updated task details"
        await log_activity(db, "Updated", performedBy, userName, detail_msg, taskId=task_id)
        
    updated = await db.tasks.find_one({"_id": ObjectId(task_id)})
    if updated and update_data.get("status") == "completed":
        freq = updated.get("frequency", "one-time")
        if freq and freq.lower() in ["daily", "every-2-days", "weekly", "monthly"]:
            base_date = None
            if updated.get("dueDate"):
                due_val = updated["dueDate"]
                if isinstance(due_val, str):
                    try:
                        base_date = datetime.strptime(due_val.split("T")[0], "%Y-%m-%d").date()
                    except Exception:
                        pass
                elif isinstance(due_val, (date, datetime)):
                    base_date = due_val if isinstance(due_val, date) else due_val.date()
            
            if not base_date:
                base_date = get_now().date()
                
            freq_lower = freq.lower()
            if freq_lower == "daily":
                next_due = base_date + timedelta(days=1)
            elif freq_lower == "every-2-days":
                next_due = base_date + timedelta(days=2)
            elif freq_lower == "weekly":
                next_due = base_date + timedelta(days=7)
            elif freq_lower == "monthly":
                next_due = base_date + timedelta(days=30)
            else:
                next_due = base_date

            # Ensure that HR tasks only generate on working days (skip Sunday)
            if updated.get("department") == "HR" or (updated.get("department") and str(updated["department"]).upper() == "HR"):
                while next_due.weekday() == 6:  # 6 is Sunday
                    next_due = next_due + timedelta(days=1)
                
            new_task_doc = {
                "title": updated.get("title"),
                "description": updated.get("description"),
                "assignedToId": updated.get("assignedToId"),
                "assignedToName": updated.get("assignedToName"),
                "assignedToIds": updated.get("assignedToIds", []),
                "assignedToNames": updated.get("assignedToNames", []),
                "assignedById": updated.get("assignedById"),
                "assignedByName": updated.get("assignedByName"),
                "dueDate": next_due.strftime("%Y-%m-%d"),
                "status": "todo",
                "priority": updated.get("priority", "medium"),
                "remarks": None,
                "createdDate": get_now().strftime("%Y-%m-%d"),
                "department": updated.get("department"),
                "frequency": freq
            }
            await db.tasks.insert_one(new_task_doc)

    return fix_id(updated) if updated else None

async def delete_task(db, task_id: str):
    task = await db.tasks.find_one({"_id": ObjectId(task_id)})
    if not task:
        return False
        
    await archive_and_delete_one(db, "tasks", {"_id": ObjectId(task_id)})
    await log_activity(db, "Deleted", "Admin", "N/A", f"Task '{task.get('title')}' was deleted.", taskId=task_id)
    return True

# Work Management Task CRUD
async def get_wm_task(db, task_id: str):
    from bson.objectid import ObjectId
    try:
        task = await db.wm_tasks.find_one({"_id": ObjectId(task_id)})
        return fix_id(task) if task else None
    except Exception:
        return None

async def get_wm_tasks(db, userId: Optional[str] = None, role: Optional[str] = None, skip: int = 0, limit: int = 1000):
    query = {}
    if userId and not await user_has_full_entity_access(db, userId, role, "wm-tasks"):
        user = await get_employee(db, userId)
        if user:
            dept = user.get("department")
            
            user_role = str(user.get("role", "")).lower().strip()
            user_desig = str(user.get("designation", "")).lower().strip()
            is_tl = (role and role.lower() == "team leader") or (user_role == "team leader") or (user_desig == "team leader")
            
            tl_proj = await db.projects.find_one({"teamLeaderId": userId})
            
            if is_tl or tl_proj:
                # TL sees tasks assigned to them, tasks in their dept, unassigned tasks in their dept, or tasks they created
                dept_employees = await db.employees.find({"department": dept}).to_list(length=1000)
                dept_emp_ids = [str(e["_id"]) for e in dept_employees]
                
                query["$or"] = [
                    {"assignedToId": {"$in": dept_emp_ids}},
                    {"department": dept},
                    {"performedBy": userId},
                    {"assignedToId": userId}
                ]
            else:
                # Employee sees their own tasks or tasks they created
                query["$or"] = [
                    {"assignedToId": userId},
                    {"performedBy": userId}
                ]
                
    cursor = db.wm_tasks.find(query).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def create_wm_task(db, task: schemas.WMTaskCreate):
    task_dict = task.dict()
    performedBy = task_dict.get("performedBy", "Unknown")
    userName = task_dict.get("userName", "Unknown User")
    
    if not task_dict.get("createdBy"):
        task_dict["createdBy"] = performedBy
    
    if not task_dict.get("projectName") and task_dict.get("projectId"):
        project = await db.projects.find_one({"_id": ObjectId(task_dict["projectId"])})
        if project:
            task_dict["projectName"] = project.get("title")
    
    if task_dict.get("moduleName") and task_dict.get("projectId") and not task_dict.get("assignedToId"):
        try:
            project_obj = await db.projects.find_one({"_id": ObjectId(task_dict["projectId"])})
            if project_obj and project_obj.get("modules"):
                for m in project_obj.get("modules", []):
                    if m.get("name") == task_dict["moduleName"]:
                        mod_assignee = m.get("assignedToId")
                        if mod_assignee and mod_assignee != "unassigned":
                            task_dict["assignedToId"] = mod_assignee
                            task_dict["assignedToName"] = m.get("assignedToName") or "Unassigned"
                        break
        except Exception as e_mod_sync:
            print(f"Error syncing module assignee on task creation: {e_mod_sync}")

    if task_dict.get("assignedToId"):
        try:
            employee = await db.employees.find_one({"_id": ObjectId(task_dict["assignedToId"])})
            if employee:
                if not task_dict.get("assignedToName") or task_dict.get("assignedToName") == "Unassigned":
                    task_dict["assignedToName"] = f"{employee.get('firstName', '')} {employee.get('lastName', '')}".trim() if hasattr(f"{employee.get('firstName', '')} {employee.get('lastName', '')}", "trim") else f"{employee.get('firstName', '')} {employee.get('lastName', '')}".strip()
                if not task_dict.get("department"):
                    task_dict["department"] = employee.get("department")
        except Exception:
            pass

    if not task_dict.get("createdDate"):
        task_dict["createdDate"] = get_now().strftime("%Y-%m-%d")
        
    if task_dict.get("status") == "in-progress" and task_dict.get("assignedToId"):
        await db.wm_tasks.update_many(
            {"assignedToId": task_dict["assignedToId"], "status": "in-progress"},
            {"$set": {"status": "todo"}}
        )

    result = await db.wm_tasks.insert_one(task_dict)
    taskId = str(result.inserted_id)
    
    # Log the creation
    await log_task_activity(db, taskId, "Created", performedBy, userName, f"Task '{task_dict['title']}' was created.")
    
    try:
        await ws_manager.broadcast_all("task_update", {"taskId": taskId})
    except Exception:
        pass
        
    try:
        assignee_id = task_dict.get("assignedToId")
        if assignee_id and assignee_id != performedBy and assignee_id != "System":
            await create_notification(db, schemas.NotificationCreate(
                employee_id=assignee_id,
                title="New Task Assigned",
                message=f"You have been assigned task '{task_dict.get('title')}' in project '{task_dict.get('projectName', 'General')}'.",
                type="wm-task",
                reference_id=taskId
            ))
    except Exception as e_notif:
        print(f"Error notifying assignee on task create: {e_notif}")

    doc = await db.wm_tasks.find_one({"_id": result.inserted_id})
    if doc:
        await sync_module_and_phase_stages(db, doc.get("projectId"), doc.get("moduleName"), doc.get("phase"), performedBy, userName)
    return fix_id(doc)

async def sync_module_and_phase_stages(db, project_id: str, module_name: str, phase_name: str = None, performed_by: str = "System", user_name: str = "System"):
    if not project_id or not module_name:
        return
    try:
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project or not project.get("modules"):
            return
            
        modules = project.get("modules", [])
        target_mod_index = -1
        for idx, m in enumerate(modules):
            if m.get("name") == module_name:
                if phase_name and m.get("phaseName") and m.get("phaseName") != phase_name:
                    continue
                target_mod_index = idx
                break
                
        if target_mod_index == -1:
            return
            
        current_mod = modules[target_mod_index]
        current_stage = str(current_mod.get("stage") or "todo").strip().lower()
        
        query = {"projectId": project_id, "moduleName": module_name}
        if phase_name:
            query["phase"] = phase_name
        tasks = await db.wm_tasks.find(query).to_list(length=1000)
        
        if not tasks:
            return
            
        all_completed_and_approved = all(
            str(t.get("status") or "").strip().lower() == "completed" and t.get("isApproved") is True 
            for t in tasks
        )
        all_completed_or_review = all(
            str(t.get("status") or "").strip().lower() in ["completed", "review", "ready for review"] 
            for t in tasks
        ) and not all_completed_and_approved
        any_bugs = any(str(t.get("status") or "").strip().lower() == "bugs" for t in tasks)
        any_in_progress = any(
            str(t.get("status") or "").strip().lower() in ["in-progress", "in_progress", "review", "ready for review"] 
            for t in tasks
        )
        all_todo = all(str(t.get("status") or "").strip().lower() == "todo" for t in tasks)
        
        target_stage = current_stage
        if all_completed_and_approved:
            target_stage = "completed"
        elif any_bugs:
            target_stage = "bugs"
        elif all_completed_or_review:
            target_stage = "review"
        elif any_in_progress:
            target_stage = "in_progress"
        elif all_todo:
            target_stage = "todo"
        else:
            target_stage = "in_progress"
            
        if current_stage == "onhold" and not all_completed_and_approved and not any_bugs:
            target_stage = current_stage
            
        mod_changed = False
        if target_stage != current_stage:
            modules[target_mod_index]["stage"] = target_stage
            mod_changed = True
            await db.projects.update_one(
                {"_id": ObjectId(project_id)},
                {"$set": {"modules": modules}}
            )
            await log_activity(
                db, "Module Stage Updated", performed_by, user_name,
                f"Module '{module_name}' stage automatically updated to '{target_stage}' based on task progress.",
                projectId=project_id
            )
            
        phase_changed = False
        target_phase_name = phase_name or current_mod.get("phaseName")
        if target_phase_name and project.get("phases"):
            phases = project.get("phases", [])
            for p_idx, p in enumerate(phases):
                if p.get("name") == target_phase_name:
                    current_p_status = str(p.get("status") or "todo").strip().lower()
                    phase_modules = [m for m in modules if m.get("phaseName") == target_phase_name]
                    if phase_modules:
                        all_mods_completed = all(str(m.get("stage") or "").strip().lower() == "completed" for m in phase_modules)
                        any_mods_active = any(str(m.get("stage") or "").strip().lower() in ["in_progress", "in-progress", "review", "bugs", "completed"] for m in phase_modules)
                        
                        target_p_status = current_p_status
                        if all_mods_completed:
                            target_p_status = "completed"
                        elif any_mods_active:
                            target_p_status = "in-progress"
                        else:
                            target_p_status = "todo"
                            
                        if target_p_status != current_p_status:
                            phases[p_idx]["status"] = target_p_status
                            phase_changed = True
                            await db.projects.update_one(
                                {"_id": ObjectId(project_id)},
                                {"$set": {"phases": phases}}
                            )
                            await log_activity(
                                db, "Phase Status Updated", performed_by, user_name,
                                f"Phase '{target_phase_name}' status automatically updated to '{target_p_status}'.",
                                projectId=project_id
                            )
                    break
                    
        if mod_changed or phase_changed:
            try:
                await ws_manager.broadcast_all("data_refresh", {"entity": "projects"})
                await ws_manager.broadcast_all("data_refresh", {"entity": "wm_tasks"})
            except Exception:
                pass
    except Exception as e:
        print(f"Error syncing module and phase stages: {e}")

async def update_wm_task(db, task_id: str, task_update: schemas.WMTaskUpdate):
    update_data = task_update.dict(exclude_unset=True)
    performedBy = update_data.pop("performedBy", "Unknown")
    userName = update_data.pop("userName", "Unknown User")
    
    if update_data:
        # Get old task state for logging
        old_task = await db.wm_tasks.find_one({"_id": ObjectId(task_id)})
        
        if update_data.get("projectId") and not update_data.get("projectName"):
            project = await db.projects.find_one({"_id": ObjectId(update_data["projectId"])})
            if project:
                update_data["projectName"] = project.get("title")
        
        if update_data.get("moduleName") and update_data.get("moduleName") != (old_task.get("moduleName") if old_task else None) and not update_data.get("assignedToId"):
            p_id = update_data.get("projectId") or (old_task.get("projectId") if old_task else None)
            if p_id:
                try:
                    project_obj = await db.projects.find_one({"_id": ObjectId(p_id)})
                    if project_obj and project_obj.get("modules"):
                        for m in project_obj.get("modules", []):
                            if m.get("name") == update_data["moduleName"]:
                                mod_assignee = m.get("assignedToId")
                                if mod_assignee and mod_assignee != "unassigned":
                                    update_data["assignedToId"] = mod_assignee
                                    update_data["assignedToName"] = m.get("assignedToName") or "Unassigned"
                                break
                except Exception as e_mod_update_sync:
                    print(f"Error syncing module assignee on task update: {e_mod_update_sync}")

        if update_data.get("assignedToId"):
            employee = await db.employees.find_one({"_id": ObjectId(update_data["assignedToId"])})
            if employee:
                if not update_data.get("assignedToName"):
                    update_data["assignedToName"] = f"{employee.get('firstName')} {employee.get('lastName')}"
                if not update_data.get("department"):
                    update_data["department"] = employee.get("department")
                
        target_assignee = update_data.get("assignedToId") or (old_task.get("assignedToId") if old_task else None)
        if update_data.get("status") == "in-progress" and target_assignee:
            await db.wm_tasks.update_many(
                {"assignedToId": target_assignee, "status": "in-progress", "_id": {"$ne": ObjectId(task_id)}},
                {"$set": {"status": "todo"}}
            )

        if "status" in update_data:
            new_status = str(update_data["status"]).strip().lower()
            if new_status != "completed":
                update_data["isApproved"] = False

        await db.wm_tasks.update_one({"_id": ObjectId(task_id)}, {"$set": update_data})
        
        log_details, diffs = format_field_changes(old_task, update_data, f"Task '{old_task.get('title')}'")
        await log_task_activity(db, task_id, "Updated", performedBy, userName, log_details, diffs=diffs)

        try:
            new_assignee_id = update_data.get("assignedToId")
            old_assignee_id = old_task.get("assignedToId") if old_task else None
            if new_assignee_id and new_assignee_id != old_assignee_id and new_assignee_id != performedBy:
                await create_notification(db, schemas.NotificationCreate(
                    employee_id=new_assignee_id,
                    title="Task Re-assigned",
                    message=f"You have been assigned task '{update_data.get('title') or (old_task.get('title') if old_task else '')}' in project '{update_data.get('projectName') or (old_task.get('projectName') if old_task else 'General')}'.",
                    type="wm-task",
                    reference_id=task_id
                ))
        except Exception as e_notif:
            print(f"Error notifying assignee on task update: {e_notif}")

        try:
            new_status = str(update_data.get("status") or "").strip().lower()
            old_status = str(old_task.get("status") or "").strip().lower() if old_task else ""
            if new_status in ["review", "ready for review"] and new_status != old_status:
                creator_id = old_task.get("createdBy") or old_task.get("reviewByTL") if old_task else None
                if not creator_id and old_task and old_task.get("projectId") and len(str(old_task.get("projectId"))) == 24:
                    project = await db.projects.find_one({"_id": ObjectId(old_task["projectId"])})
                    if project:
                        creator_id = project.get("teamLeaderId")
                if creator_id and creator_id != performedBy and creator_id != (old_task.get("assignedToId") if old_task else None):
                    await create_notification(db, schemas.NotificationCreate(
                        employee_id=creator_id,
                        title="Task Ready for Review",
                        message=f"{(old_task.get('assignedToName') if old_task else 'An employee')} has submitted task '{(old_task.get('title') if old_task else '')}' for your review.",
                        type="wm-task",
                        reference_id=task_id
                    ))
        except Exception as e_notif:
            print(f"Error notifying reviewer on task stage update: {e_notif}")
    
    doc = await db.wm_tasks.find_one({"_id": ObjectId(task_id)})
    if doc:
        await sync_module_and_phase_stages(db, doc.get("projectId"), doc.get("moduleName"), doc.get("phase"), performedBy, userName)
    return fix_id(doc)

async def delete_wm_task(db, task_id: str):
    # Get task info before deletion for logging
    task = await db.wm_tasks.find_one({"_id": ObjectId(task_id)})
    result = await db.wm_tasks.delete_one({"_id": ObjectId(task_id)})
    
    if result.deleted_count > 0 and task:
        await log_task_activity(db, task_id, "Deleted", "Admin/User", "N/A", f"Task '{task.get('title')}' was deleted.")
        await sync_module_and_phase_stages(db, task.get("projectId"), task.get("moduleName"), task.get("phase"), "Admin/User", "Admin/User")
        
    return result.deleted_count > 0

# Universal Field Diffing Helper
def format_field_changes(old_doc: dict, update_data: dict, entity_prefix: str = "") -> tuple:
    ignore_keys = {
        "performedBy", "userName", "updated_at", "updatedAt", "_id", "id", 
        "statusHistory", "createdDate", "createdAt", "createdBy", "timestamp",
        "clientName", "teamLeaderName", "assignedToName", "projectName", "employeeName"
    }
    
    field_labels = {
        "title": "Title",
        "description": "Description",
        "status": "Status/Stage",
        "priority": "Priority",
        "startDate": "Start Date",
        "endDate": "End Date",
        "dueDate": "Due Date",
        "teamDeadline": "Team Deadline",
        "clientDeadline": "Client Deadline",
        "moduleName": "Phase Name",
        "moduleDeadline": "Phase Deadline",
        "remarks": "Remarks",
        "assignedToId": "Assignee",
        "teamLeaderId": "Team Leader",
        "clientId": "Client",
        "companyName": "Company Name",
        "firstName": "First Name",
        "lastName": "Last Name",
        "email": "Email",
        "phone": "Phone",
        "department": "Department",
        "designation": "Designation",
        "services": "Services Description",
        "post": "Post Count",
        "reel": "Reel Count",
        "festivalPost": "Festival Post Requirement",
        "graphicsRequired": "Graphics Requirement",
        "postRequired": "Post Requirement",
        "reelRequired": "Reel Requirement",
        "isPhaseWise": "Phase Wise Project",
        "phases": "Project Phases",
        "modules": "Project Modules",
        "postingDate": "Posting Date",
        "postingDay": "Posting Day",
        "reelPost": "Deliverable Type",
        "concept": "Concept Notes",
        "reference": "Reference Link",
        "scriptLink": "Script Link",
        "shootingLink": "Shooting Link",
        "editingLink": "Editing Link",
        "finalLink": "Final Deliverable Link",
        "reviewByTL": "TL Review Remarks",
        "postingStatus": "Posting Status",
        "note": "Note",
        "meetingDate": "Meeting Date",
        "amount": "Amount",
        "paymentStatus": "Payment Status",
        "conclusion": "Conclusion",
        "campaigns": "Campaigns",
        "adSpend": "Ad Spend",
        "leads": "Leads Count",
        "reach": "Reach",
        "impressions": "Impressions",
        "clicks": "Clicks",
        "conversions": "Conversions",
        "salary": "Salary",
    }
    
    details = []
    diffs = []
    for k, new_val in update_data.items():
        if k in ignore_keys:
            continue
            
        old_val = old_doc.get(k) if old_doc else None
        
        if k == "assignedToId":
            new_disp = update_data.get("assignedToName", old_doc.get("assignedToName", str(new_val)) if old_doc else str(new_val))
            old_disp = old_doc.get("assignedToName", str(old_val)) if old_doc else str(old_val)
            if old_disp != new_disp:
                details.append(f"Assignee changed from '{old_disp}' to '{new_disp}'")
                diffs.append({"field": "Assignee", "old": old_disp, "new": new_disp})
            continue
        if k == "teamLeaderId":
            new_disp = update_data.get("teamLeaderName", old_doc.get("teamLeaderName", str(new_val)) if old_doc else str(new_val))
            old_disp = old_doc.get("teamLeaderName", str(old_val)) if old_doc else str(old_val)
            if old_disp != new_disp:
                details.append(f"Team Leader changed from '{old_disp}' to '{new_disp}'")
                diffs.append({"field": "Team Leader", "old": old_disp, "new": new_disp})
            continue
        if k == "clientId":
            new_disp = update_data.get("clientName", old_doc.get("clientName", str(new_val)) if old_doc else str(new_val))
            old_disp = old_doc.get("clientName", str(old_val)) if old_doc else str(old_val)
            if old_disp != new_disp:
                details.append(f"Client changed from '{old_disp}' to '{new_disp}'")
                diffs.append({"field": "Client", "old": old_disp, "new": new_disp})
            continue
            
        if old_val is None: old_val = ""
        if new_val is None: new_val = ""
        
        if isinstance(new_val, (list, dict)) or isinstance(old_val, (list, dict)):
            if old_val != new_val:
                label = field_labels.get(k, k[0].upper() + k[1:] if k else "Field")
                details.append(f"Updated {label}")
                diffs.append({"field": label, "old": str(old_val), "new": str(new_val)})
            continue
            
        # We do NOT use .strip() so exact space changes are detected!
        old_str = str(old_val)
        new_str = str(new_val)
        
        if k == "isApproved":
            old_str = "Approved" if old_val else "Not Approved"
            new_str = "Approved" if new_val else "Not Approved"
        
        if old_str.endswith(".0"): old_str = old_str[:-2]
        if new_str.endswith(".0"): new_str = new_str[:-2]
        
        if old_str != new_str:
            label = field_labels.get(k, ''.join([' ' + c if c.isupper() else c for c in k]).capitalize().strip() if k else "Field")
            
            diffs.append({"field": label, "old": old_str, "new": new_str})
            
            disp_old = (old_str[:40] + '...') if len(old_str) > 40 else old_str
            disp_new = (new_str[:40] + '...') if len(new_str) > 40 else new_str
            
            if not old_str:
                details.append(f"Set {label} to '{disp_new}'")
            elif not new_str:
                details.append(f"Removed {label} (was '{disp_old}')")
            else:
                details.append(f"{label} changed from '{disp_old}' to '{disp_new}'")
                
    prefix_str = f"{entity_prefix}: " if entity_prefix else ""
    if not details:
        return f"{prefix_str}Saved without changes", diffs
        
    return prefix_str + ", ".join(details), diffs

# Activity Log CRUD
async def resolve_performer(db, current_user) -> tuple:
    """Returns (user_id, user_name) based on current_user token payload."""
    if not current_user:
        return "System", "System User"
    
    user_id = current_user.get("sub")
    if not user_id:
        return "Unknown", "Unknown User"
        
    try:
        user = await db.employees.find_one({"_id": ObjectId(user_id) if len(user_id) == 24 else user_id})
        if user:
            name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
            return user_id, name or "Unknown User"
    except Exception:
        pass
    return user_id, "Unknown User"

async def log_activity(db, action: str, performedBy: str, userName: str, details: str, diffs: list = None, taskId: str = None, projectId: str = None, clientId: str = None, leadId: str = None, dailyReportId: str = None, monthlyReportId: str = None, applicationId: str = None, assetId: str = None, categoryId: str = None, employeeId: str = None, leaveId: str = None, attendanceId: str = None, remarkId: str = None):
    log_entry = {
        "action": action,
        "performedBy": performedBy,
        "userName": userName,
        "details": details,
        "timestamp": get_now().strftime("%Y-%m-%d %H:%M:%S")
    }
    if diffs: log_entry["diffs"] = diffs
    if taskId: log_entry["taskId"] = taskId
    if projectId: log_entry["projectId"] = projectId
    if clientId: log_entry["clientId"] = clientId
    if leadId: log_entry["leadId"] = leadId
    if dailyReportId: log_entry["dailyReportId"] = dailyReportId
    if monthlyReportId: log_entry["monthlyReportId"] = monthlyReportId
    if applicationId: log_entry["applicationId"] = applicationId
    if assetId: log_entry["assetId"] = assetId
    if categoryId: log_entry["categoryId"] = categoryId
    if employeeId: log_entry["employeeId"] = employeeId
    if leaveId: log_entry["leaveId"] = leaveId
    if attendanceId: log_entry["attendanceId"] = attendanceId
    if remarkId: log_entry["remarkId"] = remarkId
    
    await db.task_logs.insert_one(log_entry)

async def get_category_logs(db, category_id: str = None):
    query = {"categoryId": category_id} if category_id else {"categoryId": {"$exists": True}}
    cursor = db.task_logs.find(query).sort("timestamp", -1)
    logs = []
    async for doc in cursor:
        logs.append(fix_id(doc))
    return logs

async def get_task_activities(db, task_id: str):
    cursor = db.task_logs.find({"taskId": task_id}).sort("timestamp", -1)
    logs = await cursor.to_list(length=100)
    return [fix_id(log) for log in logs]

async def get_application_logs(db, application_id: str):
    cursor = db.task_logs.find({"applicationId": application_id}).sort("timestamp", -1)
    logs = []
    async for doc in cursor:
        logs.append(fix_id(doc))
    return logs

async def get_lead_logs(db, lead_id: str):
    cursor = db.task_logs.find({"leadId": lead_id}).sort("timestamp", -1)
    logs = []
    async for doc in cursor:
        logs.append(fix_id(doc))
    return logs

async def get_asset_logs(db, asset_id: str = None):
    query = {"assetId": asset_id} if asset_id else {"assetId": {"$exists": True}}
    cursor = db.task_logs.find(query).sort("timestamp", -1)
    logs = []
    async for doc in cursor:
        logs.append(fix_id(doc))
    return logs

async def get_task_logs(db, taskId: str = None, projectId: str = None, clientId: str = None, dailyReportId: str = None, monthlyReportId: str = None):
    query = {}
    if taskId: query["taskId"] = taskId
    if projectId: query["projectId"] = projectId
    if clientId: query["clientId"] = clientId
    if dailyReportId: query["dailyReportId"] = dailyReportId
    if monthlyReportId: query["monthlyReportId"] = monthlyReportId
    
    cursor = db.task_logs.find(query).sort("timestamp", -1)
    rows = await cursor.to_list(length=100)
    return [fix_id(row) for row in rows]

async def get_all_activity_logs(db, performedBy: str = None, action: str = None, search: str = None, startDate: str = None, endDate: str = None, limit: int = 50, skip: int = 0):
    query = {}
    if performedBy:
        query["userName"] = {"$regex": performedBy, "$options": "i"}
    if action:
        query["action"] = action
    if search:
        query["$or"] = [
            {"details": {"$regex": search, "$options": "i"}},
            {"action": {"$regex": search, "$options": "i"}},
            {"userName": {"$regex": search, "$options": "i"}},
            {"performedBy": {"$regex": search, "$options": "i"}}
        ]
    if startDate or endDate:
        time_query = {}
        if startDate:
            time_query["$gte"] = f"{startDate} 00:00:00"
        if endDate:
            time_query["$lte"] = f"{endDate} 23:59:59"
        query["timestamp"] = time_query

    cursor = db.task_logs.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    total = await db.task_logs.count_documents(query)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows], total


async def get_finance_activity_logs(db, performedBy: str = None, action: str = None, search: str = None, startDate: str = None, endDate: str = None, limit: int = 50, skip: int = 0):
    query = {
        "action": {"$regex": "^Finance ", "$options": "i"}
    }
    if performedBy:
        query["userName"] = {"$regex": performedBy, "$options": "i"}
    if action:
        query["action"] = action
    if search:
        query["$and"] = [
            {"action": {"$regex": "^Finance ", "$options": "i"}},
            {"$or": [
                {"details": {"$regex": search, "$options": "i"}},
                {"action": {"$regex": search, "$options": "i"}},
                {"userName": {"$regex": search, "$options": "i"}},
                {"performedBy": {"$regex": search, "$options": "i"}}
            ]}
        ]
    if startDate or endDate:
        time_query = {}
        if startDate:
            time_query["$gte"] = f"{startDate} 00:00:00"
        if endDate:
            time_query["$lte"] = f"{endDate} 23:59:59"
        query["timestamp"] = time_query

    cursor = db.task_logs.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    total = await db.task_logs.count_documents(query)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows], total


async def update_task_log(db, log_id: str, new_details: str):
    await db.task_logs.update_one({"_id": ObjectId(log_id)}, {"$set": {"details": new_details}})
    doc = await db.task_logs.find_one({"_id": ObjectId(log_id)})
    return fix_id(doc) if doc else None

async def delete_task_log(db, log_id: str):
    result = await db.task_logs.delete_one({"_id": ObjectId(log_id)})
    return result.deleted_count > 0

# Update existing log_task_activity calls to use the new log_activity
async def log_task_activity(db, taskId: str, action: str, performedBy: str, userName: str, details: str, diffs: list = None):
    await log_activity(db, action, performedBy, userName, details, diffs=diffs, taskId=taskId)


# Sales Lead CRUD
async def get_leads(db, skip: int = 0, limit: int = 100):
    cursor = db.leads.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def create_leads_bulk(db, leads: List[schemas.LeadCreate]):
    if not leads:
        return []
    
    lead_dicts = []
    log_dicts = []
    log_time = get_now()
    
    for lead in leads:
        lead_dict = lead.dict()
        performedBy = lead_dict.pop("performedBy", "Unknown")
        userName = lead_dict.pop("userName", "Unknown User")
        
        if not lead_dict.get("date"):
            lead_dict["date"] = log_time.strftime("%Y-%m-%d")
            
        if lead_dict.get("status") in ["On Hold", "Client Won", "Client Loss"]:
            lead_dict["isHot"] = False
            
        lead_dict["createdBy"] = performedBy
        lead_dict["createdByUserName"] = userName
        lead_dicts.append(lead_dict)
        
    if lead_dicts:
        result = await db.leads.insert_many(lead_dicts)
        
        # log activity for all
        for i, lead_id in enumerate(result.inserted_ids):
            lead_dict = lead_dicts[i]
            lead_dict["_id"] = lead_id  # for fix_id
            log_dicts.append({
                "action": "Lead Created",
                "performedBy": lead_dict["createdBy"],
                "userName": lead_dict["createdByUserName"],
                "details": f"Lead for '{lead_dict['company']}' was created.",
                "leadId": str(lead_id),
                "timestamp": log_time
            })
            
        if log_dicts:
            await db.activity_logs.insert_many(log_dicts)
            
    return [fix_id(doc) for doc in lead_dicts]

async def create_lead(db, lead: schemas.LeadCreate):
    lead_dict = lead.dict()
    performedBy = lead_dict.pop("performedBy", "Unknown")
    userName = lead_dict.pop("userName", "Unknown User")
    
    if not lead_dict.get("date"):
        lead_dict["date"] = get_now().strftime("%Y-%m-%d")
        
    if lead_dict.get("status") in ["On Hold", "Client Won", "Client Loss"]:
        lead_dict["isHot"] = False
        
    # Store creator information
    lead_dict["createdBy"] = performedBy
    lead_dict["createdByUserName"] = userName
        
    result = await db.leads.insert_one(lead_dict)
    lead_id = str(result.inserted_id)
    
    # Log the creation
    await log_activity(db, "Lead Created", performedBy, userName, f"Lead for '{lead_dict['company']}' was created.", leadId=lead_id)
    
    try:
        assigned_to = lead_dict.get("assignedTo", [])
        if not isinstance(assigned_to, list):
            assigned_to = [assigned_to] if assigned_to else []
        for emp_name in assigned_to:
            if not emp_name: continue
            if isinstance(emp_name, dict):
                emp_name = emp_name.get("value", "") or emp_name.get("label", "")
            if not emp_name: continue
            import re
            emp_name_escaped = re.escape(str(emp_name).strip())
            emp = await db.employees.find_one({"name": {"$regex": f"^{emp_name_escaped}$", "$options": "i"}})
            if emp:
                emp_id_str = str(emp["_id"]) if "_id" in emp else emp.get("id")
                if emp_id_str and emp_id_str != performedBy:
                    await create_notification(db, schemas.NotificationCreate(
                        employee_id=emp_id_str,
                        title="New Lead Assigned",
                        message=f"You have been assigned sales lead '{lead_dict.get('company') or lead_dict.get('contact', 'Unknown')}'.",
                        type="lead",
                        reference_id=lead_id
                    ))
    except Exception as e_notif:
        print(f"Error notifying lead assignee on create: {e_notif}")

    doc = await db.leads.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def update_lead(db, lead_id: str, lead_update: schemas.LeadUpdate):
    try:
        oid = ObjectId(lead_id)
    except Exception:
        return None
        
    update_data = lead_update.dict(exclude_unset=True)
    performedBy = update_data.pop("performedBy", "Unknown")
    userName = update_data.pop("userName", "Unknown User")
    reason = update_data.pop("reason", None)
    
    if update_data:
        # If status changed to 'Client Won', set closedDate if not provided
        old_lead = await db.leads.find_one({"_id": oid})
        if not old_lead:
            return None
            
        if update_data.get("status") == "Client Won" and old_lead.get("status") != "Client Won":
            if not update_data.get("closedDate"):
                update_data["closedDate"] = get_now().strftime("%Y-%m-%d")
            
        if update_data.get("status") in ["On Hold", "Client Won", "Client Loss"]:
            update_data["isHot"] = False
            
        if update_data.get("status") == "On Hold" and "holdResumeDate" not in update_data:
            update_data["holdResumeDate"] = None
            
        await db.leads.update_one({"_id": oid}, {"$set": update_data})
        
        # Log the update with detailed changes
        changes = []
        ALLOWED_LOG_FIELDS = ["company", "contact", "email", "phone", "expectedIncome", "status", "priority", "source", "date", "remarks", "assignedTo", "holdResumeDate", "isHot", "nextFollowUpDate", "category"]
        for key, new_val in update_data.items():
            if key not in ALLOWED_LOG_FIELDS:
                continue
            old_val = old_lead.get(key)
            if old_val != new_val:
                if key == "holdResumeDate":
                    display_key = "Resume Date"
                elif key == "nextFollowUpDate":
                    display_key = "Next Follow-up Date"
                elif key == "isHot":
                    display_key = "Hot Status"
                else:
                    display_key = key.replace("_", " ").title()
                    
                if key == "expectedIncome":
                    changes.append(f"{display_key} changed from ₹{old_val or 0} to ₹{new_val}")
                elif key == "isHot":
                    old_str = "Yes" if old_val else "No"
                    new_str = "Yes" if new_val else "No"
                    changes.append(f"{display_key} changed from '{old_str}' to '{new_str}'")
                else:
                    changes.append(f"{display_key} changed from '{old_val or 'None'}' to '{new_val or 'None'}'")
                    
        if changes:
            details = "; ".join(changes)
            if reason:
                details += f" (Reason: {reason})"
            await log_activity(db, "Lead Updated", performedBy, userName, details, leadId=lead_id)
        
        # Trigger recalculation if status is Client Won or assignedTo changed
        if update_data.get("status") == "Client Won" or "assignedTo" in update_data:
            updated_lead = await db.leads.find_one({"_id": oid})
            assigned_to = updated_lead.get("assignedTo", [])
            # assignedTo is a list of names
            if not isinstance(assigned_to, list):
                assigned_to = [assigned_to] if assigned_to else []
            
            for emp_name in assigned_to:
                if not emp_name:
                    continue
                if isinstance(emp_name, dict):
                    emp_name = emp_name.get("value", "") or emp_name.get("label", "")
                if not emp_name:
                    continue
                import re
                emp_name_escaped = re.escape(str(emp_name).strip())
                emp = await db.employees.find_one({"name": {"$regex": f"^{emp_name_escaped}$", "$options": "i"}})
                if not emp:
                    continue
                ld_str = updated_lead.get("closedDate") or updated_lead.get("date")
                try:
                    ld = None
                    if isinstance(ld_str, datetime):
                        ld = ld_str.replace(tzinfo=None)  # strip timezone for safe comparison
                    elif ld_str:
                        for fmt in ("%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                ld = datetime.strptime(str(ld_str).strip(), fmt)
                                break
                            except Exception: continue
                    
                    if ld:
                        month_name = ld.strftime("%B")
                        week_num = (ld.day - 1) // 7 + 1
                        emp_id_str = str(emp["_id"])
                        # Recalculate Monthly
                        await recalculate_sales_target(db, emp_id_str, month_name, ld.year, "Monthly")
                        # Recalculate Weekly
                        await recalculate_sales_target(db, emp_id_str, month_name, ld.year, "Weekly", week_num)
                        emp_obj_id = ObjectId(emp_id_str) if len(emp_id_str) == 24 else None
                        emp_query = [emp_id_str]
                        if emp_obj_id:
                            emp_query.append(emp_obj_id)
                            
                        custom_targets_cursor = db.sales_targets.find({
                            "employeeId": {"$in": emp_query},
                            "type": "Custom"
                        })
                        async for t in custom_targets_cursor:
                            await recalculate_sales_target(
                                db,
                                emp_id_str,
                                t.get("month"),
                                t.get("year"),
                                "Custom",
                                startDate=t.get("startDate"),
                                endDate=t.get("endDate")
                            )
                        # Auto-reprocess payroll to update the incentives and totals
                        await run_payroll_processing(db, month_name, ld.year)
                except Exception as e:
                    print(f"Error auto-processing payroll on lead won for {emp_name}: {e}")
                    
        if "assignedTo" in update_data:
            try:
                assigned_to = update_data.get("assignedTo", [])
                if not isinstance(assigned_to, list):
                    assigned_to = [assigned_to] if assigned_to else []
                for emp_name in assigned_to:
                    if not emp_name: continue
                    if isinstance(emp_name, dict):
                        emp_name = emp_name.get("value", "") or emp_name.get("label", "")
                    if not emp_name: continue
                    import re
                    emp_name_escaped = re.escape(str(emp_name).strip())
                    emp = await db.employees.find_one({"name": {"$regex": f"^{emp_name_escaped}$", "$options": "i"}})
                    if emp:
                        emp_id_str = str(emp["_id"]) if "_id" in emp else emp.get("id")
                        if emp_id_str and emp_id_str != performedBy:
                            await create_notification(db, schemas.NotificationCreate(
                                employee_id=emp_id_str,
                                title="Lead Re-assigned",
                                message=f"You have been assigned sales lead '{update_data.get('company') or (old_lead.get('company') if old_lead else '') or 'Unknown'}'.",
                                type="lead",
                                reference_id=lead_id
                            ))
            except Exception as e_notif:
                print(f"Error notifying lead assignee on update: {e_notif}")
        
    doc = await db.leads.find_one({"_id": ObjectId(lead_id)})
    return fix_id(doc)

async def delete_lead(db, lead_id: str):
    try:
        result = await db.leads.delete_one({"_id": ObjectId(lead_id)})
        return result.deleted_count > 0
    except Exception:
        return False

async def add_lead_follow_up(db, lead_id: str, follow_up: schemas.FollowUp, performedBy: str = "Unknown", userName: str = "Unknown User"):
    follow_up_dict = follow_up.dict()
    next_follow_up_date = follow_up_dict.get("nextFollowUpDate", None)
    if not follow_up_dict.get("date"):
        follow_up_dict["date"] = get_now().strftime("%Y-%m-%d %H:%M")
        
    await db.leads.update_one(
        {"_id": ObjectId(lead_id)},
        {
            "$push": {"followUps": follow_up_dict},
            "$set": {"nextFollowUpDate": next_follow_up_date}
        }
    )
    
    # Log activity
    log_detail = f"Added follow-up: {follow_up_dict.get('note', 'No notes provided')}"
    if next_follow_up_date:
        try:
            date_str = next_follow_up_date.strftime("%Y-%m-%d")
        except AttributeError:
            date_str = str(next_follow_up_date)
        log_detail += f" (Next follow-up date: {date_str})"
    await log_activity(db, "Follow-up Added", performedBy, userName, log_detail, leadId=lead_id)
    
    doc = await db.leads.find_one({"_id": ObjectId(lead_id)})
    return fix_id(doc)

async def update_lead_follow_up(db, lead_id: str, follow_up_idx: int, follow_up: schemas.FollowUp, performedBy: str = "Unknown", userName: str = "Unknown User"):
    follow_up_dict = follow_up.dict()
    
    # Update specific element in the array using positional operator
    # followUps.0, followUps.1, etc.
    await db.leads.update_one(
        {"_id": ObjectId(lead_id)},
        {"$set": {f"followUps.{follow_up_idx}": follow_up_dict}}
    )
    
    # Log activity
    await log_activity(db, "Follow-up Updated", performedBy, userName, f"Updated follow-up at index {follow_up_idx}: {follow_up_dict.get('note', 'No notes provided')}", leadId=lead_id)
    
    doc = await db.leads.find_one({"_id": ObjectId(lead_id)})
    return fix_id(doc)

# Project Finance Follow-ups
async def add_project_finance_follow_up(db, project_id: str, follow_up: schemas.FollowUp, performedBy: str = "Unknown", userName: str = "Unknown User"):
    follow_up_dict = follow_up.dict()
    next_follow_up_date = follow_up_dict.get("nextFollowUpDate", None)
    if not follow_up_dict.get("date"):
        follow_up_dict["date"] = get_now().strftime("%Y-%m-%d %H:%M")
        
    update_fields: dict = {"$push": {"financeFollowUps": follow_up_dict}}
    if follow_up_dict.get("projectStatus"):
        update_fields["$set"] = {"status": follow_up_dict.get("projectStatus")}
        
    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        update_fields
    )
    
    # Log activity
    log_detail = f"Added finance follow-up: {follow_up_dict.get('note', 'No notes provided')}"
    if next_follow_up_date:
        try:
            date_str = next_follow_up_date.strftime("%Y-%m-%d")
        except AttributeError:
            date_str = str(next_follow_up_date)
        log_detail += f" (Next follow-up date: {date_str})"
    await log_activity(db, "Finance Follow-up Added", performedBy, userName, log_detail, projectId=project_id)
    
    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    project_title = project.get("title", "Project") if project else "Project"
    
    # Target assignee: Finance Manager if assigned, else the person adding the follow-up
    assignee_id = (project.get("assignedFinanceManagerId") if project else None) or performedBy
    assignee_name = (project.get("assignedFinanceManagerName") if project else None) or userName

    # 1. Create reminder task for Next Follow-up Date if set
    if next_follow_up_date:
        try:
            due_d = next_follow_up_date.strftime("%Y-%m-%d") if hasattr(next_follow_up_date, "strftime") else str(next_follow_up_date).split("T")[0].split(" ")[0]
        except Exception:
            due_d = str(next_follow_up_date)
            
        task_obj = schemas.TaskCreate(
            title=f"Finance Follow-up Reminder: {project_title}",
            description=f"Take finance follow-up for {project_title}.\nNote: {follow_up_dict.get('note', '')}",
            assignedToId=assignee_id,
            assignedToName=assignee_name,
            assignedToIds=[assignee_id] if assignee_id else [],
            assignedToNames=[assignee_name] if assignee_name else [],
            assignedById=performedBy,
            assignedByName=userName,
            dueDate=due_d,
            status="todo",
            priority="high",
            performedBy=performedBy,
            userName=userName
        )
        try:
            await create_task(db, task_obj)
        except Exception as e:
            print("Error creating finance follow-up reminder task:", e)

    # 2. Create reminder task for Next Payment Date if set
    next_pay_date = follow_up_dict.get("nextPaymentDate")
    if next_pay_date:
        amt_str = f" (₹{follow_up_dict.get('amountReceived')})" if follow_up_dict.get("amountReceived") else ""
        task_obj = schemas.TaskCreate(
            title=f"Payment Due Reminder: {project_title}{amt_str}",
            description=f"Payment expected for project {project_title}.\nNote: {follow_up_dict.get('note', '')}",
            assignedToId=assignee_id,
            assignedToName=assignee_name,
            assignedToIds=[assignee_id] if assignee_id else [],
            assignedToNames=[assignee_name] if assignee_name else [],
            assignedById=performedBy,
            assignedByName=userName,
            dueDate=str(next_pay_date),
            status="todo",
            priority="urgent",
            performedBy=performedBy,
            userName=userName
        )
        try:
            await create_task(db, task_obj)
        except Exception as e:
            print("Error creating payment due reminder task:", e)

    return fix_id(project)

# System Settings CRUD
async def get_system_settings(db):
    settings = await db.system_settings.find_one({})
    if not settings:
        # Create default settings if none exist
        default_settings = {
            "clientVisibilityAdminOnly": True,
            "latePunchDeductionEnabled": True,
            "officeStartTime": "09:30",
            "officeEndTime": "18:30",
            "lateBufferMins": 10,
            "financeOtpEmail": "",
            "inactivityTimeoutEnabled": False,
            "inactivityTimeoutMins": 5,
            "allowedMonthlyPaidLeaves": 1,
            "companyGstin": "24AAXFN3372M1ZK",
            "otherCategories": ["Activity", "Meeting"],
            "companyAddress": "FLAT-204, 2nd FLOOR, RS NO-67/1, WING-A, HARIKRUSHANA COMPLEX, OPP. BHAGAT NAGAR, VED, GURUKULROAD, KATARGAM, SURAT- 395004, GUJARAT, INDIA.",
            "companyPhone": "+91 87805 64463",
            "companyEmail": "billing@hkdigiverse.com",
            "companyPan": "AAXFN3372M",
            "companyLlpin": "ACK-1143",
            "companyState": "24",
            "bankName": "Axis Bank",
            "bankAccountNumber": "924020057377415",
            "bankIfscCode": "UTIB0002891",
            "taxInvoicePrefix": "INV",
            "proformaInvoicePrefix": "PINV",
            "invoiceColor1": "#08304b",
            "invoiceColor2": "#08304b",
            "defaultSac": "",
            "showNamesInRemarksToAdmin": True,
            "leadCategories": ["Hot Lead", "Warm Lead", "Cold Lead"]
        }
        result = await db.system_settings.insert_one(default_settings)
        settings = await db.system_settings.find_one({"_id": result.inserted_id})
    else:
        if "leadCategories" not in settings:
            settings["leadCategories"] = ["Hot Lead", "Warm Lead", "Cold Lead"]
            await db.system_settings.update_one({"_id": settings["_id"]}, {"$set": {"leadCategories": ["Hot Lead", "Warm Lead", "Cold Lead"]}})
    return fix_id(settings)

async def update_system_settings(db, settings_update: schemas.SystemSettingsUpdate):
    update_data = settings_update.dict(exclude_unset=True)
    if update_data:
        old_settings = await db.system_settings.find_one({}) or {}
        old_email = old_settings.get("companyEmail")
        new_email = update_data.get("companyEmail")
        
        await db.system_settings.update_one({}, {"$set": update_data}, upsert=True)
        
        if "companyEmail" in update_data and old_email and new_email and old_email != new_email:
            await db.system_logs.insert_one({
                "event": "companyEmail_changed",
                "old_email": old_email,
                "new_email": new_email,
                "timestamp": get_now().isoformat()
            })
            send_caution_email(old_email, new_email)
            
    return await get_system_settings(db)
# Marketing Reports CRUD
async def create_marketing_daily_report(db, report: schemas.MarketingDailyReportCreate):
    report_dict = report.dict()
    if "date" in report_dict and hasattr(report_dict["date"], "strftime"):
        report_dict["date"] = report_dict["date"].strftime("%Y-%m-%d")
        
    if "campaignName" in report_dict and report_dict["campaignName"] and "clientId" in report_dict and report_dict["clientId"]:
        client = await db.clients.find_one({"_id": ObjectId(report_dict["clientId"])})
        if client:
            campaigns = client.get("campaigns", [])
            camp_name = report_dict["campaignName"]
            exists = False
            for c in campaigns:
                if isinstance(c, str) and c == camp_name:
                    exists = True
                    break
                elif isinstance(c, dict) and c.get("name") == camp_name:
                    exists = True
                    break
            if not exists:
                campaigns.append({"name": camp_name, "isActive": True})
                await db.clients.update_one(
                    {"_id": ObjectId(report_dict["clientId"])},
                    {"$set": {"campaigns": campaigns}}
                )

    result = await db.marketing_daily_reports.insert_one(report_dict)
    report_dict["id"] = str(result.inserted_id)
    return report_dict

async def get_marketing_daily_reports(db, client_id: str = None, date: str = None, start_date: str = None, end_date: str = None, user_info: dict = None):
    query = {}
    if user_info:
        role = str(user_info.get("role", "")).lower()
        user_id = user_info.get("sub")
        if user_id and not await user_has_full_entity_access(db, user_id, role, "digital-marketing"):
            user_id = user_info.get("sub")
            # Get allowed clients
            allowed_clients = await db.clients.find({"assignedEmployeeId": user_id}).to_list(length=None)
            allowed_client_ids = [str(c["_id"]) for c in allowed_clients]
            
            # Get allowed projects using existing get_projects logic
            allowed_projects = await get_projects(db, userId=user_id, role=role, limit=10000)
            allowed_project_ids = [str(p.get("id", p.get("_id"))) for p in allowed_projects]
            project_client_ids = [str(p.get("clientId")) for p in allowed_projects if p.get("clientId")]
            all_allowed_clients = list(set(allowed_client_ids + project_client_ids))
            
            query["$or"] = [
                {"projectId": {"$in": allowed_project_ids}},
                {"clientId": {"$in": all_allowed_clients}}
            ]
            
            if client_id:
                if client_id not in all_allowed_clients:
                    return []
                query["clientId"] = client_id
                query.pop("$or", None)
    if client_id and "clientId" not in query:
        query["clientId"] = client_id
    if date:
        try:
            parsed_date = datetime.strptime(date, "%Y-%m-%d")
            dt_val = datetime(parsed_date.year, parsed_date.month, parsed_date.day)
            query["date"] = {"$in": [date, parsed_date, dt_val]}
        except Exception:
            query["date"] = date
    elif start_date and end_date:
        query["date"] = {"$gte": start_date, "$lte": end_date}
    cursor = db.marketing_daily_reports.find(query).sort("date", -1)
    reports = []
    async for doc in cursor:
        doc["id"] = str(doc["_id"])
        reports.append(doc)
    return reports

async def generate_missing_daily_reports_for_yesterday(db):
    return {"message": "Auto-generation of marketing daily reports is disabled."}

async def update_marketing_daily_report(db, report_id: str, report: schemas.MarketingDailyReportUpdate):
    update_data = report.dict(exclude_unset=True)
    performedBy = update_data.pop("performedBy", "Unknown")
    userName = update_data.pop("userName", "Unknown User")
    
    if "date" in update_data and hasattr(update_data["date"], "strftime"):
        update_data["date"] = update_data["date"].strftime("%Y-%m-%d")
        
    if not update_data:
        return None
        
    old_report = await db.marketing_daily_reports.find_one({"_id": ObjectId(report_id)})
    await db.marketing_daily_reports.update_one({"_id": ObjectId(report_id)}, {"$set": update_data})
    
    # Log details
    log_details, diffs = format_field_changes(old_report, update_data, "Daily Report")
    client_id = old_report.get("clientId") if old_report else None
    project_id = old_report.get("projectId") if old_report else None
    await log_activity(db, "Updated", performedBy, userName, log_details, diffs=diffs, dailyReportId=report_id, clientId=client_id, projectId=project_id)
        
    doc = await db.marketing_daily_reports.find_one({"_id": ObjectId(report_id)})
    if doc:
        doc["id"] = str(doc["_id"])
        return doc
    return None

async def delete_marketing_daily_report(db, report_id: str):
    result = await db.marketing_daily_reports.update_one(
        {"_id": ObjectId(report_id)},
        {"$set": {"isDeleted": True}}
    )
    return result.modified_count > 0 or result.matched_count > 0

async def bulk_clear_leads_files(db, ids: list, collection_name: str):
    object_ids = [ObjectId(id) for id in ids if ObjectId.is_valid(id)]
    if not object_ids:
        return []
    
    collection = db[collection_name]
    
    # 1. Fetch documents to return their leadsFileUrl
    cursor = collection.find({"_id": {"$in": object_ids}, "leadsFileUrl": {"$ne": None}})
    docs = await cursor.to_list(length=None)
    
    urls_to_delete = [doc.get("leadsFileUrl") for doc in docs if doc.get("leadsFileUrl")]
    
    # 2. Update documents to clear leadsFileUrl
    await collection.update_many(
        {"_id": {"$in": object_ids}},
        {"$set": {"leadsFileUrl": ""}}
    )
    
    return urls_to_delete

async def create_marketing_monthly_report(db, report: schemas.MarketingMonthlyReportCreate):
    report_dict = report.dict()
    result = await db.marketing_monthly_reports.insert_one(report_dict)
    report_dict["id"] = str(result.inserted_id)
    return report_dict

async def get_marketing_monthly_reports(db, client_id: str = None, month: list = None, user_info: dict = None):
    match_query = {}
    allowed_project_ids = []
    all_allowed_clients = []
    is_restricted = False
    
    if user_info:
        role = str(user_info.get("role", "")).lower()
        user_id = user_info.get("sub")
        if user_id and not await user_has_full_entity_access(db, user_id, role, "digital-marketing"):
            is_restricted = True
            user_id = user_info.get("sub")
            # Get allowed clients
            allowed_clients = await db.clients.find({"assignedEmployeeId": user_id}).to_list(length=None)
            allowed_client_ids = [str(c["_id"]) for c in allowed_clients]
            
            # Get allowed projects
            allowed_projects = await get_projects(db, userId=user_id, role=role, limit=10000)
            allowed_project_ids = [str(p.get("id", p.get("_id"))) for p in allowed_projects]
            project_client_ids = [str(p.get("clientId")) for p in allowed_projects if p.get("clientId")]
            all_allowed_clients = list(set(allowed_client_ids + project_client_ids))
            
            match_query["$or"] = [
                {"projectId": {"$in": allowed_project_ids}},
                {"clientId": {"$in": all_allowed_clients}}
            ]
            
            if client_id:
                if client_id not in all_allowed_clients:
                    return []
                match_query["clientId"] = client_id
                match_query.pop("$or", None)
    
    if client_id and "clientId" not in match_query:
        match_query["clientId"] = client_id
    
    MONTH_MAP = {
        "January": "01", "February": "02", "March": "03", "April": "04",
        "May": "05", "June": "06", "July": "07", "August": "08",
        "September": "09", "October": "10", "November": "11", "December": "12"
    }
    
    if month:
        # Handle case where month is a string or a list of strings
        months_list = [month] if isinstance(month, str) else month
        months_list = [m for m in months_list if m != "all"]
        
        if months_list:
            all_date_ors = []
            for m in months_list:
                month_num = MONTH_MAP.get(m)
                if month_num:
                    year = datetime.now().year
                    # Create start and end datetime for the month
                    start_dt = datetime(year, int(month_num), 1)
                    if int(month_num) == 12:
                        end_dt = datetime(year + 1, 1, 1)
                    else:
                        end_dt = datetime(year, int(month_num) + 1, 1)
                    
                    regex_pattern = f"^{year}-{month_num}-"
                    all_date_ors.extend([
                        {"date": {"$regex": regex_pattern}},
                        {"date": {"$gte": start_dt, "$lt": end_dt}},
                        {"month": m}
                    ])
                else:
                    # Fallback if month is something else
                    all_date_ors.append({"month": m})
            
            if "$or" in match_query:
                match_query["$and"] = [{"$or": match_query.pop("$or")}, {"$or": all_date_ors}]
            else:
                match_query["$or"] = all_date_ors
            
    pipeline = [
        {"$match": match_query},
        {"$group": {
            "_id": "$clientId",
            "clientName": {"$first": "$clientName"},
            "totalSpend": {"$sum": "$spend"},
            "totalLeads": {"$sum": "$leads"},
            "totalRevenue": {"$sum": "$revenue"}
        }}
    ]
    
    aggregated = await db.marketing_daily_reports.aggregate(pipeline).to_list(length=1000)
    
    display_month = "All"
    if isinstance(month, str):
        display_month = month if month != "all" else "All"
    elif isinstance(month, list) and month:
        filtered_months = [m for m in month if m != "all"]
        if filtered_months:
            display_month = ", ".join(filtered_months)

    # Fetch manual conclusions from existing marketing_monthly_reports collection
    monthly_query = {}
    if is_restricted:
        monthly_query["$or"] = [
            {"projectId": {"$in": allowed_project_ids}},
            {"clientId": {"$in": all_allowed_clients}}
        ]
        
    if client_id:
        monthly_query["clientId"] = client_id
        monthly_query.pop("$or", None)
        
    if month and month != "all":
        # Handle if month is a list from FastAPI
        if isinstance(month, list):
            valid_months = [m for m in month if m != "all"]
            if valid_months:
                monthly_query["month"] = {"$in": valid_months}
        else:
            monthly_query["month"] = month
        
    manual_reports_cursor = db.marketing_monthly_reports.find(monthly_query)
    manual_reports = {doc.get("clientId"): doc async for doc in manual_reports_cursor}
    
    results = []
    for agg in aggregated:
        cid = agg["_id"]
        manual = manual_reports.get(cid, {})
        
        spend = float(agg.get("totalSpend") or 0)
        leads = int(agg.get("totalLeads") or 0)
        revenue = float(agg.get("totalRevenue") or 0)
        
        cpr = spend / leads if leads > 0 else 0
        roas = revenue / spend if spend > 0 else 0
        
        report = {
            "id": str(manual.get("_id")) if manual.get("_id") else f"agg-{cid}-{display_month}",
            "clientId": cid,
            "clientName": agg.get("clientName") or manual.get("clientName", "Unknown"),
            "month": display_month,
            "totalSpend": spend,
            "totalLeads": leads,
            "totalSales": 0,
            "totalRevenue": revenue,
            "avgCPR": cpr,
            "avgCPP": 0,
            "overallROAS": roas,
            "conclusion": manual.get("conclusion") or "",
            "employeeConclusion": manual.get("employeeConclusion") or "",
            "adminConclusion": manual.get("adminConclusion") or "",
            "clientConclusion": manual.get("clientConclusion") or ""
        }
        results.append(report)
        
    # Add any manual reports that didn't have daily reports for this month
    agg_client_ids = {agg["_id"] for agg in aggregated}
    for cid, manual in manual_reports.items():
        if cid not in agg_client_ids:
            doc = manual.copy()
            doc["id"] = str(doc.pop("_id"))
            # Ensure fields exist so UI doesn't break
            doc["totalSpend"] = float(doc.get("totalSpend") or 0)
            doc["totalLeads"] = int(doc.get("totalLeads") or 0)
            doc["totalSales"] = int(doc.get("totalSales") or 0)
            doc["totalRevenue"] = float(doc.get("totalRevenue") or 0)
            doc["avgCPR"] = float(doc.get("avgCPR") or 0)
            doc["avgCPP"] = float(doc.get("avgCPP") or 0)
            doc["overallROAS"] = float(doc.get("overallROAS") or 0)
            doc["conclusion"] = doc.get("conclusion") or ""
            doc["employeeConclusion"] = doc.get("employeeConclusion") or ""
            doc["adminConclusion"] = doc.get("adminConclusion") or ""
            doc["clientConclusion"] = doc.get("clientConclusion") or ""
            results.append(doc)
            
    return results

async def update_marketing_monthly_report(db, report_id: str, report: schemas.MarketingMonthlyReportUpdate):
    update_data = report.dict(exclude_unset=True)
    performedBy = update_data.pop("performedBy", "Unknown")
    userName = update_data.pop("userName", "Unknown User")
    
    if not update_data:
        return None
        
    if report_id.startswith("agg-"):
        # This is an aggregated report that doesn't have a manual DB entry yet.
        # We need to upsert it based on clientId and month.
        parts = report_id.split("-", 2)
        if len(parts) >= 3:
            client_id = parts[1]
            month = parts[2]
            
            # Make sure we have the required fields to create the doc if it doesn't exist
            if "clientId" not in update_data:
                update_data["clientId"] = client_id
            if "month" not in update_data:
                update_data["month"] = month
                
            # If clientName is missing, try to fetch it
            if "clientName" not in update_data:
                client = await db.clients.find_one({"_id": ObjectId(client_id)}) if ObjectId.is_valid(client_id) else None
                update_data["clientName"] = client.get("companyName", "Unknown") if client else "Unknown"

            # Fill missing defaults so Pydantic doesn't throw 500
            for k in ["totalSpend", "avgCPR", "avgCPP", "totalRevenue", "overallROAS"]:
                if k not in update_data: update_data[k] = 0.0
            for k in ["totalLeads", "totalSales"]:
                if k not in update_data: update_data[k] = 0
            
            result = await db.marketing_monthly_reports.update_one(
                {"clientId": client_id, "month": month},
                {"$set": update_data},
                upsert=True
            )
            
            doc = await db.marketing_monthly_reports.find_one({"clientId": client_id, "month": month})
            if doc:
                doc["id"] = str(doc.pop("_id"))
                
                # Log details
                log_details = f"Monthly Report created/updated with conclusion"
                await log_activity(db, "Updated", performedBy, userName, log_details, monthlyReportId=doc["id"])
                
                return doc
        return None

    if not ObjectId.is_valid(report_id):
        return None
        
    old_report = await db.marketing_monthly_reports.find_one({"_id": ObjectId(report_id)})
    await db.marketing_monthly_reports.update_one({"_id": ObjectId(report_id)}, {"$set": update_data})
    
    # Log details
    log_details, diffs = format_field_changes(old_report, update_data, "Monthly Report")
    await log_activity(db, "Updated", performedBy, userName, log_details, diffs=diffs, monthlyReportId=report_id)
        
    doc = await db.marketing_monthly_reports.find_one({"_id": ObjectId(report_id)})
    if doc:
        doc["id"] = str(doc["_id"])
        return doc
    return None

async def delete_marketing_monthly_report(db, report_id: str):
    res = await db.marketing_monthly_reports.delete_one({"_id": ObjectId(report_id)})
    if res.deleted_count > 0:
        await log_activity(db, "Deleted", "Admin", "N/A", f"Marketing Monthly Report was deleted.", monthlyReportId=report_id)
    return res.deleted_count > 0

async def sync_monthly_marketing_reports(db, date_str: str = None):
    try:
        if not date_str:
            now = get_now()
            date_str = now.strftime("%Y-%m-%d")
        else:
            now = datetime.strptime(date_str, "%Y-%m-%d")

        year_month = now.strftime("%Y-%m") # e.g. "2026-06"
        month_name = now.strftime("%B")    # e.g. "June"
        
        cursor = db.marketing_daily_reports.find({"date": {"$regex": f"^{year_month}"}})
        daily_reports = await cursor.to_list(length=10000)
        
        client_aggregates = {}
        for r in daily_reports:
            c_id = str(r.get("clientId"))
            if not c_id or c_id == "None": continue
            if c_id not in client_aggregates:
                client_aggregates[c_id] = {
                    "clientName": r.get("clientName", "Unknown"),
                    "totalSpend": 0.0,
                    "totalLeads": 0,
                    "totalRevenue": 0.0
                }
            
            try:
                client_aggregates[c_id]["totalSpend"] += float(r.get("spend") or 0)
                client_aggregates[c_id]["totalLeads"] += int(r.get("leads") or 0)
                client_aggregates[c_id]["totalRevenue"] += float(r.get("revenue") or 0)
            except Exception:
                pass
                
        for c_id, agg in client_aggregates.items():
            total_spend = agg["totalSpend"]
            total_leads = agg["totalLeads"]
            total_revenue = agg["totalRevenue"]
            
            avg_cpr = round(total_spend / total_leads, 2) if total_leads > 0 else 0.0
            overall_roas = round(total_revenue / total_spend, 2) if total_spend > 0 else 0.0
            
            existing = await db.marketing_monthly_reports.find_one({
                "clientId": c_id,
                "month": month_name
            })
            
            if existing:
                total_sales = existing.get("totalSales", 0)
                avg_cpp = round(total_spend / total_sales, 2) if total_sales > 0 else 0.0
                
                update_data = {
                    "totalSpend": round(total_spend, 2),
                    "totalLeads": total_leads,
                    "totalRevenue": round(total_revenue, 2),
                    "avgCPR": avg_cpr,
                    "avgCPP": avg_cpp,
                    "overallROAS": overall_roas
                }
                await db.marketing_monthly_reports.update_one(
                    {"_id": existing["_id"]},
                    {"$set": update_data}
                )
            else:
                # Check if the client is on-hold before adding a new row
                client = await db.clients.find_one({"_id": ObjectId(c_id)})
                if client and client.get("status") == "on-hold":
                    continue
                
                new_report = {
                    "clientId": c_id,
                    "clientName": agg["clientName"],
                    "month": month_name,
                    "totalSpend": round(total_spend, 2),
                    "totalLeads": total_leads,
                    "totalSales": 0,
                    "avgCPR": avg_cpr,
                    "avgCPP": 0.0,
                    "totalRevenue": round(total_revenue, 2),
                    "overallROAS": overall_roas,
                    "employeeConclusion": "",
                    "adminConclusion": "",
                    "clientConclusion": ""
                }
                await db.marketing_monthly_reports.insert_one(new_report)
                
    except Exception as e:
        print(f"Error syncing monthly marketing reports: {e}", flush=True)

# Penalty Type CRUD
async def get_penalty_types(db, skip: int = 0, limit: int = 100):
    cursor = db.penalty_types.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def create_penalty_type(db, penalty_type: schemas.PenaltyTypeCreate):
    penalty_dict = penalty_type.dict()
    result = await db.penalty_types.insert_one(penalty_dict)
    penalty_dict["id"] = str(result.inserted_id)
    return penalty_dict

async def update_penalty_type(db, penalty_id: str, penalty_update: schemas.PenaltyTypeUpdate):
    update_data = penalty_update.dict(exclude_unset=True)
    await db.penalty_types.update_one({"_id": ObjectId(penalty_id)}, {"$set": update_data})
    updated_doc = await db.penalty_types.find_one({"_id": ObjectId(penalty_id)})
    return fix_id(updated_doc)

async def delete_penalty_type(db, penalty_id: str):
    await db.penalty_types.delete_one({"_id": ObjectId(penalty_id)})
    return True
# Chat CRUD
async def create_message(db, message: schemas.ChatMessageCreate):
    message_dict = message.dict()
    message_dict["timestamp"] = get_now().isoformat()
    result = await db.messages.insert_one(message_dict)
    message_dict["id"] = str(result.inserted_id)
    if "_id" in message_dict:
        message_dict.pop("_id")
    if "senderId" in message_dict:
        try:
            emp = await db.employees.find_one({"_id": ObjectId(message_dict["senderId"])})
            if emp:
                message_dict["sender"] = emp.get("name", "Colleague")
                message_dict["senderAvatar"] = emp.get("profilePhoto") or ""
        except Exception:
            pass
    return message_dict

async def create_chat_group(db, group: schemas.ChatGroupCreate):
    group_dict = group.dict()
    group_dict["timestamp"] = get_now().isoformat()
    result = await db.chat_groups.insert_one(group_dict)
    group_dict["id"] = str(result.inserted_id)
    return group_dict

async def update_chat_group(db, group_id: str, group_update: schemas.ChatGroupUpdate):
    update_data = group_update.dict(exclude_unset=True)
    await db.chat_groups.update_one({"_id": ObjectId(group_id)}, {"$set": update_data})
    updated_doc = await db.chat_groups.find_one({"_id": ObjectId(group_id)})
    return fix_id(updated_doc)

async def delete_chat_group(db, group_id: str):
    await db.chat_groups.delete_one({"_id": ObjectId(group_id)})
    # Also delete messages in this group
    await db.messages.delete_many({"groupId": group_id})
    return True

async def get_chat_groups(db, user_id: str):
    # Get groups where user is a member
    cursor = db.chat_groups.find({"members": user_id}).sort("timestamp", -1)
    rows = await cursor.to_list(length=100)
    
    fixed_rows = []
    for row in rows:
        fixed = fix_id(row)
        last_msg = await db.messages.find_one({"groupId": fixed["id"]}, sort=[("timestamp", -1)])
        if last_msg:
            text = last_msg.get("text", "")
            if not text and (last_msg.get("attachmentUrl") or last_msg.get("attachmentName")):
                text = "Sent a file"
            fixed["lastMessage"] = text
            fixed["lastMessageAttachmentName"] = last_msg.get("attachmentName") or ""
            fixed["lastMessageSenderId"] = str(last_msg.get("senderId", ""))
            fixed["lastMessageIsVoice"] = bool(last_msg.get("isVoice", False))
            try:
                from datetime import datetime
                t_str = last_msg["timestamp"].replace("Z", "+00:00")
                dt = datetime.fromisoformat(t_str)
                fixed["lastMessageTime"] = dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                fixed["lastMessageTime"] = last_msg.get("timestamp", "")
        else:
            fixed["lastMessage"] = ""
            fixed["lastMessageTime"] = ""
            fixed["lastMessageAttachmentName"] = ""
            fixed["lastMessageSenderId"] = ""
            fixed["lastMessageIsVoice"] = False
        fixed_rows.append(fixed)
    return fixed_rows

async def get_messages(db, sender_id: str = None, receiver_id: str = None, group_id: str = None):
    if group_id:
        group_ids = [group_id]
        if len(group_id) == 24:
            try:
                group_ids.append(ObjectId(group_id))
            except:
                pass
        query = {"groupId": {"$in": group_ids}}
    else:
        sender_ids = [sender_id]
        if sender_id and len(sender_id) == 24:
            try:
                sender_ids.append(ObjectId(sender_id))
            except:
                pass
                
        receiver_ids = [receiver_id]
        if receiver_id and len(receiver_id) == 24:
            try:
                receiver_ids.append(ObjectId(receiver_id))
            except:
                pass
                
        query = {
            "$or": [
                {"senderId": {"$in": sender_ids}, "receiverId": {"$in": receiver_ids}},
                {"senderId": {"$in": receiver_ids}, "receiverId": {"$in": sender_ids}}
            ]
        }
    cursor = db.messages.find(query).sort("timestamp", 1)
    rows = await cursor.to_list(length=1000)
    
    # Pre-fetch all active employees to avoid N+1 database queries
    try:
        employees_cursor = db.employees.find()
        employees_list = await employees_cursor.to_list(length=1000)
        employee_cache = {
            str(emp["_id"]): {
                "name": emp.get("name", "Colleague"),
                "photo": emp.get("profilePhoto") or ""
            }
            for emp in employees_list
        }
    except Exception:
        employee_cache = {}

    user_id = None
    if group_id:
        user_id = receiver_id
    else:
        user_id = sender_id
    
    fixed_rows = []
    for row in rows:
        deleted_for = row.get("deletedFor") or []
        if user_id and str(user_id) in [str(d) for d in deleted_for]:
            continue
        
        fixed = fix_id(row)
        if "senderId" in fixed and fixed["senderId"]:
            fixed["senderId"] = str(fixed["senderId"])
            emp_info = employee_cache.get(fixed["senderId"], {"name": "Colleague", "photo": ""})
            fixed["sender"] = emp_info["name"]
            fixed["senderAvatar"] = emp_info["photo"]
        if "receiverId" in fixed and fixed["receiverId"]:
            fixed["receiverId"] = str(fixed["receiverId"])
        if "groupId" in fixed and fixed["groupId"]:
            fixed["groupId"] = str(fixed["groupId"])
        if "deletedFor" in fixed:
            del fixed["deletedFor"]
        fixed_rows.append(fixed)
    return fixed_rows

async def update_message(db, message_id: str, text: str):
    await db.messages.update_one(
        {"_id": ObjectId(message_id)},
        {"$set": {"text": text, "isEdited": True}}
    )
    doc = await db.messages.find_one({"_id": ObjectId(message_id)})
    return fix_id(doc)

async def delete_message(db, message_id: str, delete_for: str = "everyone", user_id: str = None):
    if delete_for == "me" and user_id:
        await db.messages.update_one(
            {"_id": ObjectId(message_id)},
            {
                "$push": {"deletedFor": user_id},
                "$set": {"text": "You deleted this message", "attachmentUrl": None, "attachmentName": None}
            }
        )
        return {"deleted": "for_me"}
    else:
        await db.messages.delete_one({"_id": ObjectId(message_id)})
        return {"deleted": "for_everyone"}

async def mark_messages_as_seen(db, other_id: str, user_id: str):
    is_group = await db.chat_groups.find_one({"_id": ObjectId(other_id)}) if len(other_id) == 24 else None
    is_channel = await db.chat_channels.find_one({"_id": ObjectId(other_id)}) if len(other_id) == 24 else None
    
    other_id_obj = ObjectId(other_id) if len(other_id) == 24 else None
    user_id_obj = ObjectId(user_id) if len(user_id) == 24 else None
    
    user_ids = [user_id]
    if user_id_obj:
        user_ids.append(user_id_obj)
        
    other_ids = [other_id]
    if other_id_obj:
        other_ids.append(other_id_obj)
        
    if is_group or is_channel or other_id.startswith("gen-"):
        # Group or General channel
        await db.messages.update_many(
            {"groupId": {"$in": other_ids}, "seenBy": {"$nin": user_ids}},
            {"$push": {"seenBy": user_id}}
        )
    else:
        # Personal chat
        await db.messages.update_many(
            {"senderId": {"$in": other_ids}, "receiverId": {"$in": user_ids}, "seenBy": {"$nin": user_ids}},
            {"$set": {"isSeen": True}, "$push": {"seenBy": user_id}}
        )
    return True

async def get_chat_summaries(db, user_id: str):
    pipeline = [
        {"$match": {"$or": [{"senderId": user_id}, {"receiverId": user_id}]}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": {
                "$cond": [
                    {"$eq": ["$senderId", user_id]},
                    "$receiverId",
                    "$senderId"
                ]
            },
            "lastMessage": {"$first": "$text"},
            "attachmentUrl": {"$first": "$attachmentUrl"},
            "attachmentName": {"$first": "$attachmentName"},
            "timestamp": {"$first": "$timestamp"},
            "isSeen": {"$first": "$isSeen"},
            "senderId": {"$first": "$senderId"},
            "isVoice": {"$first": "$isVoice"}
        }}
    ]
    cursor = db.messages.aggregate(pipeline)
    results = await cursor.to_list(length=1000)
    return {r["_id"]: r for r in results}

async def toggle_save_message(db, message_id: str, user_id: str):
    msg = await db.messages.find_one({"_id": ObjectId(message_id)})
    if msg:
        saved_by = msg.get("savedBy", [])
        if user_id in saved_by:
            await db.messages.update_one({"_id": ObjectId(message_id)}, {"$pull": {"savedBy": user_id}})
            return False
        else:
            await db.messages.update_one({"_id": ObjectId(message_id)}, {"$push": {"savedBy": user_id}})
            return True
    return False

async def toggle_pin_message(db, message_id: str):
    msg = await db.messages.find_one({"_id": ObjectId(message_id)})
    if msg:
        new_status = not msg.get("isPinned", False)
        await db.messages.update_one({"_id": ObjectId(message_id)}, {"$set": {"isPinned": new_status}})
        return new_status
    return False

# Chat Channels CRUD
async def get_chat_channels(db):
    cursor = db.chat_channels.find()
    rows = await cursor.to_list(length=100)
    if not rows:
        # Seed default channels
        defaults = [
            {"name": "Announcements", "description": "Company-wide official news"},
            {"name": "General", "description": "General watercooler talk"},
            {"name": "Tech Support", "description": "IT and technical help"},
            {"name": "HR Queries", "description": "Ask HR about policies"}
        ]
        for d in defaults:
            await db.chat_channels.insert_one(d)
        cursor = db.chat_channels.find()
        rows = await cursor.to_list(length=100)
        
    fixed_rows = []
    for row in rows:
        fixed = fix_id(row)
        last_msg = await db.messages.find_one({"groupId": fixed["id"]}, sort=[("timestamp", -1)])
        if last_msg:
            text = last_msg.get("text", "")
            if not text and (last_msg.get("attachmentUrl") or last_msg.get("attachmentName")):
                text = "Sent a file"
            fixed["lastMessage"] = text
            fixed["lastMessageAttachmentName"] = last_msg.get("attachmentName") or ""
            fixed["lastMessageSenderId"] = str(last_msg.get("senderId", ""))
            fixed["lastMessageIsVoice"] = bool(last_msg.get("isVoice", False))
            try:
                from datetime import datetime
                t_str = last_msg["timestamp"].replace("Z", "+00:00")
                dt = datetime.fromisoformat(t_str)
                fixed["lastMessageTime"] = dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                fixed["lastMessageTime"] = last_msg.get("timestamp", "")
        else:
            fixed["lastMessage"] = fixed.get("description", "")
            fixed["lastMessageTime"] = ""
            fixed["lastMessageAttachmentName"] = ""
            fixed["lastMessageSenderId"] = ""
            fixed["lastMessageIsVoice"] = False
        fixed_rows.append(fixed)
    return fixed_rows

async def create_chat_channel(db, channel: schemas.ChatChannelCreate):
    channel_dict = channel.dict()
    result = await db.chat_channels.insert_one(channel_dict)
    channel_dict["id"] = str(result.inserted_id)
    if "_id" in channel_dict:
        channel_dict.pop("_id")
    return channel_dict

async def update_chat_channel(db, channel_id: str, channel_update: schemas.ChatChannelUpdate):
    update_data = channel_update.dict(exclude_unset=True)
    await db.chat_channels.update_one({"_id": ObjectId(channel_id)}, {"$set": update_data})
    doc = await db.chat_channels.find_one({"_id": ObjectId(channel_id)})
    return fix_id(doc)

async def delete_chat_channel(db, channel_id: str):
    await db.chat_channels.delete_one({"_id": ObjectId(channel_id)})
    # Note: messages might use channel id as groupId
    await db.messages.delete_many({"groupId": channel_id})
    return True

async def get_saved_messages(db, user_id: str):
    cursor = db.messages.find({"savedBy": user_id})
    rows = await cursor.to_list(length=1000)
    return [fix_id(row) for row in rows]

async def get_chat_files(db, user_id: str, other_id: str, is_group: bool = False):
    if is_group:
        query = {"groupId": other_id, "attachmentUrl": {"$ne": None}}
    else:
        query = {
            "$or": [
                {"senderId": user_id, "receiverId": other_id},
                {"senderId": other_id, "receiverId": user_id}
            ],
            "attachmentUrl": {"$ne": None}
        }
    cursor = db.messages.find(query).sort("timestamp", -1)
    rows = await cursor.to_list(length=500)
    return [fix_id(row) for row in rows]

async def toggle_archive_message(db, message_id: str, user_id: str):
    msg = await db.messages.find_one({"_id": ObjectId(message_id)})
    if msg:
        archived_by = msg.get("archivedBy", [])
        if user_id in archived_by:
            await db.messages.update_one({"_id": ObjectId(message_id)}, {"$pull": {"archivedBy": user_id}})
            return False
        else:
            await db.messages.update_one({"_id": ObjectId(message_id)}, {"$push": {"archivedBy": user_id}})
            return True
    return False

async def toggle_complete_message(db, message_id: str, user_id: str):
    msg = await db.messages.find_one({"_id": ObjectId(message_id)})
    if msg:
        completed_by = msg.get("completedBy", [])
        if user_id in completed_by:
            await db.messages.update_one({"_id": ObjectId(message_id)}, {"$pull": {"completedBy": user_id}})
            return False
        else:
            await db.messages.update_one({"_id": ObjectId(message_id)}, {"$push": {"completedBy": user_id}})
            return True
    return False

async def toggle_reaction(db, message_id: str, user_id: str, emoji: str):
    msg = await db.messages.find_one({"_id": ObjectId(message_id)})
    if not msg:
        return None
    
    reactions = msg.get("reactions", {})
    if not isinstance(reactions, dict):
        reactions = {}
        
    user_list = reactions.get(emoji, [])
    if user_id in user_list:
        user_list.remove(user_id)
        if not user_list:
            del reactions[emoji]
        else:
            reactions[emoji] = user_list
    else:
        # Check if user already reacted with this emoji, or if we want to allow multiple different reactions per user
        # Standard behavior: allow multiple different emojis, but only one of each
        if emoji not in reactions:
            reactions[emoji] = []
        reactions[emoji].append(user_id)
        
    await db.messages.update_one(
        {"_id": ObjectId(message_id)},
        {"$set": {"reactions": reactions}}
    )
    return reactions

async def update_employee_status(db, employee_id: str, status: str, emoji: str):
    await db.employees.update_one(
        {"_id": ObjectId(employee_id)},
        {"$set": {"customStatus": status, "statusEmoji": emoji}}
    )
    doc = await db.employees.find_one({"_id": ObjectId(employee_id)})
    return fix_id(doc)

async def vote_poll(db, message_id: str, user_id: str, option_id: str):
    msg = await db.messages.find_one({"_id": ObjectId(message_id)})
    if not msg or "poll" not in msg:
        return None
    
    poll = msg["poll"]
    is_multiple = poll.get("isMultiple", False)
    
    updated_options = []
    for opt in poll["options"]:
        if opt["id"] == option_id:
            if user_id in opt["votes"]:
                opt["votes"].remove(user_id)
            else:
                opt["votes"].append(user_id)
        elif not is_multiple:
            # If not multiple choice, remove user from other options
            if user_id in opt["votes"]:
                opt["votes"].remove(user_id)
        updated_options.append(opt)
    
    await db.messages.update_one(
        {"_id": ObjectId(message_id)},
        {"$set": {"poll.options": updated_options}}
    )
    return updated_options

async def set_typing_status(db, chat_id: str, user_id: str, is_typing: bool):
    if is_typing:
        await db.typing.update_one(
            {"chatId": chat_id, "userId": user_id},
            {"$set": {"timestamp": get_now()}},
            upsert=True
        )
    else:
        await db.typing.delete_one({"chatId": chat_id, "userId": user_id})
    return True

async def get_typing_users(db, chat_id: str, current_user_id: str):
    # Get users typing in this chat in the last 10 seconds
    threshold = get_now() - timedelta(seconds=10)
    
    # Check if it's a group chat or personal chat
    is_group = False
    if chat_id.startswith("gen-"):
        is_group = True
    elif len(chat_id) == 24:
        try:
            group = await db.chat_groups.find_one({"_id": ObjectId(chat_id)})
            if group:
                is_group = True
        except Exception:
            pass
            
    if is_group:
        query = {
            "chatId": chat_id, 
            "userId": {"$ne": current_user_id},
            "timestamp": {"$gt": threshold}
        }
    else:
        # Personal chat: check if the other user (chat_id) is typing to the current user (current_user_id)
        query = {
            "chatId": current_user_id,
            "userId": chat_id,
            "timestamp": {"$gt": threshold}
        }
        
    cursor = db.typing.find(query)
    typing_entries = await cursor.to_list(length=100)
    
    user_ids = [entry["userId"] for entry in typing_entries]
    if not user_ids:
        return []
    
    # Get user names
    users = await db.employees.find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}}).to_list(length=100)
    return [user["name"] for user in users]

# Employee Document CRUD
async def create_employee_document(db, document: schemas.EmployeeDocumentCreate):
    doc_dict = document.dict()
    result = await db.employee_documents.insert_one(doc_dict)
    doc_dict["id"] = str(result.inserted_id)
    # Log activity
    await log_task_activity(db, None, "Document Uploaded", doc_dict["employeeId"], doc_dict["employeeName"], f"Uploaded document: {doc_dict['documentName']}")
    return doc_dict

async def get_employee_documents(db, employee_id: str = None):
    query = {}
    if employee_id:
        query["employeeId"] = employee_id
    cursor = db.employee_documents.find(query).sort("uploadDate", -1)
    docs = []
    async for doc in cursor:
        docs.append(fix_id(doc))
    return docs

async def update_employee_document(db, doc_id: str, doc_update: schemas.EmployeeDocumentUpdate):
    update_data = doc_update.dict(exclude_unset=True)
    if not update_data:
        doc = await db.employee_documents.find_one({"_id": ObjectId(doc_id)})
        return fix_id(doc)
    
    # Extract actor info if provided
    performed_by = update_data.pop("performedBy", None)
    user_name = update_data.pop("userName", None)
    
    existing_doc = await db.employee_documents.find_one({"_id": ObjectId(doc_id)})
    
    # Check for status change
    log_entry = None
    if "status" in update_data and existing_doc:
        old_status = existing_doc.get("status")
        new_status = update_data["status"]
        if old_status != new_status:
            log_entry = {
                "oldStatus": old_status,
                "newStatus": new_status,
                "changedBy": user_name or performed_by or "System",
                "timestamp": get_now().isoformat()
            }
            
    await db.employee_documents.update_one({"_id": ObjectId(doc_id)}, {"$set": update_data})
    
    if log_entry:
        await db.employee_documents.update_one({"_id": ObjectId(doc_id)}, {"$push": {"logs": log_entry}})
        
    doc = await db.employee_documents.find_one({"_id": ObjectId(doc_id)})
    return fix_id(doc)

async def delete_employee_document(db, doc_id: str):
    result = await db.employee_documents.delete_one({"_id": ObjectId(doc_id)})
    return result.deleted_count > 0

# Document Type CRUD
async def create_document_type(db, doc_type: schemas.DocumentTypeCreate):
    dt_dict = doc_type.dict()
    result = await db.document_types.insert_one(dt_dict)
    dt_dict["id"] = str(result.inserted_id)
    if "_id" in dt_dict:
        dt_dict.pop("_id")
    return dt_dict

async def get_document_types(db):
    cursor = db.document_types.find({})
    types = []
    async for dt in cursor:
        types.append(fix_id(dt))
    return types

async def update_document_type(db, type_id: str, type_update: schemas.DocumentTypeUpdate):
    update_data = type_update.dict(exclude_unset=True)
    if not update_data:
        dt = await db.document_types.find_one({"_id": ObjectId(type_id)})
        return fix_id(dt)
    
    await db.document_types.update_one({"_id": ObjectId(type_id)}, {"$set": update_data})
    dt = await db.document_types.find_one({"_id": ObjectId(type_id)})
    return fix_id(dt)

async def delete_document_type(db, type_id: str):
    result = await db.document_types.delete_one({"_id": ObjectId(type_id)})
    return result.deleted_count > 0


# Document Request CRUD
async def create_document_request(db, request: schemas.DocumentRequestCreate):
    req_dict = request.dict()
    result = await db.document_requests.insert_one(req_dict)
    req_dict["id"] = str(result.inserted_id)
    if "_id" in req_dict:
        req_dict.pop("_id")
    # Log activity
    await log_task_activity(db, None, "Document Requested", req_dict["employeeId"], req_dict["employeeName"], f"Requested letter: {req_dict['documentType']}")
    
    # Notify all Admins and HR
    try:
        admins_and_hr = await db.employees.find({
            "role": {"$regex": "^(Admin|HR)$", "$options": "i"}
        }).to_list(length=100)
        for staff in admins_and_hr:
            staff_id = str(staff["_id"])
            if staff_id == req_dict["employeeId"]:
                continue
            await db.notifications.insert_one({
                "employee_id": staff_id,
                "title": "New Document Request",
                "message": f"{req_dict['employeeName']} has requested a {req_dict['documentType']}. Reason: {req_dict.get('reason', 'Not specified')}",
                "type": "document",
                "reference_id": req_dict["id"],
                "is_read": False,
                "created_at": get_now().strftime("%d-%m-%Y %H:%M")
            })
    except Exception as e:
        print(f"Error creating notifications for document request: {e}")
    
    return req_dict

async def get_document_requests(db, employee_id: str = None):
    query = {}
    if employee_id:
        query["employeeId"] = employee_id
    cursor = db.document_requests.find(query).sort("requestDate", -1)
    reqs = []
    async for req in cursor:
        reqs.append(fix_id(req))
    return reqs

async def update_document_request(db, req_id: str, req_update: schemas.DocumentRequestUpdate):
    update_data = req_update.dict(exclude_unset=True)
    
    # Fetch the existing request before updating
    existing_req = await db.document_requests.find_one({"_id": ObjectId(req_id)})
    
    if not update_data:
        return fix_id(existing_req) if existing_req else None
    
    await db.document_requests.update_one({"_id": ObjectId(req_id)}, {"$set": update_data})
    req = await db.document_requests.find_one({"_id": ObjectId(req_id)})
    
    # Notify the employee when their document has been sent
    if update_data.get("status") == "Sent" and existing_req:
        try:
            employee_id = existing_req.get("employeeId", "")
            doc_type = existing_req.get("documentType", "document")
            if employee_id:
                await db.notifications.insert_one({
                    "employee_id": employee_id,
                    "title": "Your Document is Ready",
                    "message": f"Your requested {doc_type} has been generated and sent to you. You can download it from the Official Letters section.",
                    "type": "document",
                    "reference_id": req_id,
                    "is_read": False,
                    "created_at": get_now().strftime("%d-%m-%Y %H:%M")
                })
        except Exception as e:
            print(f"Error creating notification for document sent: {e}")
    
    return fix_id(req)

async def delete_document_request(db, req_id: str):
    result = await db.document_requests.delete_one({"_id": ObjectId(req_id)})
    return result.deleted_count > 0

# Employee Daily Report CRUD
async def apply_work_rejection_penalty(db, employee_id: str, report_date):
    employee = await get_employee(db, employee_id)
    if not employee:
        return
    
    salary_struct = await get_salary_structure_by_employee(db, employee_id)
    if not salary_struct:
        return
    
    try:
        if isinstance(report_date, str):
            dt = datetime.strptime(report_date.split("T")[0].split(" ")[0], "%Y-%m-%d")
        else:
            # It's already a date or datetime object
            dt = report_date
            
        report_date_str = dt.strftime("%Y-%m-%d")
            
        month_name = calendar.month_name[dt.month]
        month_num = dt.month
        year = dt.year
        _, num_days = calendar.monthrange(year, month_num)
        
        # Calculate working days in month (excluding Sundays and Holidays)
        sunday_dates = []
        for d in range(1, num_days + 1):
            if calendar.weekday(year, month_num, d) == 6: # Sunday
                sunday_dates.append(f"{year}-{str(month_num).zfill(2)}-{str(d).zfill(2)}")
        
        start_dt_naive = datetime(year, month_num, 1)
        end_dt_naive = datetime(year, month_num, num_days, 23, 59, 59)
        start_dt_aware = start_dt_naive.replace(tzinfo=IST)
        end_dt_aware = end_dt_naive.replace(tzinfo=IST)

        # Count Holidays for this employee's company
        emp_company = employee.get("company")
        holiday_query = {
            "$or": [
                {"date": {"$gte": start_dt_naive, "$lte": end_dt_naive}},
                {"date": {"$gte": start_dt_aware, "$lte": end_dt_aware}}
            ]
        }
        if emp_company:
            holiday_query["$or"] = [
                {"$and": [{"company": emp_company}, {"$or": [{"date": {"$gte": start_dt_naive, "$lte": end_dt_naive}}, {"date": {"$gte": start_dt_aware, "$lte": end_dt_aware}}]}]},
                {"$and": [{"company": {"$in": [None, "", "null"]}}, {"$or": [{"date": {"$gte": start_dt_naive, "$lte": end_dt_naive}}, {"date": {"$gte": start_dt_aware, "$lte": end_dt_aware}}]}]}
            ]
        
        holidays_cursor = db.holidays.find(holiday_query)
        month_holidays = await holidays_cursor.to_list(length=31)
        
        unique_holidays = 0
        for h in month_holidays:
            h_date = h.get("date")
            h_date_str = h_date.strftime("%Y-%m-%d") if isinstance(h_date, (date, datetime)) else str(h_date)
            if h_date_str not in sunday_dates:
                unique_holidays += 1
                
        total_working_days = max(1, num_days - len(sunday_dates) - unique_holidays)
        per_day_salary = salary_struct["monthlyGross"] / total_working_days
        reason = f"Daily work report for {report_date_str} was rejected. Automatic full-day salary deduction applied."
        
        # Check if already deducted for this specific date
        existing = await db.bonus_deductions.find_one({
            "employeeId": employee_id,
            "type": "deduction",
            "reason": reason
        })
        
        if not existing:
            deduction = schemas.BonusDeductionCreate(
                employeeId=employee_id,
                month=month_name,
                year=year,
                type="deduction",
                amount=round(per_day_salary, 2),
                reason=reason,
                status="active",
                date=report_date_str
            )
            await create_bonus_deduction(db, deduction)
            
            # Add Remark for visibility
            remark_data = {
                "employeeId": employee_id,
                "employeeName": employee["name"],
                "role": employee.get("designation", "Staff"),
                "avatar": employee.get("profilePhoto", ""),
                "type": "Performance",
                "details": f"Daily work report for {report_date_str} was rejected. Automatic full-day salary deduction applied.",
                "addedBy": "System",
                "date": datetime.combine(dt.date(), datetime.min.time()).replace(tzinfo=IST)
            }
            await db.remarks.insert_one(remark_data)
            
    except Exception as e:
        print(f"Error applying work rejection penalty: {e}")

async def remove_work_rejection_penalty(db, employee_id: str, report_date):
    try:
        if isinstance(report_date, str):
            dt = datetime.strptime(report_date.split("T")[0].split(" ")[0], "%Y-%m-%d")
        else:
            dt = report_date
            
        report_date_str = dt.strftime("%Y-%m-%d")
        reason = f"Daily work report for {report_date_str} was rejected. Automatic full-day salary deduction applied."
        
        # Remove deduction
        await db.bonus_deductions.delete_many({
            "employeeId": employee_id,
            "type": "deduction",
            "reason": reason
        })
        
        # Remove remark
        await db.remarks.delete_many({
            "employeeId": employee_id,
            "type": "Performance",
            "details": reason
        })
    except Exception as e:
        print(f"Error removing work rejection penalty: {e}")

async def create_employee_daily_report(db, report: schemas.EmployeeDailyReportCreate):
    report_dict = report.dict()
    performedBy = report_dict.get("performedBy", None)
    userName = report_dict.get("userName", None)
    
    emp_id = report_dict.get("employeeId")
    rep_date = report_dict.get("date")
    existing = await db.employee_daily_reports.find_one({"employeeId": emp_id, "date": rep_date})
    if existing:
        await db.employee_daily_reports.update_one({"_id": existing["_id"]}, {"$set": report_dict})
        report_dict["id"] = str(existing["_id"])
    else:
        result = await db.employee_daily_reports.insert_one(report_dict)
        report_dict["id"] = str(result.inserted_id)
    
    # Log activity
    status = report_dict.get("status", "Submitted")
    report_date = str(report_dict['date']).split(" ")[0]
    if status in ["Approved", "Rejected"] and performedBy and userName:
        # Created directly as Approved or Rejected by TL/Admin
        action = f"Daily Report {status}"
        details = f"{status} daily report for {report_dict['employeeName']} on {report_date}"
        await log_activity(db, action, performedBy, userName, details, dailyReportId=report_dict["id"])
    else:
        # Submitted by the employee themselves
        actor_id = performedBy or report_dict["employeeId"]
        actor_name = userName or report_dict["employeeName"]
        await log_activity(db, "Daily Report Submitted", actor_id, actor_name, f"Submitted daily report for {report_date}", dailyReportId=report_dict["id"])
        
    if report_dict.get("status") == "Rejected":
        await apply_work_rejection_penalty(db, report_dict["employeeId"], report_dict["date"])
        
    return report_dict

async def get_employee_daily_reports(db, employee_id: str = None, department: str = None, date: str = None):
    query = {}
    if employee_id:
        query["employeeId"] = employee_id
    if department:
        query["department"] = department
    if date:
        try:
            parsed_date = datetime.strptime(date, "%Y-%m-%d")
            dt_val = datetime(parsed_date.year, parsed_date.month, parsed_date.day)
            query["date"] = {"$in": [date, parsed_date, dt_val]}
        except Exception:
            query["date"] = date
    
    cursor = db.employee_daily_reports.find(query).sort("date", -1)
    reports = []
    async for doc in cursor:
        reports.append(fix_id(doc))
    return reports

# Sales Target CRUD
async def get_sales_targets(db, month: Optional[str] = None, year: Optional[int] = None, type: Optional[str] = None):
    query = {}
    if month: query["month"] = month
    if year: query["year"] = year
    if type: query["type"] = type
    cursor = db.sales_targets.find(query)
    rows = await cursor.to_list(length=1000)
    return [fix_id(row) for row in rows]

async def create_or_update_sales_target(db, target: schemas.SalesTargetCreate):
    target_dict = target.dict()
    # Ensure employeeId is an ObjectId if valid
    emp_id = target_dict["employeeId"]
    if isinstance(emp_id, str) and len(emp_id) == 24:
        target_dict["employeeId"] = ObjectId(emp_id)

    # Unique identifier: employeeId + type + category + details
    query = {
        "employeeId": target_dict["employeeId"],
        "type": target_dict.get("type", "Monthly"),
        "category": target_dict.get("category", "Overall")
    }
    if target_dict.get("type") == "Weekly":
        query["month"] = target_dict["month"]
        query["year"] = target_dict["year"]
        query["week"] = target_dict.get("week")
    elif target_dict.get("type") == "Monthly":
        query["month"] = target_dict["month"]
        query["year"] = target_dict["year"]
    elif target_dict.get("type") == "Custom":
        query["startDate"] = target_dict.get("startDate")
        query["endDate"] = target_dict.get("endDate")
        
    # Check for date overlaps
    def get_date_range(td):
        from datetime import datetime
        import calendar
        ttype = td.get("type", "Monthly")
        if ttype == "Custom":
            sd_str = td.get("startDate")
            ed_str = td.get("endDate")
            if not sd_str or not ed_str: return None, None
            try:
                sd = datetime.strptime(sd_str, "%Y-%m-%d")
                ed = datetime.strptime(ed_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                return sd, ed
            except: return None, None
        
        m_str = td.get("month")
        y = td.get("year")
        if not m_str or not y: return None, None
        
        try:
            m_num = list(calendar.month_name).index(m_str)
        except: return None, None
            
        if ttype == "Monthly":
            _, last = calendar.monthrange(y, m_num)
            sd = datetime(y, m_num, 1)
            ed = datetime(y, m_num, last, 23, 59, 59)
            return sd, ed
            
        if ttype == "Weekly":
            w = td.get("week", 1)
            _, last = calendar.monthrange(y, m_num)
            s_day = (w - 1) * 7 + 1
            e_day = min(w * 7, last)
            if s_day > last: return None, None
            sd = datetime(y, m_num, s_day)
            ed = datetime(y, m_num, e_day, 23, 59, 59)
            return sd, ed
            
        return None, None
        
    new_sd, new_ed = get_date_range(target_dict)
    if new_sd and new_ed:
        existing = await db.sales_targets.find({"employeeId": target_dict["employeeId"]}).to_list(length=100)
        for ext in existing:
            # Skip if it is the exact same target type/period we are upserting
            ext_q = {"type": ext.get("type")}
            if ext.get("type") == "Weekly":
                ext_q["month"] = ext.get("month")
                ext_q["year"] = ext.get("year")
                ext_q["week"] = ext.get("week")
            elif ext.get("type") == "Monthly":
                ext_q["month"] = ext.get("month")
                ext_q["year"] = ext.get("year")
            elif ext.get("type") == "Custom":
                ext_q["startDate"] = ext.get("startDate")
                ext_q["endDate"] = ext.get("endDate")
                
            is_same = True
            for k, v in query.items():
                if k != "employeeId" and ext_q.get(k) != v:
                    is_same = False
                    break
            if ext.get("category", "Overall") != target_dict.get("category", "Overall"):
                is_same = False
            if is_same: continue
            
            # Check overlap only if category matches
            if ext.get("category", "Overall") == target_dict.get("category", "Overall"):
                ext_sd, ext_ed = get_date_range(ext)
                if ext_sd and ext_ed:
                    if new_sd <= ext_ed and new_ed >= ext_sd:
                        raise ValueError(f"Target overlaps with existing {ext.get('type')} target for category '{ext.get('category', 'Overall')}'")
                    
    target_dict.pop("createdAt", None)
    await db.sales_targets.update_one(
        query,
        {
            "$set": target_dict,
            "$setOnInsert": {"createdAt": get_now().isoformat()}
        },
        upsert=True
    )
    
    # Trigger recalculation immediately
    await recalculate_sales_target(
        db, 
        target_dict["employeeId"], 
        target_dict.get("month"), 
        target_dict.get("year"), 
        target_dict.get("type", "Monthly"), 
        target_dict.get("week"),
        startDate=target_dict.get("startDate"),
        endDate=target_dict.get("endDate")
    )
    
    doc = await db.sales_targets.find_one(query)
    return fix_id(doc)

async def update_sales_target(db, target_id: str, target_update: schemas.SalesTargetUpdate):
    update_data = target_update.dict(exclude_unset=True)
    result = await db.sales_targets.find_one_and_update(
        {"_id": ObjectId(target_id)},
        {"$set": update_data},
        return_document=True
    )
    return fix_id(result)

async def delete_sales_target(db, target_id: str):
    target = await db.sales_targets.find_one({"_id": ObjectId(target_id)})
    if target:
        query = {
            "type": target.get("type"),
            "targetAmount": target.get("targetAmount")
        }
        if "month" in target: query["month"] = target["month"]
        if "year" in target: query["year"] = target["year"]
        if "week" in target: query["week"] = target["week"]
        if "startDate" in target: query["startDate"] = target["startDate"]
        if "endDate" in target: query["endDate"] = target["endDate"]
        
        await db.sales_targets.delete_many(query)
    else:
        await db.sales_targets.delete_one({"_id": ObjectId(target_id)})
    return True

# Incentive Slab CRUD
async def get_incentive_slabs(db):
    cursor = db.incentive_slabs.find().sort("minAmount", 1)
    rows = await cursor.to_list(length=100)
    return [fix_id(row) for row in rows]

async def create_incentive_slab(db, slab: schemas.IncentiveSlabCreate):
    slab_dict = slab.dict()
    result = await db.incentive_slabs.insert_one(slab_dict)
    doc = await db.incentive_slabs.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def update_incentive_slab(db, slab_id: str, slab_update: schemas.IncentiveSlabUpdate):
    update_data = slab_update.dict(exclude_unset=True)
    result = await db.incentive_slabs.find_one_and_update(
        {"_id": ObjectId(slab_id)},
        {"$set": update_data},
        return_document=True
    )
    return fix_id(result)

async def calculate_sales_incentive(db, total_amount: float, invoice_amount: float, employee_id: str, employee_name: str, category: str = None, is_recurring: bool = False):
    if total_amount <= 0 or invoice_amount <= 0:
        return 0.0, 0.0
        
    # Fetch slabs assigned to this specific employee (match by name)
    cursor = db.incentive_slabs.find({
        "employees": {"$in": [employee_id, employee_name]}
    }).sort("minAmount", 1)
    slabs = await cursor.to_list(length=100)
    
    # If no custom slabs exist for this employee, fetch global default slabs
    if not slabs:
        cursor = db.incentive_slabs.find({
            "$or": [
                {"employees": {"$exists": False}},
                {"employees": {"$size": 0}},
                {"employees": None}
            ]
        }).sort("minAmount", 1)
        slabs = await cursor.to_list(length=100)
    
    if not slabs:
        return 0.0, 0.0

    valid_slabs = []
    for slab in slabs:
        slab_recurring = slab.get("isRecurring", False)
        if slab_recurring != is_recurring:
            continue
            
        slab_cats = slab.get("clientCategories", [])
        if slab_cats and category:
            if category not in slab_cats:
                continue
                
        valid_slabs.append(slab)
        
    if not valid_slabs:
        valid_slabs = slabs
        
    tier_slab = None
    for slab in valid_slabs:
        if total_amount >= slab["minAmount"] and total_amount <= slab["maxAmount"]:
            tier_slab = slab
            break
            
    # If total_amount exceeds all slab maxAmounts, use the highest slab
    if not tier_slab and total_amount > valid_slabs[-1]["minAmount"]:
        tier_slab = valid_slabs[-1]
        
    if tier_slab:
        earned = round((invoice_amount * tier_slab["percentage"]) / 100, 2)
        return earned, tier_slab["percentage"]
        
    return 0.0, 0.0

async def recalculate_sales_target(db, employee_id: str, month: str, year: int, target_type: str = "Monthly", week: Optional[int] = None, startDate: Optional[str] = None, endDate: Optional[str] = None):
    try:
        # 1. Calculate Achievement from First Paid Invoices
        emp = await get_employee(db, employee_id)
        if not emp: return
        emp_name = emp["name"]
        emp_name_norm = " ".join(emp_name.split()).lower()

        # Find all targets matching this employee and period
        emp_obj_id = ObjectId(employee_id) if isinstance(employee_id, str) and len(employee_id) == 24 else employee_id
        target_query = {
            "employeeId": {"$in": [employee_id, emp_obj_id]},
            "type": target_type
        }
        if target_type == "Weekly":
            target_query["month"] = month
            target_query["year"] = year
            target_query["week"] = week
        elif target_type == "Monthly":
            target_query["month"] = month
            target_query["year"] = year
        elif target_type == "Custom":
            target_query["startDate"] = startDate
            target_query["endDate"] = endDate

        targets = await db.sales_targets.find(target_query).to_list(length=100)
        if not targets:
            return

        cursor = db.invoices.find({"status": "Paid"}).sort("timestamp", 1)
        all_paid_invoices = await cursor.to_list(length=10000)
        
        # Fetch all won leads and clients ONCE to avoid db regex issues and improve performance
        won_leads_cursor = db.leads.find({"status": "Client Won"})
        all_won_leads = await won_leads_cursor.to_list(length=10000)
        
        clients_cursor = db.clients.find()
        all_clients = await clients_cursor.to_list(length=10000)
        
        processed_clients = set()
        first_paid_invoices = []
        recurring_paid_invoices = []
        
        for inv in all_paid_invoices:
            c_name = inv.get("clientName", "").strip().lower()
            if not c_name: continue
            
            if c_name not in processed_clients:
                processed_clients.add(c_name)
                first_paid_invoices.append(inv)
            else:
                recurring_paid_invoices.append(inv)
        
        for target in targets:
            target_category = target.get("category") or "Overall"
            
            async def process_invoices_for_emp(invoices_list):
                achievement = 0.0
                incentive_achievement = 0.0
                matched_invoices = []
                for inv in invoices_list:
                    try:
                        c_name_orig = str(inv.get("clientName", "")).strip()
                        if not c_name_orig:
                            continue
                            
                        inv_date_str = inv.get("paymentDate") or inv.get("issueDate") or inv.get("timestamp")
                        if not inv_date_str: continue

                        ld = None
                        if isinstance(inv_date_str, datetime):
                            ld = inv_date_str.replace(tzinfo=None)
                        elif isinstance(inv_date_str, date) and not isinstance(inv_date_str, datetime):
                            ld = datetime(inv_date_str.year, inv_date_str.month, inv_date_str.day)
                        else:
                            try:
                                ld = datetime.fromisoformat(str(inv_date_str).strip()).replace(tzinfo=None)
                            except Exception:
                                date_str = str(inv_date_str).strip()
                                try:
                                    ld = datetime.fromisoformat(date_str)
                                    if ld.tzinfo:
                                        ld = ld.replace(tzinfo=None)
                                except ValueError:
                                    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%b %d, %Y", "%d/%m/%Y", "%d-%m-%Y"):
                                            try:
                                                ld = datetime.strptime(date_str, fmt)
                                                if ld.tzinfo:
                                                    ld = ld.replace(tzinfo=None)
                                                break
                                            except Exception: continue
                        
                        if not ld: continue
                        
                        if target_type == "Custom":
                            if not startDate or not endDate:
                                continue
                            sd = datetime.strptime(startDate, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
                            ed = datetime.strptime(endDate, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
                            if not (sd <= ld <= ed):
                                continue
                        else:
                            month_name_ld = ld.strftime("%B")
                            if month_name_ld != month or ld.year != year:
                                continue
                        
                        if not ld: continue
                        
                        if target_type == "Custom":
                            if not startDate or not endDate:
                                continue
                            sd = datetime.strptime(startDate, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
                            ed = datetime.strptime(endDate, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
                            if not (sd <= ld <= ed):
                                continue
                        else:
                            month_name_ld = ld.strftime("%B")
                            if month_name_ld != month or ld.year != year:
                                continue
                            
                            if target_type == "Weekly" and week:
                                lead_week = (ld.day - 1) // 7 + 1
                                if lead_week != week:
                                    continue

                        invoice_incentives = inv.get("incentives", [])
                        is_matched = False
                        explicit_amount = 0.0
                        
                        if invoice_incentives:
                            for inc in invoice_incentives:
                                if str(inc.get("employeeId")) == str(employee_id) or str(inc.get("employeeName", "")).strip().lower() == emp_name_norm:
                                    is_matched = True
                                    explicit_amount = float(inc.get("amount", 0))
                                    break
                        
                        invoice_category = "Other"
                        if not is_matched and not invoice_incentives:
                            import re
                            c_name_escaped = re.escape(c_name_orig)
                            regex_pattern = f"^\\s*{c_name_escaped}\\s*$"
                            leads_cursor = db.leads.find({
                                "status": "Client Won",
                                "$or": [
                                    {"company": {"$regex": regex_pattern, "$options": "i"}},
                                    {"contact": {"$regex": regex_pattern, "$options": "i"}}
                                ]
                            })
                            matching_leads = await leads_cursor.to_list(length=100)
                            
                            if matching_leads:
                                assigned_lead = None
                                for lead in matching_leads:
                                    raw_assigned = lead.get("assignedTo", [])
                                    def extract_names(item):
                                        if isinstance(item, list):
                                            res = []
                                            for x in item: res.extend(extract_names(x))
                                            return res
                                        elif isinstance(item, dict):
                                            return [item.get("value", "") or item.get("label", "")]
                                        else:
                                            return [str(item)]
                                            
                                    assigned_names = extract_names(raw_assigned)
                                    for name in assigned_names:
                                        if name and " ".join(name.split()).lower() == emp_name_norm:
                                            assigned_lead = lead
                                            break
                                    if assigned_lead:
                                        break
                                
                                if assigned_lead:
                                    is_matched = True
                                    invoice_category = assigned_lead.get("category") or "Other"
                        elif is_matched:
                            import re
                            c_name_escaped = re.escape(c_name_orig)
                            regex_pattern = f"^\\s*{c_name_escaped}\\s*$"
                            lead = await db.leads.find_one({
                                "status": "Client Won",
                                "$or": [
                                    {"company": {"$regex": regex_pattern, "$options": "i"}},
                                    {"contact": {"$regex": regex_pattern, "$options": "i"}}
                                ]
                            })
                            if lead:
                                invoice_category = lead.get("category") or "Other"

                        if not is_matched:
                            continue
                            
                        # Apply category filtering for target
                        if target_category and target_category != "Overall":
                            if invoice_category != target_category:
                                continue

                        if invoice_incentives and is_matched:
                            actual_base = explicit_amount
                            inc_base = explicit_amount
                        else:
                            actual_base = float(inv.get("subtotal", 0))
                            raw_inc_base = inv.get("incentiveAmountBase")
                            if raw_inc_base == "":
                                inc_base = actual_base
                            else:
                                inc_base = float(raw_inc_base if raw_inc_base is not None else actual_base)
                        
                        achievement += actual_base
                        incentive_achievement += inc_base

                        is_recurring = inv in recurring_paid_invoices
                        
                        matched_invoices.append({
                            "invoiceId": str(inv.get("_id", "")),
                            "invoiceNumber": inv.get("invoiceNumber", ""),
                            "clientName": c_name_orig,
                            "subtotal": actual_base,
                            "incentiveBase": inc_base,
                            "category": invoice_category,
                            "isRecurring": is_recurring,
                            "slabPercentage": 0.0,
                            "earnedIncentive": 0.0
                        })
                    except Exception as e:
                        print(f"Error processing invoice in recalculate_sales_target: {e}")
                        continue
                
                final_matched_invoices = []
                for item in matched_invoices:
                    earned, slab_pct = await calculate_sales_incentive(
                        db,
                        total_amount=item["incentiveBase"],
                        invoice_amount=item["incentiveBase"],
                        employee_id=employee_id,
                        employee_name=emp_name,
                        category=item["category"],
                        is_recurring=item["isRecurring"]
                    )
                    item["slabPercentage"] = slab_pct
                    item["earnedIncentive"] = earned
                    final_matched_invoices.append(item)
                    
                return achievement, incentive_achievement, final_matched_invoices
                
            # 2. Calculate Achievements & Incentive
            total_achievement, total_incentive_base, matched_all = await process_invoices_for_emp(all_paid_invoices)
            
            incentive = sum([mi.get("earnedIncentive", 0.0) for mi in matched_all])
            breakdown = matched_all
            
            # 3. Update Target record
            emp_obj_id = ObjectId(employee_id) if isinstance(employee_id, str) and len(employee_id) == 24 else employee_id
            
            target_query = {
                "employeeId": {"$in": [employee_id, emp_obj_id]},
                "type": target_type
            }
            if target_type == "Weekly":
                target_query["month"] = month
                target_query["year"] = year
                target_query["week"] = week
            await db.sales_targets.update_one(
                {"_id": target["_id"]},
                {"$set": {
                    "currentAchievement": total_achievement,
                    "incentiveBase": total_incentive_base,
                    "incentiveAmount": incentive,
                    "breakdown": breakdown
                }}
            )
    except Exception as e:
        print(f"Global error in recalculate_sales_target: {e}")

async def update_employee_daily_report(db, report_id: str, report_update: schemas.EmployeeDailyReportUpdate):
    update_data = report_update.dict(exclude_unset=True)
    performedBy = update_data.get("performedBy", "Unknown")
    userName = update_data.get("userName", "Unknown User")
    
    if not update_data:
        return fix_id(await db.employee_daily_reports.find_one({"_id": ObjectId(report_id)}))
    
    # Fetch existing to check status change
    existing = await db.employee_daily_reports.find_one({"_id": ObjectId(report_id)})
    
    await db.employee_daily_reports.update_one({"_id": ObjectId(report_id)}, {"$set": update_data})
    
    # Apply penalty if status changed to Rejected
    if update_data.get("status") == "Rejected" and existing and existing.get("status") != "Rejected":
        system_settings = await get_system_settings(db)
        if system_settings.get("dailyProgressRejectDeductionEnabled", False):
            await apply_work_rejection_penalty(db, existing["employeeId"], existing["date"])
    elif update_data.get("status") != "Rejected" and existing and existing.get("status") == "Rejected":
        await remove_work_rejection_penalty(db, existing["employeeId"], existing["date"])
        
    # Log activity
    if existing:
        report_date = str(existing.get('date')).split(" ")[0]
        if "status" in update_data and existing.get("status") != update_data["status"]:
            status = update_data["status"]
            await log_activity(
                db,
                f"Daily Report {status}",
                performedBy,
                userName,
                f"{status} daily report for {existing.get('employeeName')} on {report_date}",
                dailyReportId=report_id
            )
        if "note" in update_data and existing.get("note") != update_data["note"]:
            await log_activity(
                db,
                "Verification Note Added",
                performedBy,
                userName,
                f"Verification note for {existing.get('employeeName')} on {report_date} updated to: \"{update_data['note']}\"",
                dailyReportId=report_id
            )
        
    doc = await db.employee_daily_reports.find_one({"_id": ObjectId(report_id)})
    return fix_id(doc)

# Permission CRUD
async def get_user_permissions(db, employee_id: str):
    doc = await db.user_permissions.find_one({"employeeId": employee_id})
    if not doc:
        return None
    return fix_id(doc)

async def save_user_permissions(db, employee_id: str, permissions_data: schemas.UserPermissionUpdate, performed_by: str = "System", user_name: str = "System User"):
    # Fetch employee to get their name
    employee = await db.employees.find_one({"_id": ObjectId(employee_id) if len(employee_id) == 24 else employee_id})
    emp_name = employee.get("name", "Staff") if employee else "Staff"
    
    # Check if there was a previous preset or permissions
    existing = await db.user_permissions.find_one({"employeeId": employee_id})
    old_preset = existing.get("presetId", "Custom") if existing else "None"
    new_preset = permissions_data.presetId or "Custom"

    # Upsert pattern
    await db.user_permissions.update_one(
        {"employeeId": employee_id},
        {"$set": {
            "permissions": [p.dict() for p in permissions_data.permissions],
            "presetId": permissions_data.presetId
        }},
        upsert=True
    )
    
    await log_activity(
        db=db,
        action="Permissions Updated",
        performedBy=performed_by,
        userName=user_name,
        details=f"Permissions updated for '{emp_name}'. Preset changed from '{old_preset}' to '{new_preset}'.",
        employeeId=employee_id
    )
    
    doc = await db.user_permissions.find_one({"employeeId": employee_id})
    return fix_id(doc)

# Permission Presets CRUD
async def get_permission_presets(db, skip: int = 0, limit: int = 100):
    cursor = db.permission_presets.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_permission_preset(db, preset_id: str):
    query = {"_id": ObjectId(preset_id)} if ObjectId.is_valid(preset_id) else {"_id": preset_id}
    doc = await db.permission_presets.find_one(query)
    if doc:
        return fix_id(doc)
    return None

async def sync_designation_presets_for_department(db, department_name: str, permissions: list):
    if not department_name:
        return
    # Fetch all designations globally so presets are created for all titles under this department
    cursor = db.designations.find({})
    designations = await cursor.to_list(length=1000)
    
    clean_perms = [p.dict() if hasattr(p, 'dict') else p for p in permissions]
    
    for desig in designations:
        title = desig.get("title")
        if not title:
            continue
        preset_name = f"{department_name} - {title}"
        
        existing = await db.permission_presets.find_one({
            "presetType": "designation",
            "department": department_name,
            "designation": title
        })
        
        if existing:
            await db.permission_presets.update_one(
                {"_id": existing["_id"]},
                {"$set": {"permissions": clean_perms, "name": preset_name}}
            )
            await db.user_permissions.update_many(
                {"presetId": str(existing["_id"])},
                {"$set": {"permissions": clean_perms}}
            )
        else:
            new_doc = {
                "name": preset_name,
                "description": f"Designation preset for {title} in {department_name}",
                "presetType": "designation",
                "department": department_name,
                "designation": title,
                "permissions": clean_perms
            }
            await db.permission_presets.insert_one(new_doc)

async def create_permission_preset(db, preset: schemas.PermissionPresetCreate):
    doc = preset.dict()
    result = await db.permission_presets.insert_one(doc)
    doc["_id"] = result.inserted_id
    
    if doc.get("presetType") == "department" and (doc.get("department") or doc.get("name")):
        dept_name = doc.get("department") or doc.get("name")
        await sync_designation_presets_for_department(db, dept_name, doc.get("permissions", []))
        
    return fix_id(doc)

async def update_permission_preset(db, preset_id: str, preset_update: schemas.PermissionPresetUpdate):
    try:
        update_data = preset_update.dict(exclude_unset=True)
        query = {"_id": ObjectId(preset_id)} if ObjectId.is_valid(preset_id) else {"_id": preset_id}
        
        if update_data:
            await db.permission_presets.update_one(query, {"$set": update_data})
        
        doc = await db.permission_presets.find_one(query)
        
        if "permissions" in update_data and update_data["permissions"] is not None:
            raw_perms = update_data["permissions"]
            permissions_list = [p.dict() if hasattr(p, 'dict') else p for p in raw_perms]
            await db.user_permissions.update_many(
                {"presetId": preset_id},
                {"$set": {"permissions": permissions_list}}
            )
            
        if doc and doc.get("presetType") == "department":
            dept_name = doc.get("department") or doc.get("name")
            current_perms = doc.get("permissions", [])
            await sync_designation_presets_for_department(db, dept_name, current_perms)

        return fix_id(doc) if doc else None
    except Exception as e:
        print(f"Error updating permission preset {preset_id}: {e}")
        import traceback
        traceback.print_exc()
        raise e

async def delete_permission_preset(db, preset_id: str):
    query = {"_id": ObjectId(preset_id)} if ObjectId.is_valid(preset_id) else {"_id": preset_id}
    result = await db.permission_presets.delete_one(query)
    return result.deleted_count > 0

async def create_time_recovery(db, recovery: schemas.TimeRecoveryCreate):
    doc = recovery.dict()
    doc['created_at'] = get_now().isoformat()
    result = await db.time_recovery.insert_one(doc)
    doc['_id'] = result.inserted_id

    # Send notifications to Admins and HRs (except sender)
    try:
        sender_id = doc.get("employee_id")
        sender = await get_employee(db, sender_id)
        sender_role = str(sender.get("role", "")).lower() if sender else "employee"
        
        # If the sender is an Admin, no notifications are sent at all
        if sender_role != "admin":
            # Fetch all Admins and HRs
            admins_and_hrs = await db.employees.find({
                "role": {"$regex": "^(Admin|HR)$", "$options": "i"}
            }).to_list(length=1000)
            
            for staff in admins_and_hrs:
                staff_id = str(staff["_id"])
                
                # Exclude the sender themselves
                if staff_id == sender_id:
                    continue
                
                # Create the notification document
                rec_date = doc.get('date', '')
                date_str = str(rec_date).split(' ')[0] if rec_date else ''
                notification = {
                    "employee_id": staff_id,
                    "title": "New Time Recovery Request",
                    "message": f"{doc.get('employee_name', 'An employee')} has submitted a time recovery request for {date_str}.",
                    "type": "attendance",
                    "reference_id": str(doc["_id"]),
                    "is_read": False,
                    "created_at": get_now().strftime("%d-%m-%Y %H:%M")
                }
                
                # Insert to db
                await db.notifications.insert_one(notification)
                
                # Broadcast via WebSocket
                try:
                    notification_fixed = fix_id(notification)
                    await ws_manager.send_personal_message(staff_id, "new_notification", notification_fixed)
                except Exception as ws_err:
                    print(f"Error broadcasting time recovery notification to {staff_id}: {ws_err}")
                    
    except Exception as e:
        print(f"Error creating notifications for time recovery: {e}")

    return fix_id(doc)

async def get_time_recoveries(db, skip: int = 0, limit: int = 100):
    cursor = db.time_recovery.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_employee_time_recoveries(db, employee_id: str):
    cursor = db.time_recovery.find({'employee_id': employee_id})
    rows = await cursor.to_list(length=1000)
    return [fix_id(row) for row in rows]

def recalculate_attendance_seconds(punches: list, breaks: list) -> tuple:
    """
    Given a list of raw punches and breaks, sort and merge them,
    and calculate the total accumulated work seconds from the completed punches.
    Returns: (merged_punches, sorted_breaks, accumulated_seconds)
    """
    def parse_time(time_str):
        if not time_str:
            return 0
        parts = time_str.split(':')
        try:
            h = int(parts[0])
            m = int(parts[1])
            s = int(parts[2]) if len(parts) > 2 else 0
            return h * 3600 + m * 60 + s
        except Exception:
            return 0

    # Sort breaks
    sorted_breaks = sorted(breaks, key=lambda b: parse_time(b.get("startTime") or "00:00:00"))
    for b in sorted_breaks:
        if b.get("startTime") and len(b["startTime"].split(':')) == 2:
            b["startTime"] = f"{b['startTime']}:00"
        if b.get("endTime") and len(b["endTime"].split(':')) == 2:
            b["endTime"] = f"{b['endTime']}:00"

    # 1. Sort punches by punchIn
    sorted_punches = sorted(punches, key=lambda p: parse_time(p.get("punchIn") or "00:00:00"))

    # 2. Merge overlapping/contiguous punches
    merged_punches = []
    for p in sorted_punches:
        p_copy = dict(p)
        if not p_copy.get("punchIn"):
            continue
        # normalize format to HH:MM:SS
        in_parts = p_copy["punchIn"].split(':')
        if len(in_parts) == 2:
            p_copy["punchIn"] = f"{p_copy['punchIn']}:00"
        
        out_time = p_copy.get("punchOut")
        if out_time:
            out_parts = out_time.split(':')
            if len(out_parts) == 2:
                p_copy["punchOut"] = f"{out_time}:00"
        
        if not merged_punches:
            merged_punches.append(p_copy)
            continue
        
        last = merged_punches[-1]
        
        # If the last punch has no punchOut, it's active.
        if not last.get("punchOut"):
            merged_punches.append(p_copy)
            continue
            
        last_out_sec = parse_time(last["punchOut"])
        curr_in_sec = parse_time(p_copy["punchIn"])
        
        if curr_in_sec <= last_out_sec:
            # Overlap or contiguous! Merge them.
            if not p_copy.get("punchOut"):
                new_out = None
            else:
                curr_out_sec = parse_time(p_copy["punchOut"])
                if curr_out_sec > last_out_sec:
                    new_out = p_copy["punchOut"]
                else:
                    new_out = last["punchOut"]
            
            last["punchOut"] = new_out
            if last.get("type") == "meeting" or p_copy.get("type") == "meeting":
                last["type"] = "meeting"
            else:
                last["type"] = p_copy.get("type") or last.get("type") or "work"
        else:
            merged_punches.append(p_copy)

    # 3. Calculate accumulated seconds for completed punches
    total_seconds = 0.0
    for p in merged_punches:
        punch_out_str = p.get("punchOut")
        if not punch_out_str:
            continue
        
        p_in_sec = parse_time(p["punchIn"])
        p_out_sec = parse_time(punch_out_str)
        duration = p_out_sec - p_in_sec
        if duration < 0:
            duration += 86400  # handle cross-midnight if any
            
        # Find break overlaps
        break_overlap_sec = 0.0
        for b in sorted_breaks:
            b_start_str = b.get("startTime")
            b_end_str = b.get("endTime")
            if b_start_str and b_end_str:
                b_in_sec = parse_time(b_start_str)
                b_out_sec = parse_time(b_end_str)
                if b_out_sec < b_in_sec:
                    b_out_sec += 86400
                
                # Intersection of [p_in_sec, p_out_sec] and [b_in_sec, b_out_sec]
                overlap_start = max(p_in_sec, b_in_sec)
                overlap_end = min(p_out_sec, b_out_sec)
                if overlap_start < overlap_end:
                    break_overlap_sec += (overlap_end - overlap_start)
                    
        total_seconds += max(0.0, duration - break_overlap_sec)

    return merged_punches, sorted_breaks, total_seconds

async def update_time_recovery_status(db, recovery_id: str, status: str):
    await db.time_recovery.update_one(
        {'_id': ObjectId(recovery_id)},
        {'$set': {'status': status}}
    )
    doc = await db.time_recovery.find_one({'_id': ObjectId(recovery_id)})

    if status == 'approved' and doc:
        try:
            import re
            from datetime import datetime
            
            rec_date = doc.get('date')
            date_vals = [rec_date] if rec_date else []
            if rec_date:
                if isinstance(rec_date, str):
                    date_only_str = rec_date.split(' ')[0]
                    try:
                        dt_naive = datetime.strptime(date_only_str, "%Y-%m-%d")
                        dt_aware = dt_naive.replace(tzinfo=IST)
                        date_vals.extend([dt_naive, dt_aware])
                    except Exception as parse_err:
                        print(f"Error parsing date string {rec_date}: {parse_err}")
                elif hasattr(rec_date, 'year'):
                    try:
                        dt_naive = datetime(rec_date.year, rec_date.month, rec_date.day)
                        dt_aware = dt_naive.replace(tzinfo=IST)
                        date_vals.extend([dt_naive, dt_aware])
                    except Exception as parse_err:
                        print(f"Error converting date object {rec_date}: {parse_err}")

            recovery_type = doc.get('recovery_type')
            start_time = doc.get('start_time')
            end_time = doc.get('end_time')
            
            if recovery_type and start_time and end_time:
                # Modern type-based approval logic
                emp_id = doc['employee_id']
                query_or = [{'employeeId': emp_id}, {'employee_id': emp_id}]
                if ObjectId.is_valid(emp_id):
                    query_or.append({'employeeId': ObjectId(emp_id)})
                    query_or.append({'employee_id': ObjectId(emp_id)})
                    
                search_query = {
                    '$or': query_or,
                    'date': {'$in': date_vals} if date_vals else doc.get('date')
                }
                print(f"DEBUG: Searching attendance records for recovery: {doc['date']}")
                cursor = db.attendance.find(search_query)
                attn_list = await cursor.to_list(length=100)
                
                if attn_list:
                    attn = attn_list[0]
                    
                    # Compute duration
                    ci_dt = datetime.strptime(start_time, "%H:%M:%S" if ":" in start_time else "%H:%M")
                    co_dt = datetime.strptime(end_time, "%H:%M:%S" if ":" in end_time else "%H:%M")
                    duration_seconds = (co_dt - ci_dt).total_seconds()
                    if duration_seconds < 0:
                        duration_seconds += 86400
                        
                    punches = attn.get('punches') or []
                    punches = [dict(p) for p in punches]
                    breaks = attn.get('breaks') or []
                    breaks = [dict(b) for b in breaks]
                    
                    fmt_start = start_time if len(start_time.split(':')) == 3 else f"{start_time}:00"
                    fmt_end = end_time if len(end_time.split(':')) == 3 else f"{end_time}:00"

                    if recovery_type in ['meeting', 'work']:
                        # Fix: Trim or remove breaks that overlap with this recovery
                        def parse_t(t_str):
                            if not t_str: return 0
                            pts = t_str.split(':')
                            try:
                                return int(pts[0])*3600 + int(pts[1])*60 + (int(pts[2]) if len(pts)>2 else 0)
                            except: return 0
                        
                        def format_t(sec):
                            sec = sec % 86400
                            return f"{int(sec//3600):02d}:{int((sec%3600)//60):02d}:{int(sec%60):02d}"
                            
                        rec_in = parse_t(fmt_start)
                        rec_out = parse_t(fmt_end)
                        if rec_out < rec_in:
                            rec_out += 86400
                            
                        new_breaks = []
                        for b in breaks:
                            b_start = b.get("startTime")
                            b_end = b.get("endTime")
                            if not b_start or not b_end:
                                new_breaks.append(b)
                                continue
                                
                            b_in = parse_t(b_start)
                            b_out = parse_t(b_end)
                            if b_out < b_in:
                                b_out += 86400
                                
                            overlap_start = max(rec_in, b_in)
                            overlap_end = min(rec_out, b_out)
                            
                            if overlap_start < overlap_end:
                                # There is overlap
                                if rec_in <= b_in and rec_out >= b_out:
                                    continue # fully engulfed, remove break
                                elif b_in < rec_in and b_out > rec_out:
                                    # Split into two
                                    new_breaks.append({"startTime": b_start, "endTime": format_t(rec_in), "duration": str(int((rec_in - b_in)//60))})
                                    new_breaks.append({"startTime": format_t(rec_out), "endTime": b_end, "duration": str(int((b_out - rec_out)//60))})
                                elif b_in >= rec_in and b_out > rec_out:
                                    # Trim start
                                    new_breaks.append({"startTime": format_t(rec_out), "endTime": b_end, "duration": str(int((b_out - rec_out)//60))})
                                elif b_in < rec_in and b_out <= rec_out:
                                    # Trim end
                                    new_breaks.append({"startTime": b_start, "endTime": format_t(rec_in), "duration": str(int((rec_in - b_in)//60))})
                            else:
                                new_breaks.append(b)
                                
                        breaks = new_breaks

                        punches.append({
                            "punchIn": fmt_start,
                            "punchOut": fmt_end,
                            "type": recovery_type
                        })
                    elif recovery_type == 'break':
                        # Find matching or overlapping break to correct instead of blindly appending
                        best_break = None
                        req_start_mins = (ci_dt.hour * 60) + ci_dt.minute
                        req_end_mins = (co_dt.hour * 60) + co_dt.minute
                        
                        for b in breaks:
                            try:
                                b_start_parts = b.get('startTime', '00:00').split(':')
                                b_start_mins = int(b_start_parts[0]) * 60 + int(b_start_parts[1])
                                b_end_parts = b.get('endTime', '23:59').split(':')
                                b_end_mins = int(b_end_parts[0]) * 60 + int(b_end_parts[1])
                                
                                overlaps = max(req_start_mins, b_start_mins) < min(req_end_mins, b_end_mins)
                                close_start = abs(b_start_mins - req_start_mins) <= 30
                                close_end = abs(b_end_mins - req_end_mins) <= 30
                                
                                if overlaps or close_start or close_end:
                                    best_break = b
                                    break
                            except Exception:
                                continue
                                
                        if best_break:
                            best_break["startTime"] = fmt_start
                            best_break["endTime"] = fmt_end
                            best_break["duration"] = str(int(duration_seconds // 60))
                        else:
                            breaks.append({
                                "startTime": fmt_start,
                                "endTime": fmt_end,
                                "duration": str(int(duration_seconds // 60))
                            })
                    
                    # Sort/merge punches, sort breaks, and recalculate accumulated work seconds
                    merged_punches, sorted_breaks, new_accumulated = recalculate_attendance_seconds(punches, breaks)
                    # Recalculate workHours string
                    hours, remainder = divmod(int(new_accumulated), 3600)
                    minutes, _ = divmod(remainder, 60)
                    work_hours = f"{hours}h {minutes}m"

                    # Determine active punch and status
                    has_active = False
                    active_punch_in = None
                    for p in merged_punches:
                        if not p.get("punchOut"):
                            has_active = True
                            active_punch_in = p.get("punchIn")
                            break
                    
                    has_active_break = False
                    for b in sorted_breaks:
                        if not b.get("endTime"):
                            has_active_break = True
                            break

                    new_status = 'Logged'
                    new_checkout = attn.get('checkOut')
                    
                    set_dict = {
                        'punches': merged_punches,
                        'breaks': sorted_breaks,
                        'accumulatedWorkSeconds': new_accumulated,
                        'workHours': work_hours,
                    }

                    if has_active:
                        new_status = 'On Break' if has_active_break else 'Active'
                        new_checkout = None
                        set_dict['lastPunchIn'] = active_punch_in
                    else:
                        if merged_punches:
                            new_checkout = merged_punches[-1].get("punchOut")

                    set_dict['status'] = new_status
                    set_dict['checkOut'] = new_checkout
                    
                    await db.attendance.update_one(
                        {'_id': attn['_id']},
                        {'$set': set_dict}
                    )
            else:
                reason = doc.get('reason', '')
                
                # Fuzzy search for attendance (find all records for the day)
                emp_id = doc['employee_id']
                query_or = [
                    {'employeeId': {'$regex': f'^{re.escape(str(emp_id))}$', '$options': 'i'}},
                    {'employee_id': {'$regex': f'^{re.escape(str(emp_id))}$', '$options': 'i'}},
                    {'employeeName': {'$regex': f'^{re.escape(str(doc.get("employee_name", "")))}', '$options': 'i'}},
                    {'employee_name': {'$regex': f'^{re.escape(str(doc.get("employee_name", "")))}', '$options': 'i'}}
                ]
                if ObjectId.is_valid(emp_id):
                    query_or.append({'employeeId': ObjectId(emp_id)})
                    query_or.append({'employee_id': ObjectId(emp_id)})
                    
                search_query = {
                    '$or': query_or,
                    'date': {'$in': date_vals} if date_vals else doc.get('date')
                }
                print(f"DEBUG: Searching all attendance records for date: {doc['date']}")
                cursor = db.attendance.find(search_query)
                attn_list = await cursor.to_list(length=100)
                
                if not attn_list:
                    print(f"ERROR: No attendance found for query.")
                    return fix_id(doc)
    
                # Helper to recalculate and save
                async def apply_updates(attn_record, updated_breaks):
                    has_active = not attn_record.get('checkOut') or attn_record.get('checkOut') in [None, "--", "--:--", "", "-"]
                    has_active_break = False
                    for b in updated_breaks:
                        if not b.get("endTime"):
                            has_active_break = True
                            break

                    if not has_active and attn_record.get('checkIn') and attn_record.get('checkOut'):
                        try:
                            ci = datetime.strptime(attn_record['checkIn'], "%H:%M:%S" if ":" in attn_record['checkIn'] else "%H:%M")
                            co = datetime.strptime(attn_record['checkOut'], "%H:%M:%S" if ":" in attn_record['checkOut'] else "%H:%M")
                            diff = co - ci
                            h, r = divmod(diff.total_seconds(), 3600)
                            m, _ = divmod(r, 60)
                            attn_record['workHours'] = f"{int(h)}h {int(m)}m"
                        except Exception: pass
                    
                    new_status = 'On Break' if (has_active and has_active_break) else ('Active' if has_active else 'Logged')
                    new_checkout = None if has_active else attn_record.get('checkOut')
                    
                    await db.attendance.update_one(
                        {'_id': attn_record['_id']},
                        {'$set': {
                            'breaks': updated_breaks,
                            'checkIn': attn_record.get('checkIn'),
                            'checkOut': new_checkout,
                            'workHours': attn_record.get('workHours'),
                            'status': new_status
                        }}
                    )
    
                # Case 1: Break Timing Correction
                break_match = re.search(r'Break-In:\s*(\d{1,2}:\d{2}(?::\d{2})?),?\s*Actual Break-Out:\s*(\d{1,2}:\d{2}(?::\d{2})?)', reason)
                if break_match:
                    break_in, break_out = break_match.group(1), break_match.group(2)
                    # Ensure 2-digit hour for consistency
                    if len(break_in.split(':')[0]) == 1: break_in = '0' + break_in
                    if len(break_out.split(':')[0]) == 1: break_out = '0' + break_out
    
                    print(f"DEBUG: Processing break correction: {break_in} to {break_out}")
    
                    for attn in attn_list:
                        updated = False
                        breaks = attn.get('breaks', [])
                        
                        # Find the BEST matching or overlapping break (closest startTime or overlap)
                        best_break = None
                        
                        # Parse requested times to minutes
                        def to_mins(t_str):
                            if not t_str: return None
                            parts = t_str.split(':')
                            try:
                                return int(parts[0]) * 60 + int(parts[1])
                            except Exception:
                                return None
                                
                        req_start = to_mins(break_in)
                        req_end = to_mins(break_out)
                        
                        if req_start is not None and req_end is not None:
                            for b in breaks:
                                try:
                                    b_start = to_mins(b.get('startTime'))
                                    b_end = to_mins(b.get('endTime'))
                                    if b_start is None:
                                        continue
                                    if b_end is None:
                                        b_end = 23 * 60 + 59  # default to end of day if active
                                        
                                    overlaps = max(req_start, b_start) < min(req_end, b_end)
                                    close_start = abs(b_start - req_start) <= 30
                                    close_end = b_end is not None and abs(b_end - req_end) <= 30
                                    
                                    if overlaps or close_start or close_end:
                                        best_break = b
                                        break
                                except Exception:
                                    continue
                        
                        if best_break:
                            print(f"DEBUG: Best match found in record {attn['_id']}")
                            best_break['startTime'] = break_in if len(break_in.split(':')) == 3 else f"{break_in}:00"
                            best_break['endTime'] = break_out if len(break_out.split(':')) == 3 else f"{break_out}:00"
                            fmt = '%H:%M:%S' if len(break_in.split(':')) == 3 else '%H:%M'
                            t1, t2 = datetime.strptime(break_in, fmt), datetime.strptime(break_out, fmt)
                            best_break['duration'] = str(int((t2 - t1).total_seconds() / 60))
                            updated = True
                        else:
                            # Check if this record's time range covers the requested break
                            try:
                                cin_h, cin_m = map(int, attn['checkIn'].split(':')[:2])
                                cout_h, cout_m = map(int, attn.get('checkOut', '23:59:59').split(':')[:2])
                                req_h, req_m = map(int, break_in.split(':')[:2])
                                if (cin_h * 60 + cin_m) <= (req_h * 60 + req_m) <= (cout_h * 60 + cout_m):
                                    print(f"DEBUG: Adding NEW break to record {attn['_id']}")
                                    fmt = '%H:%M:%S' if len(break_in.split(':')) == 3 else '%H:%M'
                                    t1, t2 = datetime.strptime(break_in, fmt), datetime.strptime(break_out, fmt)
                                    breaks.append({
                                        "startTime": break_in if len(break_in.split(':')) == 3 else f"{break_in}:00",
                                        "endTime": break_out if len(break_out.split(':')) == 3 else f"{break_out}:00",
                                        "duration": str(int((t2 - t1).total_seconds() / 60))
                                    })
                                    updated = True
                            except Exception: pass
    
                        if updated:
                            await apply_updates(attn, breaks)
    
                # Case 2: Late Arrival Correction
                if "Late" in reason or "Punch-in" in reason:
                    time_match = re.search(r'Actual:\s*(\d{1,2}:\d{2}(?::\d{2})?)', reason)
                    if time_match:
                        actual_time = time_match.group(1)
                        if len(actual_time.split(':')[0]) == 1: actual_time = '0' + actual_time
                        print(f"DEBUG: Processing Late Arrival correction: {actual_time}")
                        earliest_attn = min(attn_list, key=lambda x: x.get('checkIn', '23:59:59'))
                        earliest_attn['checkIn'] = actual_time if len(actual_time.split(':')) == 3 else f"{actual_time}:00"
                        await apply_updates(earliest_attn, earliest_attn.get('breaks', []))
    
                # Case 3: Punch-Out Correction
                if "Punch-Out" in reason:
                    time_match = re.search(r'Actual Punch-Out:\s*(\d{1,2}:\d{2}(?::\d{2})?)', reason)
                    if time_match:
                        actual_time = time_match.group(1)
                        if len(actual_time.split(':')[0]) == 1: actual_time = '0' + actual_time
                        print(f"DEBUG: Processing Punch-Out correction: {actual_time}")
                        
                        # Apply to the latest record of the day
                        latest_attn = max(attn_list, key=lambda x: x.get('checkIn', '00:00:00'))
                        latest_attn['checkOut'] = actual_time if len(actual_time.split(':')) == 3 else f"{actual_time}:00"
                        await apply_updates(latest_attn, latest_attn.get('breaks', []))
        except Exception as e:
            print(f"Correction logic error: {e}")

    if doc:
        try:
            status_title = "Approved" if status == "approved" else "Rejected"
            rec_date = doc.get('date', '')
            date_str = str(rec_date).split(' ')[0] if rec_date else ''
            emp_notification = {
                "employee_id": doc["employee_id"],
                "title": f"Time Recovery {status_title}",
                "message": f"Your time recovery request for {date_str} ({doc.get('start_time', '')} - {doc.get('end_time', '')}) has been {status}.",
                "type": "attendance",
                "reference_id": str(doc["_id"]),
                "is_read": False,
                "created_at": get_now().strftime("%d-%m-%Y %H:%M")
            }
            await db.notifications.insert_one(emp_notification)
            
            # Broadcast the notification via WebSocket to the employee
            await ws_manager.send_personal_message(doc["employee_id"], "new_notification", fix_id(emp_notification))
            
            # Broadcast attendance_update via WebSocket to the employee
            await ws_manager.send_personal_message(doc["employee_id"], "attendance_update", {})
        except Exception as ws_err:
            print(f"Error sending recovery status update websocket / notification: {ws_err}")

    return fix_id(doc)


# Invoice CRUD
async def _trigger_sales_target_recalculation(db, invoice_doc):
    try:
        client_name = invoice_doc.get("clientName") or ""
        import re
        client_name_escaped = re.escape(client_name)
        lead = await db.leads.find_one({
            "status": "Client Won",
            "$or": [
                {"company": {"$regex": f"^{client_name_escaped}$", "$options": "i"}},
                {"contact": {"$regex": f"^{client_name_escaped}$", "$options": "i"}}
            ]
        })
        
        if lead:
            assigned_to = lead.get("assignedTo", [])
            if not isinstance(assigned_to, list):
                assigned_to = [assigned_to] if assigned_to else []
            
            for emp_name in assigned_to:
                if not emp_name:
                    continue
                if isinstance(emp_name, dict):
                    emp_name = emp_name.get("value", "") or emp_name.get("label", "")
                if not emp_name:
                    continue
                import re
                emp_name_escaped = re.escape(str(emp_name).strip())
                emp = await db.employees.find_one({"name": {"$regex": f"^{emp_name_escaped}$", "$options": "i"}})
                if not emp:
                    continue
                
                pd_str = invoice_doc.get("paymentDate") or invoice_doc.get("timestamp")
                try:
                    from datetime import datetime
                    pd = datetime.fromisoformat(pd_str) if pd_str else get_now()
                    month_name = pd.strftime("%B")
                    week_num = (pd.day - 1) // 7 + 1
                    emp_id_str = str(emp["_id"])
                    
                    await recalculate_sales_target(db, emp_id_str, month_name, pd.year, "Monthly")
                    await recalculate_sales_target(db, emp_id_str, month_name, pd.year, "Weekly", week_num)
                    
                    emp_obj_id = ObjectId(emp_id_str) if len(emp_id_str) == 24 else None
                    emp_query = [emp_id_str]
                    if emp_obj_id:
                        emp_query.append(emp_obj_id)
                        
                    custom_targets_cursor = db.sales_targets.find({
                        "employeeId": {"$in": emp_query},
                        "type": "Custom"
                    })
                    async for t in custom_targets_cursor:
                        await recalculate_sales_target(
                            db,
                            emp_id_str,
                            t.get("month"),
                            t.get("year"),
                            "Custom",
                            startDate=t.get("startDate"),
                            endDate=t.get("endDate")
                        )
                    await run_payroll_processing(db, month_name, pd.year)
                except Exception as e:
                    print(f"Error triggering recalculate_sales_target from invoice: {e}")
    except Exception as e:
        print(f"Global error in _trigger_sales_target_recalculation: {e}")

async def create_invoice(db, invoice: schemas.InvoiceCreate):
    invoice_dict = invoice.dict()
    invoice_dict["timestamp"] = get_now().isoformat()
    if invoice_dict.get("status") == "Paid":
        invoice_dict["paymentDate"] = get_now().isoformat()
        
    user_name = invoice_dict.get("createdBy") or "Creator"
    invoice_dict["logs"] = [{
        "action": "Invoice Created",
        "remarks": f"Invoice created with initial status: {invoice_dict.get('status', 'Pending')}",
        "timestamp": get_now().isoformat(),
        "userName": user_name
    }]
    
    result = await db.invoices.insert_one(invoice_dict)
    invoice_dict["id"] = str(result.inserted_id)
    if "_id" in invoice_dict:
        invoice_dict.pop("_id")
        
    if invoice_dict.get("status") == "Paid":
        await _trigger_sales_target_recalculation(db, invoice_dict)
        
    return invoice_dict

async def get_invoices(db, current_user, skip: int = 0, limit: int = 100):
    query = {}
    user_role = str(current_user.get("role", "")).strip().lower()
    if user_role not in ["admin", "hr"]:
        user_id_str = str(current_user.get("sub"))
        query = {
            "$or": [
                {"sharedWith": user_id_str},
                {
                    "createdById": user_id_str,
                    "accessManaged": {"$ne": True}
                }
            ]
        }
    cursor = db.invoices.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_invoice(db, invoice_id: str, current_user):
    row = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    if not row:
        return None
        
    user_role = str(current_user.get("role", "")).strip().lower()
    if user_role not in ["admin", "hr"]:
        user_id_str = str(current_user.get("sub"))
        created_by = row.get("createdById")
        shared_with = row.get("sharedWith", [])
        access_managed = row.get("accessManaged", False)
        
        # Has access if explicitly in shared_with OR (creator AND access is not yet managed)
        if user_id_str not in shared_with and not (created_by == user_id_str and not access_managed):
            return None
            
    return fix_id(row)
            
def compare_line_items(old_items, new_items):
    desc = []
    old_items = old_items or []
    new_items = new_items or []
    
    max_len = max(len(old_items), len(new_items))
    for i in range(max_len):
        if i >= len(old_items):
            new_item = new_items[i]
            desc.append(f"Added Item {i+1}: '{new_item.get('description', '')}' (Qty: {new_item.get('qty', 1)}, Rate: {new_item.get('rate', 0)})")
        elif i >= len(new_items):
            old_item = old_items[i]
            desc.append(f"Removed Item {i+1}: '{old_item.get('description', '')}'")
        else:
            old_item = old_items[i]
            new_item = new_items[i]
            item_changes = []
            for field in ["description", "rate", "qty", "discount", "sac"]:
                old_f = old_item.get(field)
                new_f = new_item.get(field)
                if old_f != new_f:
                    if old_f is None and (new_f == "" or new_f == 0):
                        continue
                    item_changes.append(f"{field} changed from '{old_f}' to '{new_f}'")
            if item_changes:
                desc.append(f"Item {i+1} ('{old_item.get('description', '')}') modified: {', '.join(item_changes)}")
    return desc

def compare_incentives(old_inc, new_inc):
    desc = []
    old_inc = old_inc or []
    new_inc = new_inc or []
    
    max_len = max(len(old_inc), len(new_inc))
    for i in range(max_len):
        if i >= len(old_inc):
            new_i = new_inc[i]
            desc.append(f"Added Incentive: {new_i.get('employeeName', '')} (Amount: {new_i.get('amount', 0)})")
        elif i >= len(new_inc):
            old_i = old_inc[i]
            desc.append(f"Removed Incentive: {old_i.get('employeeName', '')}")
        else:
            old_i = old_inc[i]
            new_i = new_inc[i]
            inc_changes = []
            if old_i.get("employeeId") != new_i.get("employeeId") or old_i.get("employeeName") != new_i.get("employeeName"):
                inc_changes.append(f"Employee changed to '{new_i.get('employeeName')}'")
            if old_i.get("amount") != new_i.get("amount"):
                inc_changes.append(f"Amount changed from '{old_i.get('amount')}' to '{new_i.get('amount')}'")
            if inc_changes:
                desc.append(f"Incentive {i+1} modified: {', '.join(inc_changes)}")
    return desc

async def update_invoice(db, invoice_id: str, invoice_update: schemas.InvoiceUpdate, current_user=None):
    update_data = invoice_update.dict(exclude_unset=True)
    
    existing = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    if not existing:
        return None
        
    user_name = "Unknown User"
    if current_user:
        user_name = current_user.get("name") or f"{current_user.get('firstName', '')} {current_user.get('lastName', '')}".strip() or "Unknown User"

    new_logs = []
    timestamp_str = get_now().isoformat()

    changes_desc = []
    for key, val in update_data.items():
        if key in ["logs", "updated_at", "timestamp", "previousStatus"]:
            continue
        old_val = existing.get(key)
        if val != old_val:
            # Skip if both are falsy/empty/None
            if (val is None or val == "" or val == []) and (old_val is None or old_val == "" or old_val == []):
                continue
            
            if key == "lineItems":
                old_list = [dict(x) if not isinstance(x, dict) else x for x in (old_val or [])]
                new_list = [dict(x) if not isinstance(x, dict) else x for x in (val or [])]
                diffs = compare_line_items(old_list, new_list)
                if diffs:
                    changes_desc.extend(diffs)
            elif key == "incentives":
                old_list = [dict(x) if not isinstance(x, dict) else x for x in (old_val or [])]
                new_list = [dict(x) if not isinstance(x, dict) else x for x in (val or [])]
                diffs = compare_incentives(old_list, new_list)
                if diffs:
                    changes_desc.extend(diffs)
            elif key == "status":
                new_logs.append({
                    "action": f"Status Changed to {val}",
                    "remarks": f"Status changed from '{old_val}' to '{val}'",
                    "timestamp": timestamp_str,
                    "userName": user_name
                })
            else:
                import re
                pretty_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', key).title()
                changes_desc.append(f"{pretty_name} changed from '{old_val}' to '{val}'")

    if changes_desc:
        new_logs.append({
            "action": "Invoice Details Updated",
            "remarks": ", ".join(changes_desc),
            "timestamp": timestamp_str,
            "userName": user_name
        })

    existing_logs = existing.get("logs") or []
    if new_logs:
        update_data["logs"] = existing_logs + new_logs

    was_paid = existing.get("status") == "Paid"
    is_paid = update_data.get("status") == "Paid" if "status" in update_data else was_paid
    
    if is_paid and not was_paid:
        update_data["paymentDate"] = get_now().isoformat()
        
    await db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": update_data})
    updated_doc = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    
    if is_paid and updated_doc:
        if not was_paid or "incentiveAmountBase" in update_data:
            await _trigger_sales_target_recalculation(db, updated_doc)
                    
    return fix_id(updated_doc) if updated_doc else None

async def delete_invoice(db, invoice_id: str):
    invoice = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    if invoice:
        await db.deleted_invoices.insert_one(invoice)
        await db.invoices.delete_one({"_id": ObjectId(invoice_id)})
        return True
    return False

async def get_deleted_invoices(db):
    cursor = db.deleted_invoices.find().sort("timestamp", -1)
    invoices = []
    async for doc in cursor:
        invoices.append(fix_id(doc))
    return invoices

async def restore_invoice(db, invoice_id: str):
    invoice = await db.deleted_invoices.find_one({"_id": ObjectId(invoice_id)})
    if invoice:
        await db.invoices.insert_one(invoice)
        await db.deleted_invoices.delete_one({"_id": ObjectId(invoice_id)})
        return True
    return False

async def get_next_invoice_number(db, invoice_type: str = "Tax Invoice", tax_type: str = "CGST+SGST"):
    import re
    
    cursor = db.invoices.find()
    invoices = await cursor.to_list(length=1000)
    
    settings = await get_system_settings(db)
    
    if invoice_type == "Proforma Invoice":
        prefix = settings.get("proformaInvoicePrefix", "PINV")
    else:
        if tax_type == "No Tax":
            prefix = settings.get("noTaxInvoicePrefix", "NINV")
        else:
            prefix = settings.get("taxInvoicePrefix", "INV")
    
    used_nums = set()
    for inv in invoices:
        num_str = inv.get("invoiceNumber", "")
        # ONLY match the current prefix format
        match_simple = re.match(rf'^{prefix}-(\d+)$', num_str)
        
        if match_simple:
            try:
                num = int(match_simple.group(1))
                used_nums.add(num)
            except ValueError:
                pass
                
    next_num = 1
    while next_num in used_nums:
        next_num += 1
        
    return f"{prefix}-{next_num:03d}"

# Referral (Reference) CRUD
async def resolve_referral_status(db, referral: dict) -> dict:
    if not referral:
        return referral
    # Look for matching application by candidateName and phone
    app = await db.applications.find_one({
        "candidateName": referral.get("candidateName"),
        "phone": referral.get("phone")
    })
    if app:
        STAGE_TO_REFERRAL_STATUS = {
            "new": "Contacted",
            "tl_approved": "TL Approved",
            "interview": "Scheduled Interview",
            "selected": "Selected",
            "rejected": "Rejected"
        }
        app_status = app.get("status")
        mapped_status = STAGE_TO_REFERRAL_STATUS.get(app_status)
        if mapped_status:
            referral["status"] = mapped_status
    return referral

async def get_referrals(db, skip: int = 0, limit: int = 10000):
    cursor = db.referrals.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    referrals = [fix_id(row) for row in rows]
    
    resolved_referrals = []
    for ref in referrals:
        resolved = await resolve_referral_status(db, ref)
        # Only return Pending status referrals for the HR's list
        if resolved.get("status") == "Pending":
            resolved_referrals.append(resolved)
    return resolved_referrals

async def get_employee_referrals(db, employee_id: str, skip: int = 0, limit: int = 10000):
    cursor = db.referrals.find({"referredById": employee_id}).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    referrals = [fix_id(row) for row in rows]
    
    resolved_referrals = []
    for ref in referrals:
        resolved = await resolve_referral_status(db, ref)
        resolved_referrals.append(resolved)
    return resolved_referrals

async def create_referral(db, referral: schemas.ReferralCreate):
    referral_dict = referral.dict()
    if not referral_dict.get("submissionDate"):
        referral_dict["submissionDate"] = get_now().strftime("%Y-%m-%d")
    result = await db.referrals.insert_one(referral_dict)
    doc = await db.referrals.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def update_referral(db, referral_id: str, referral_update: schemas.ReferralUpdate):
    update_data = referral_update.dict(exclude_unset=True)
    
    # Get the existing referral first
    existing = await db.referrals.find_one({"_id": ObjectId(referral_id)})
    
    if update_data:
        await db.referrals.update_one({"_id": ObjectId(referral_id)}, {"$set": update_data})
        
        # If status is being updated to 'Contacted' and it wasn't already Contacted
        if existing and update_data.get("status") == "Contacted" and existing.get("status") != "Contacted":
            # 1. Create a new candidate application in 'new' stage
            app_data = {
                "candidateName": existing.get("candidateName"),
                "email": existing.get("email") or "not_provided@example.com",
                "phone": existing.get("phone") or "N/A",
                "status": "new",
                "appliedDate": get_now().strftime("%Y-%m-%d"),
                "resume": existing.get("resumeUrl") or "",
                "jobTitle": existing.get("jobTitle") or "General",
                "reference": f"Referred by: {existing.get('referredByName')}",
                "skills": "",
                "source": "Employee Referral"
            }
            # Add to applications collection
            app_result = await db.applications.insert_one(app_data)
            
            # Log the application creation activity
            await log_activity(
                db, 
                "Created", 
                existing.get("referredById") or "System", 
                existing.get("referredByName") or "System User", 
                f"Candidate '{existing.get('candidateName')}' was added automatically via Employee Referral.", 
                applicationId=str(app_result.inserted_id)
            )
            
    doc = await db.referrals.find_one({"_id": ObjectId(referral_id)})
    return fix_id(doc)

async def delete_referral(db, referral_id: str):
    await db.referrals.delete_one({"_id": ObjectId(referral_id)})
    return True

# Document Templates CRUD
async def create_document_template(db, template: schemas.DocumentTemplateCreate):
    template_dict = template.dict()
    result = await db.document_templates.insert_one(template_dict)
    doc = await db.document_templates.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def get_document_templates(db, skip: int = 0, limit: int = 100):
    cursor = db.document_templates.find().sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def get_document_template(db, template_id: str):
    doc = await db.document_templates.find_one({"_id": ObjectId(template_id)})
    if doc:
        return fix_id(doc)
    return None

async def update_document_template(db, template_id: str, template_update: schemas.DocumentTemplateUpdate):
    update_data = template_update.dict(exclude_unset=True)
    if update_data:
        await db.document_templates.update_one({"_id": ObjectId(template_id)}, {"$set": update_data})
    doc = await db.document_templates.find_one({"_id": ObjectId(template_id)})
    return fix_id(doc)

async def delete_document_template(db, template_id: str):
    result = await db.document_templates.delete_one({"_id": ObjectId(template_id)})
    return result.deleted_count > 0
# Asset Category CRUD
async def get_asset_categories(db, skip: int = 0, limit: int = 100):
    cursor = db.asset_categories.find({"is_user_created": True}).sort("_id", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    return [fix_id(row) for row in rows]

async def sync_category_assets(db, category_name: str, total_items: int, performed_by: str = "System", user_name: str = "System User"):
    # 1. Count existing assets in this category
    existing_count = await db.assets.count_documents({"category": category_name})
    
    if existing_count > total_items:
        to_delete = existing_count - total_items
        # Find unallocated assets to delete (status = "Available")
        cursor = db.assets.find({"category": category_name, "status": "Available"}).limit(to_delete)
        assets_to_delete = await cursor.to_list(length=to_delete)
        
        for asset in assets_to_delete:
            asset_id_str = str(asset["_id"])
            await log_activity(
                db, 
                "Deleted", 
                performed_by, 
                user_name, 
                f"Asset '{asset.get('name')}' ({asset.get('assetId')}) was automatically removed because category '{category_name}' capacity was reduced.", 
                assetId=asset_id_str
            )
            await db.assets.delete_one({"_id": asset["_id"]})
        return

    if existing_count == total_items:
        return
        
    # 2. We need to create (total_items - existing_count) new assets
    to_create = total_items - existing_count
    
    # Generate unique assetIds.
    prefix = "".join([c for c in category_name if c.isalnum()]).upper()[:3]
    if not prefix:
        prefix = "AST"
        
    for i in range(to_create):
        index = existing_count + i + 1
        asset_id = f"HK-{prefix}-{index:03d}"
        
        # Make sure assetId is unique in the db
        while await db.assets.find_one({"assetId": asset_id}):
            index += 1
            asset_id = f"HK-{prefix}-{index:03d}"
            
        asset_doc = {
            "assetId": asset_id,
            "name": f"{category_name} {index}",
            "category": category_name,
            "serialNumber": f"SN-{prefix}-{index:04d}",
            "assignedTo": "",
            "status": "Available",
            "condition": "New",
            "location": "",
            "purchaseDate": None,
            "value": 0.0,
            "description": f"Automatically created resource under category '{category_name}'"
        }
        
        # Insert asset
        result = await db.assets.insert_one(asset_doc)
        inserted_id = str(result.inserted_id)
        
        # Give individual logs for the inventory
        await log_activity(
            db, 
            "Created", 
            performed_by, 
            user_name, 
            f"Asset '{asset_doc['name']}' ({asset_doc['assetId']}) was automatically created under category '{category_name}'.", 
            assetId=inserted_id
        )

async def create_asset_category(db, category: schemas.AssetCategoryCreate):
    category_dict = category.dict()
    performed_by = category_dict.pop("performedBy", "System") or "System"
    user_name = category_dict.pop("userName", "System User") or "System User"
    
    category_dict["is_user_created"] = True
    result = await db.asset_categories.insert_one(category_dict)
    category_id = str(result.inserted_id)
    category_dict["id"] = category_id
    if "_id" in category_dict:
        category_dict.pop("_id")
        
    # Give individual log for the category
    await log_activity(
        db,
        "Created",
        performed_by,
        user_name,
        f"Asset Category '{category_dict['name']}' was created with {category_dict.get('totalItems', 0)} total resources.",
        categoryId=category_id
    )
    
    # Auto create resources in inventory
    await sync_category_assets(db, category_dict["name"], category_dict.get("totalItems", 0), performed_by, user_name)
    
    return category_dict

async def update_asset_category(db, category_id: str, category_update: schemas.AssetCategoryUpdate):
    existing = await db.asset_categories.find_one({"_id": ObjectId(category_id)})
    update_data = category_update.dict(exclude_unset=True)
    performed_by = update_data.pop("performedBy", "System") or "System"
    user_name = update_data.pop("userName", "System User") or "System User"
    
    await db.asset_categories.update_one(
        {"_id": ObjectId(category_id)},
        {"$set": update_data}
    )
    updated_doc = await db.asset_categories.find_one({"_id": ObjectId(category_id)})
    
    # Give individual log for the category
    if existing:
        changes = []
        if "name" in update_data and existing.get("name") != update_data["name"]:
            changes.append(f"Name changed from '{existing.get('name')}' to '{update_data['name']}'")
        if "totalItems" in update_data and existing.get("totalItems") != update_data["totalItems"]:
            changes.append(f"Total Resources changed from {existing.get('totalItems', 0)} to {update_data['totalItems']}")
        if "description" in update_data and existing.get("description") != update_data["description"]:
            changes.append("Description updated")
            
        if not changes:
            changes.append("General category details updated")
            
        log_details = f"Asset Category '{existing.get('name')}': " + ", ".join(changes)
        await log_activity(db, "Updated", performed_by, user_name, log_details, categoryId=category_id)
        
        # If name of the category changed, update category name in all associated assets first
        if "name" in update_data and existing.get("name") != update_data["name"]:
            await db.assets.update_many({"category": existing.get("name")}, {"$set": {"category": update_data["name"]}})
            
    # Auto sync/create resources in inventory
    await sync_category_assets(db, updated_doc["name"], updated_doc.get("totalItems", 0), performed_by, user_name)
        
    return fix_id(updated_doc)

async def delete_asset_category(db, category_id: str, performed_by: str = "System", user_name: str = "System User"):
    existing = await db.asset_categories.find_one({"_id": ObjectId(category_id)})
    category_name = existing.get("name") if existing else None

    if existing:
        await log_activity(
            db,
            "Deleted",
            performed_by,
            user_name,
            f"Asset Category '{category_name}' was deleted along with all its resources.",
            categoryId=category_id
        )

    # Delete all assets belonging to this category
    # NOTE: old assets may use "type" field instead of "category" for compatibility
    if category_name:
        assets_cursor = db.assets.find({
            "$or": [
                {"category": category_name},
                {"type": category_name}
            ]
        })
        assets = await assets_cursor.to_list(length=None)
        for asset in assets:
            asset_id_str = str(asset["_id"])
            asset_name = asset.get("name", "Unknown Asset")
            asset_code = asset.get("assetId", "N/A")
            await log_activity(
                db,
                "Deleted",
                performed_by,
                user_name,
                f"Asset '{asset_name}' ({asset_code}) was deleted because its category '{category_name}' was removed.",
                assetId=asset_id_str
            )
        # Delete all matched assets at once
        await db.assets.delete_many({
            "$or": [
                {"category": category_name},
                {"type": category_name}
            ]
        })

    await db.asset_categories.delete_one({"_id": ObjectId(category_id)})
    return True

# --- Schedule Operations ---
async def _get_creds_and_persist(db, emp):
    """Get Google credentials for an employee and persist refreshed tokens back to DB."""
    token_data = emp.get("googleCalendarTokens")
    if not token_data:
        return None
    try:
        creds, refreshed_token_data = google_auth.get_credentials(token_data)
        if refreshed_token_data:
            # Token was refreshed — save the new access token back to the DB
            await db.employees.update_one(
                {"_id": emp["_id"]},
                {"$set": {"googleCalendarTokens": refreshed_token_data}}
            )
            print(f"[Google Sync] Persisted refreshed token for employee {emp.get('name', emp['_id'])}")
        return creds
    except Exception as e:
        print(f"[Google Sync] Error getting/refreshing credentials for {emp.get('name', emp['_id'])}: {e}")
        return None

async def sync_google_events(db, employee_id: str, start_date_str: str, end_date_str: str):
    if not employee_id:
        return
        
    query = {"employeeId": employee_id}
    if ObjectId.is_valid(employee_id):
        query = {"$or": [{"employeeId": employee_id}, {"_id": ObjectId(employee_id)}]}
    emp = await db.employees.find_one(query)
    
    if not emp or not emp.get("googleCalendarTokens"):
        return
        
    try:
        creds = await _get_creds_and_persist(db, emp)
        if not creds:
            return
            
        tz = pytz.timezone('Asia/Kolkata')
        
        try:
            start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        except Exception:
            return
            
        try:
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        except Exception:
            end_dt = start_dt
            
        time_min_dt = tz.localize(datetime.combine(start_dt, datetime.min.time()))
        time_max_dt = tz.localize(datetime.combine(end_dt, datetime.max.time()))
        
        time_min = time_min_dt.isoformat()
        time_max = time_max_dt.isoformat()
        
        events = await asyncio.to_thread(google_calendar.list_events, creds, time_min, time_max)
        
        # Collect all Google event IDs returned from Google for this date range
        google_event_ids_from_google = set()
        
        if events:
            for event in events:
                event_id = event.get('id')
                if not event_id:
                    continue
                    
                # Skip cancelled events
                if event.get('status') == 'cancelled':
                    continue
                    
                google_event_ids_from_google.add(event_id)
                summary = event.get('summary', 'Google Calendar Event')
                description = event.get('description', '')
                
                start = event.get('start', {})
                end = event.get('end', {})
                
                start_time_str = start.get('dateTime') or start.get('date')
                end_time_str = end.get('dateTime') or end.get('date')
                
                if not start_time_str or not end_time_str:
                    continue
                    
                try:
                    if 'T' in start_time_str:
                        s_dt = datetime.fromisoformat(start_time_str).astimezone(tz)
                        e_dt = datetime.fromisoformat(end_time_str).astimezone(tz)
                    else:
                        s_dt = datetime.strptime(start_time_str, "%Y-%m-%d")
                        e_dt = datetime.strptime(end_time_str, "%Y-%m-%d")
                except Exception:
                    continue
                    
                local_start = s_dt.strftime("%H:%M")
                local_end = e_dt.strftime("%H:%M")
                
                schedule_date = datetime.combine(s_dt.date(), datetime.min.time())
                
                await db.schedules.update_one(
                    {"googleEventId": event_id},
                    {"$set": {
                        "employeeId": employee_id,
                        "employeeName": emp.get("name") or "Unknown",
                        "title": summary,
                        "description": description,
                        "date": schedule_date,
                        "startTime": local_start,
                        "endTime": local_end,
                        "googleEventId": event_id,
                        "type": "Google Event"
                    }},
                    upsert=True
                )
        
        # --- Handle deleted Google events ---
        # Find all local schedules with a googleEventId for this employee in this date range
        # that are NOT in the set of events returned from Google — these were deleted in Google
        date_variants = []
        current = start_dt
        while current <= end_dt:
            date_variants.append(current.strftime("%Y-%m-%d"))
            date_variants.append(current)
            current += timedelta(days=1)
        
        local_google_schedules_cursor = db.schedules.find({
            "employeeId": employee_id,
            "googleEventId": {"$exists": True, "$ne": None},
            "date": {"$in": date_variants}
        })
        local_google_schedules = await local_google_schedules_cursor.to_list(length=1000)
        
        for local_sched in local_google_schedules:
            local_gid = local_sched.get("googleEventId")
            if local_gid and local_gid not in google_event_ids_from_google:
                # This event was deleted or cancelled in Google Calendar — remove it locally
                await db.schedules.delete_one({"_id": local_sched["_id"]})
                print(f"[Google Sync] Removed locally deleted Google event: {local_sched.get('title', 'Unknown')} (gid: {local_gid})")
            
    except Exception as e:
        print(f"Error syncing Google Calendar events: {e}")

async def get_schedules(db, employee_id: str = None, date_str: str = None, date_from: str = None, date_to: str = None):
    if date_str or (date_from and date_to):
        d_from = date_from or date_str
        d_to = date_to or date_str
        if employee_id:
            await sync_google_events(db, employee_id, d_from, d_to)
        else:
            cursor = db.employees.find({"googleCalendarTokens": {"$exists": True, "$ne": None}})
            employees_with_tokens = await cursor.to_list(length=1000)
            tasks = [sync_google_events(db, str(emp["_id"]), d_from, d_to) for emp in employees_with_tokens]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        
    query = {}
    if employee_id:
        query["$or"] = [
            {"employeeId": employee_id},
            {"attendees": employee_id}
        ]
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
            query["date"] = {"$in": [date_str, target_date]}
        except Exception:
            query["date"] = date_str
    elif date_from and date_to:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d")
            end = datetime.strptime(date_to, "%Y-%m-%d")
            date_variants = []
            current = start
            while current <= end:
                date_variants.append(current.strftime("%Y-%m-%d"))
                date_variants.append(current)
                current += timedelta(days=1)
            query["date"] = {"$in": date_variants}
        except Exception:
            pass
    cursor = db.schedules.find(query)
    schedules = await cursor.to_list(length=1000)
    return [fix_id(s) for s in schedules]

async def create_schedule(db, schedule_data: dict):
    # Try to push to Google Calendar
    emp_id = schedule_data.get("employeeId")
    if emp_id:
        query = {"employeeId": emp_id}
        if ObjectId.is_valid(emp_id):
            query = {"$or": [{"employeeId": emp_id}, {"_id": ObjectId(emp_id)}]}
        emp = await db.employees.find_one(query)
        if emp and emp.get("googleCalendarTokens"):
            try:
                creds = await _get_creds_and_persist(db, emp)
                if creds:
                    event_id = await asyncio.to_thread(google_calendar.create_event, creds, schedule_data)
                    if event_id:
                        schedule_data["googleEventId"] = event_id
            except Exception as e:
                print(f"Error syncing new schedule to Google Calendar: {e}")

    new_doc = await db.schedules.insert_one(schedule_data)
    created = await db.schedules.find_one({"_id": new_doc.inserted_id})
    return fix_id(created)

async def update_schedule(db, schedule_id: str, schedule_data: dict):
    query = {"_id": ObjectId(schedule_id)} if ObjectId.is_valid(schedule_id) else {"_id": schedule_id}
    
    existing = await db.schedules.find_one(query)
    if not existing:
        existing = await db.schedules.find_one({"$or": [{"id": schedule_id}, {"googleEventId": schedule_id}]})
        if not existing:
            return None
        query = {"_id": existing["_id"]}
    
    await db.schedules.update_one(
        query,
        {"$set": schedule_data}
    )
    updated = await db.schedules.find_one(query)
    
    # Sync update to Google Calendar
    if updated and existing:
        emp_id = updated.get("employeeId")
        google_event_id = updated.get("googleEventId") or existing.get("googleEventId")
        if emp_id and google_event_id:
            query = {"employeeId": emp_id}
            if ObjectId.is_valid(emp_id):
                query = {"$or": [{"employeeId": emp_id}, {"_id": ObjectId(emp_id)}]}
            emp = await db.employees.find_one(query)
            if emp and emp.get("googleCalendarTokens"):
                try:
                    creds = await _get_creds_and_persist(db, emp)
                    if creds:
                        await asyncio.to_thread(google_calendar.update_event, creds, google_event_id, updated)
                except Exception as e:
                    print(f"Error syncing updated schedule to Google Calendar: {e}")

    return fix_id(updated) if updated else None

async def delete_schedule(db, schedule_id: str):
    query = {"_id": ObjectId(schedule_id)} if ObjectId.is_valid(schedule_id) else {"_id": schedule_id}
    
    existing = await db.schedules.find_one(query)
    if not existing:
        # Fallback to checking by string 'id' or 'googleEventId' just in case
        existing = await db.schedules.find_one({"$or": [{"id": schedule_id}, {"googleEventId": schedule_id}]})
        if not existing:
            return False
        query = {"_id": existing["_id"]}
        
    res = await db.schedules.delete_one(query)
    
    # Sync delete to Google Calendar
    if res.deleted_count > 0 and existing:
        emp_id = existing.get("employeeId")
        google_event_id = existing.get("googleEventId")
        if emp_id and google_event_id:
            query = {"employeeId": emp_id}
            if ObjectId.is_valid(emp_id):
                query = {"$or": [{"employeeId": emp_id}, {"_id": ObjectId(emp_id)}]}
            emp = await db.employees.find_one(query)
            if emp and emp.get("googleCalendarTokens"):
                try:
                    creds = await _get_creds_and_persist(db, emp)
                    if creds:
                        await asyncio.to_thread(google_calendar.delete_event, creds, google_event_id)
                except Exception as e:
                    print(f"Error syncing deleted schedule to Google Calendar: {e}")
                    
    return res.deleted_count > 0

# --- Appointment Configuration Operations ---
async def get_appointment_config(db, employee_id: str):
    config = None
    if ObjectId.is_valid(employee_id) and len(str(employee_id)) == 24:
        config = await db.appointment_configs.find_one({"_id": ObjectId(employee_id)})
    if not config:
        config = await db.appointment_configs.find_one({"employeeId": employee_id})
    return fix_id(config) if config else None

async def delete_appointment_config(db, config_id: str):
    if not ObjectId.is_valid(config_id):
        return False
    res = await db.appointment_configs.delete_one({"_id": ObjectId(config_id)})
    return res.deleted_count > 0

async def save_appointment_config(db, config_data: dict):
    employee_id = config_data.get("employeeId")
    if not employee_id:
        raise ValueError("employeeId is required")
    
    config_id = config_data.get("id") or config_data.get("_id")
    data_to_save = {k: v for k, v in config_data.items() if k not in ["id", "_id"]}
    
    if config_id and ObjectId.is_valid(config_id) and len(str(config_id)) == 24:
        existing = await db.appointment_configs.find_one({"_id": ObjectId(config_id)})
        if existing:
            data_to_save["updated_at"] = datetime.now()
            await db.appointment_configs.update_one(
                {"_id": ObjectId(config_id)},
                {"$set": data_to_save}
            )
            updated = await db.appointment_configs.find_one({"_id": ObjectId(config_id)})
            return fix_id(updated)
            
    data_to_save["created_at"] = datetime.now()
    data_to_save["updated_at"] = datetime.now()
    new_doc = await db.appointment_configs.insert_one(data_to_save)
    created = await db.appointment_configs.find_one({"_id": new_doc.inserted_id})
    return fix_id(created)

async def calculate_public_slots(db, employee_id: str, date_str: str, config_id: str = None):
    config = None
    if config_id and ObjectId.is_valid(config_id) and len(str(config_id)) == 24:
        config = await db.appointment_configs.find_one({"_id": ObjectId(config_id)})
    if not config and ObjectId.is_valid(employee_id) and len(str(employee_id)) == 24:
        config = await db.appointment_configs.find_one({"_id": ObjectId(employee_id)})
    if not config:
        config = await db.appointment_configs.find_one({"employeeId": employee_id})
    if not config or not config.get("active", True):
        return []
    
    duration = config.get("duration", 30)
    availability = config.get("availability", {})
    
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return []
    
    recurrence = config.get("recurrence", "weekly")
    if recurrence == "none":
        day_slots = config.get("specificDates", {}).get(date_str, [])
    else:
        day_name = target_date.strftime("%A")
        day_slots = availability.get(day_name, [])
        
    if not day_slots:
        return []
    
    real_emp_id = config.get("employeeId", employee_id)
    all_member_ids = [str(real_emp_id)]
    if config.get("employeeIds"):
        all_member_ids.extend([str(x) for x in config.get("employeeIds")])
    all_member_ids = list(set(all_member_ids))

    # Sync Google Calendar events for all members before calculating available slots
    sync_tasks = []
    for member_id in all_member_ids:
        sync_tasks.append(sync_google_events(db, member_id, date_str, date_str))
    if sync_tasks:
        await asyncio.gather(*sync_tasks, return_exceptions=True)

    query = {
        "$or": [
            {"employeeId": {"$in": all_member_ids}},
            {"attendees": {"$in": all_member_ids}}
        ]
    }
    query["date"] = {"$in": [date_str, target_date]}
    
    cursor = db.schedules.find(query)
    existing_schedules = await cursor.to_list(length=1000)
    
    def time_str_to_mins(t_str: str):
        try:
            parts = t_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return 0
            
    existing_ranges = []
    for s in existing_schedules:
        s_start = s.get("startTime")
        s_end = s.get("endTime")
        if s_start and s_end:
            start_mins = time_str_to_mins(s_start)
            end_mins = time_str_to_mins(s_end)
            # Full day event (00:00 to 00:00) blocks the entire day
            if start_mins == 0 and end_mins == 0:
                end_mins = 1440
            existing_ranges.append((start_mins, end_mins))
            
    candidate_slots = []
    for window in day_slots:
        w_start = time_str_to_mins(window.get("start", "09:00"))
        w_end = time_str_to_mins(window.get("end", "17:00"))
        
        current = w_start
        while current + duration <= w_end:
            slot_end = current + duration
            
            overlap = False
            for ex_start, ex_end in existing_ranges:
                if max(current, ex_start) < min(slot_end, ex_end):
                    overlap = True
                    break
            
            if not overlap:
                start_str = f"{current // 60:02d}:{current % 60:02d}"
                end_str = f"{slot_end // 60:02d}:{slot_end % 60:02d}"
                candidate_slots.append({"start": start_str, "end": end_str})
                
            current += duration
            
    try:
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        today_ist_str = now_ist.strftime("%Y-%m-%d")
        if date_str == today_ist_str:
            now_mins = now_ist.hour * 60 + now_ist.minute
            now_mins += 10
            candidate_slots = [
                slot for slot in candidate_slots
                if time_str_to_mins(slot["start"]) > now_mins
            ]
    except Exception as e:
        print(f"Error filtering past slots: {e}")
        
    return candidate_slots

# --- User Activity Input Tracking Operations ---
async def track_user_activity(db, employee_id: str, clicks: int, keystrokes: int, applications: Optional[dict] = None, domains: Optional[dict] = None):
    # Find the employee first to get their name
    employee = None
    if ObjectId.is_valid(employee_id):
        employee = await db.employees.find_one({"_id": ObjectId(employee_id)})
    if not employee:
        employee = await db.employees.find_one({"_id": employee_id})
    if not employee:
        employee = await db.employees.find_one({"employeeId": employee_id})

    employee_name = "Unknown Employee"
    if employee:
        employee_name = employee.get("name") or f"{employee.get('firstName', '')} {employee.get('lastName', '')}".strip() or "Unnamed"

    today_str = get_now().strftime("%Y-%m-%d")

    # Construct increments dynamically
    inc_updates = {
        "clicks": clicks,
        "keystrokes": keystrokes
    }

    if applications:
        for app, seconds in applications.items():
            # Replace dots with underscores to prevent MongoDB path errors
            safe_app = app.replace(".", "_")
            inc_updates[f"applications.{safe_app}"] = seconds

    if domains:
        for domain, seconds in domains.items():
            safe_domain = domain.replace(".", "_")
            inc_updates[f"domains.{safe_domain}"] = seconds

    # Upsert the daily record for this user
    await db.user_input_stats.update_one(
        {"employeeId": employee_id, "date": today_str},
        {
            "$set": {
                "employeeName": employee_name,
                "lastActive": get_now()
            },
            "$inc": inc_updates
        },
        upsert=True
    )
    
    updated = await db.user_input_stats.find_one({"employeeId": employee_id, "date": today_str})
    return fix_id(updated)

async def get_user_activity_stats(db, employee_id: str = None):
    query = {}
    if employee_id:
        query["employeeId"] = employee_id
    cursor = db.user_input_stats.find(query)
    stats = await cursor.to_list(length=10000)
    return [fix_id(s) for s in stats]


# --- Registered PC Device Operations ---
async def register_pc_device(db, hostname: str, ip_address: str, os_name: str, os_version: str):
    import re
    existing = await db.registered_pcs.find_one(
        {"hostname": {"$regex": f"^{re.escape(hostname)}$", "$options": "i"}}
    )
    if existing:
        await db.registered_pcs.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "ipAddress": ip_address,
                    "os": os_name,
                    "osVersion": os_version,
                    "lastSeen": get_now()
                }
            }
        )
        return await db.registered_pcs.find_one({"_id": existing["_id"]})
    else:
        new_pc = {
            "hostname": hostname,
            "ipAddress": ip_address,
            "os": os_name,
            "osVersion": os_version,
            "lastSeen": get_now(),
            "firstSeen": get_now(),
            "blockChrome": False,
            "blockYoutube": False,
            "blockApps": [],
            "blockUrls": []
        }
        await db.registered_pcs.insert_one(new_pc)
        return new_pc

async def get_registered_pcs(db):
    cursor = db.registered_pcs.find({})
    pcs = await cursor.to_list(length=1000)
    return [fix_id(pc) for pc in pcs]

async def update_pc_restrictions(db, hostname: str, block_chrome: Optional[bool] = None, block_youtube: Optional[bool] = None, block_apps: Optional[List[str]] = None, block_urls: Optional[List[str]] = None):
    import re
    update_fields = {}
    if block_chrome is not None:
        update_fields["blockChrome"] = block_chrome
    if block_youtube is not None:
        update_fields["blockYoutube"] = block_youtube
    if block_apps is not None:
        update_fields["blockApps"] = block_apps
    if block_urls is not None:
        update_fields["blockUrls"] = block_urls
        
    if update_fields:
        await db.registered_pcs.update_one(
            {"hostname": {"$regex": f"^{re.escape(hostname)}$", "$options": "i"}},
            {"$set": update_fields}
        )
    
    updated = await db.registered_pcs.find_one({"hostname": {"$regex": f"^{re.escape(hostname)}$", "$options": "i"}})
    return fix_id(updated) if updated else None
# --- Content Calendar Operations ---
async def get_all_content_calendar_entries(db):
    try:
        cursor = db.content_calendar_entries.find({})
        entries = await cursor.to_list(length=5000)
        return [fix_id(e) for e in entries]
    except Exception as e:
        raise e

async def get_content_calendar_entries(db, client_id: str, project_id: str = None, month_year: str = None, include_legacy: bool = False):
    try:
        query = {"clientId": client_id}
        and_conditions = []
        
        if project_id:
            if include_legacy:
                and_conditions.append({
                    "$or": [
                        {"projectId": project_id},
                        {"projectId": {"$exists": False}},
                        {"projectId": None},
                        {"projectId": ""}
                    ]
                })
            else:
                query["projectId"] = project_id
                
        if month_year:
            month_cond = {
                "$or": [
                    {"monthYear": month_year},
                    {"postingDate": {"$regex": f"^{month_year}"}},
                    {"scriptDate": {"$regex": f"^{month_year}"}},
                    {"shootDate": {"$regex": f"^{month_year}"}},
                    {"editingStart": {"$regex": f"^{month_year}"}},
                    {"actualPostingDate": {"$regex": f"^{month_year}"}}
                ]
            }
            if "$or" in query:
                and_conditions.append(month_cond)
            else:
                and_conditions.append(month_cond)
                
        if and_conditions:
            query["$and"] = and_conditions
            
        cursor = db.content_calendar_entries.find(query)
        entries = await cursor.to_list(length=1000)
        return [fix_id(e) for e in entries]
    except Exception as e:
        import traceback
        with open("error_log.txt", "w") as f:
            f.write(traceback.format_exc())
        raise e

async def create_content_calendar_entry(db, entry_data: dict):
    updated_by = entry_data.pop("updatedBy", "Unknown User")
    
    if entry_data.get("postReel") == "Post" and not entry_data.get("shootLink"):
        entry_data["shootLink"] = "-"
    elif entry_data.get("postReel") == "Story":
        for k in ["scriptLink", "shootLink", "thumbnailLink", "caption", "postingLinkOfIg", "finalPostLink"]:
            if not entry_data.get(k):
                entry_data[k] = "-"
        for k in ["scriptDate", "shootDate", "thumbnailDate", "captionDate"]:
            if not entry_data.get(k):
                entry_data[k] = "-"
        entry_data["assignedBrandPersonIds"] = []
        
    entry_data["logs"] = [{
        "timestamp": datetime.now(IST).isoformat(),
        "action": "Row created",
        "details": "Initial entry created",
        "userName": updated_by
    }]
    new_doc = await db.content_calendar_entries.insert_one(entry_data)
    created = await db.content_calendar_entries.find_one({"_id": new_doc.inserted_id})
    return fix_id(created)

async def update_content_calendar_entry(db, entry_id: str, update_data: dict):
    if not ObjectId.is_valid(entry_id):
        return None
        
    existing = await db.content_calendar_entries.find_one({"_id": ObjectId(entry_id)})
    if not existing:
        return None
        
    changes = []
    updated_by = update_data.get("updatedBy", "Unknown User")
    curr_post_reel = update_data.get("postReel") or existing.get("postReel")
    if curr_post_reel == "Post":
        if not update_data.get("shootLink") and not existing.get("shootLink"):
            update_data["shootLink"] = "-"
    elif curr_post_reel == "Story":
        for k in ["scriptLink", "shootLink", "thumbnailLink", "caption", "postingLinkOfIg", "finalPostLink"]:
            if not update_data.get(k) and not existing.get(k):
                update_data[k] = "-"
            elif update_data.get(k) == "":
                update_data[k] = "-"
        for k in ["scriptDate", "shootDate", "thumbnailDate", "captionDate"]:
            if not update_data.get(k) and not existing.get(k):
                update_data[k] = "-"
            elif update_data.get(k) == "":
                update_data[k] = "-"
        if "assignedBrandPersonIds" not in update_data:
            update_data["assignedBrandPersonIds"] = []
            
    for key, val in update_data.items():
        if key not in ["logs", "clientId", "monthYear", "id", "_id", "updated_at", "created_at", "updatedAt", "createdAt", "updatedBy"]:
            old_val = existing.get(key)
            if old_val != val:
                changes.append(f"'{key}' changed to '{val or ''}'")
                
    if changes:
        new_log = {
            "timestamp": datetime.now(IST).isoformat(),
            "action": "Row updated",
            "details": ", ".join(changes),
            "userName": updated_by
        }
        logs = existing.get("logs", [])
        logs.append(new_log)
        update_data["logs"] = logs
        
    await db.content_calendar_entries.update_one(
        {"_id": ObjectId(entry_id)},
        {"$set": update_data}
    )
    updated = await db.content_calendar_entries.find_one({"_id": ObjectId(entry_id)})
    return fix_id(updated) if updated else None

async def delete_content_calendar_entry(db, entry_id: str):
    if not ObjectId.is_valid(entry_id):
        return False
    res = await db.content_calendar_entries.delete_one({"_id": ObjectId(entry_id)})
    return res.deleted_count > 0

async def get_content_calendar_settings(db, client_id: str, month_year: str, project_id: str = None):
    query = {
        "clientId": client_id,
        "monthYear": month_year
    }
    if project_id:
        query["projectId"] = project_id
    settings = await db.content_calendar_settings.find_one(query)
    if settings:
        return fix_id(settings)
    return None

async def get_all_content_calendar_settings(db, month_year: str):
    cursor = db.content_calendar_settings.find({"monthYear": month_year})
    settings = await cursor.to_list(length=1000)
    return [fix_id(s) for s in settings]

async def upsert_content_calendar_settings(db, client_id: str, month_year: str, project_id: str, update_data: dict):
    query = {
        "clientId": client_id,
        "monthYear": month_year
    }
    if project_id:
        query["projectId"] = project_id

    if "_id" in update_data:
        del update_data["_id"]

    await db.content_calendar_settings.update_one(
        query,
        {"$set": update_data},
        upsert=True
    )
    return await get_content_calendar_settings(db, client_id, month_year, project_id)

# Dynamic Feedback Forms

async def create_feedback_form(db, form: schemas.FeedbackFormCreate, createdBy: str = "Unknown"):
    form_dict = form.dict()
    form_dict["createdAt"] = get_now().strftime("%Y-%m-%d %H:%M:%S")
    form_dict["createdBy"] = createdBy
    res = await db.feedback_forms.insert_one(form_dict)
    doc = await db.feedback_forms.find_one({"_id": res.inserted_id})
    return fix_id(doc)

async def get_feedback_form(db, form_id: str):
    if not ObjectId.is_valid(form_id):
        return None
    doc = await db.feedback_forms.find_one({"_id": ObjectId(form_id)})
    return fix_id(doc) if doc else None

async def get_client_feedback_forms(db, client_id: str):
    cursor = db.feedback_forms.find({"clientId": client_id}).sort("createdAt", -1)
    return [fix_id(doc) async for doc in cursor]

async def create_feedback_response(db, response: schemas.FeedbackResponseCreate):
    resp_dict = response.dict()
    resp_dict["submittedAt"] = get_now().strftime("%Y-%m-%d %H:%M:%S")
    res = await db.feedback_responses.insert_one(resp_dict)
    doc = await db.feedback_responses.find_one({"_id": res.inserted_id})
    return fix_id(doc)

async def get_all_feedback_responses(db):
    cursor = db.feedback_responses.find().sort("submittedAt", -1)
    return [fix_id(doc) async for doc in cursor]

async def get_all_feedback_forms(db):
    cursor = db.feedback_forms.find().sort("createdAt", -1)
    return [fix_id(doc) async for doc in cursor]

async def get_form_responses(db, form_id: str):
    cursor = db.feedback_responses.find({"formId": form_id}).sort("submittedAt", -1)
    return [fix_id(doc) async for doc in cursor]

async def get_client_form_responses(db, client_id: str):
    cursor = db.feedback_responses.find({"clientId": client_id}).sort("submittedAt", -1)
    return [fix_id(doc) async for doc in cursor]

async def update_feedback_form(db, form_id: str, form_data: schemas.FeedbackFormCreate):
    if not ObjectId.is_valid(form_id):
        return None
    
    update_data = form_data.dict()
    res = await db.feedback_forms.update_one(
        {"_id": ObjectId(form_id)},
        {"$set": update_data}
    )
    if res.modified_count == 0 and res.matched_count == 0:
        return None
    return await get_feedback_form(db, form_id)

async def delete_feedback_form(db, form_id: str):
    if not ObjectId.is_valid(form_id):
        return False
        
    # Cascade delete responses first
    await db.feedback_responses.delete_many({"formId": form_id})
    
    # Delete the form
    res = await db.feedback_forms.delete_one({"_id": ObjectId(form_id)})
    return res.deleted_count > 0

# --- Other Work ---
async def get_all_other_work(db):
    cursor = db.other_work.find().sort("created_at", -1)
    items = await cursor.to_list(length=5000)
    return [fix_id(item) for item in items]

async def create_other_work(db, data: dict):
    from datetime import datetime
    data["created_at"] = datetime.now().isoformat()
    data["updated_at"] = datetime.now().isoformat()
    if "logs" not in data or not data["logs"]:
        data["logs"] = [{
            "timestamp": data["created_at"],
            "action": "Task created",
            "details": f"Task '{data.get('title')}' created by {data.get('assignerName')}",
            "userName": data.get('assignerName', "System")
        }]
    res = await db.other_work.insert_one(data)
    doc = await db.other_work.find_one({"_id": res.inserted_id})
    return fix_id(doc)

async def update_other_work(db, work_id: str, update_data: dict):
    from bson import ObjectId
    from datetime import datetime
    if not ObjectId.is_valid(work_id):
        return None
    existing = await db.other_work.find_one({"_id": ObjectId(work_id)})
    if not existing:
        return None
    
    update_data["updated_at"] = datetime.now().isoformat()
    
    changes = []
    updater = update_data.pop("updatedBy", "System")
    for k, v in update_data.items():
        if k not in ["logs", "created_at", "updated_at"]:
            if existing.get(k) != v:
                changes.append(f"'{k}' changed to '{v}'")
                
    if changes:
        logs = existing.get("logs", [])
        logs.append({
            "timestamp": datetime.now().isoformat(),
            "action": "Task updated",
            "details": ", ".join(changes),
            "userName": updater
        })
        update_data["logs"] = logs
        
    await db.other_work.update_one({"_id": ObjectId(work_id)}, {"$set": update_data})
    doc = await db.other_work.find_one({"_id": ObjectId(work_id)})
    return fix_id(doc)

async def delete_other_work(db, work_id: str):
    from bson import ObjectId
    if not ObjectId.is_valid(work_id):
        return False
    res = await db.other_work.delete_one({"_id": ObjectId(work_id)})
    return res.deleted_count > 0

async def get_all_transfer_requests(db, task_id: str = None, task_type: str = None):
    query = {}
    if task_id:
        query["taskId"] = task_id
    if task_type:
        if task_type in ["smm", "all"]:
            query["taskType"] = {"$in": ["content-calendar", "creative", "other-work"]}
        elif task_type in ["wm-task", "wm-tasks"]:
            query["taskType"] = {"$in": ["wm-task", "wm-tasks"]}
        else:
            query["taskType"] = task_type
    cursor = db.work_transfer_requests.find(query).sort("createdDate", -1)
    rows = await cursor.to_list(length=1000)
    return [fix_id(row) for row in rows]

async def create_transfer_request(db, request_data: dict):
    # Check if there is already a Pending request for the same taskId and stage
    existing = await db.work_transfer_requests.find_one({
        "taskId": request_data["taskId"],
        "stage": request_data["stage"],
        "status": "Pending"
    })
    if existing:
        raise ValueError("A pending transfer request already exists for this task stage.")
        
    request_data["createdDate"] = datetime.now(IST).isoformat()
    request_data["status"] = "Pending"
    
    res = await db.work_transfer_requests.insert_one(request_data)
    doc = await db.work_transfer_requests.find_one({"_id": res.inserted_id})

    # Create and broadcast notification to receiver
    try:
        await create_notification(db, schemas.NotificationCreate(
            employee_id=request_data["receiverId"],
            title="New Task Transfer Request",
            message=f"{request_data['senderName']} requested to transfer task '{request_data['taskName']}' ({request_data['stage']}) to you.",
            type="work_transfer",
            reference_id=str(res.inserted_id)
        ))
    except Exception as e:
        print(f"Error creating transfer notification: {e}")
        
    return fix_id(doc)

async def get_incoming_transfer_requests(db, receiver_id: str, task_type: str = None):
    query = {"receiverId": receiver_id}
    if task_type:
        if task_type in ["smm", "all"]:
            query["taskType"] = {"$in": ["content-calendar", "creative", "other-work"]}
        elif task_type in ["wm-task", "wm-tasks"]:
            query["taskType"] = {"$in": ["wm-task", "wm-tasks"]}
        else:
            query["taskType"] = task_type
    cursor = db.work_transfer_requests.find(query).sort("createdDate", -1)
    rows = await cursor.to_list(length=1000)
    return [fix_id(row) for row in rows]

async def get_outgoing_transfer_requests(db, sender_id: str, task_type: str = None):
    query = {"senderId": sender_id}
    if task_type:
        if task_type in ["smm", "all"]:
            query["taskType"] = {"$in": ["content-calendar", "creative", "other-work"]}
        elif task_type in ["wm-task", "wm-tasks"]:
            query["taskType"] = {"$in": ["wm-task", "wm-tasks"]}
        else:
            query["taskType"] = task_type
    cursor = db.work_transfer_requests.find(query).sort("createdDate", -1)
    rows = await cursor.to_list(length=1000)
    return [fix_id(row) for row in rows]

async def respond_to_transfer_request(db, request_id: str, status: str):
    if not ObjectId.is_valid(request_id):
        return None
        
    req = await db.work_transfer_requests.find_one({"_id": ObjectId(request_id)})
    if not req:
        return None
        
    await db.work_transfer_requests.update_one(
        {"_id": ObjectId(request_id)},
        {"$set": {"status": status}}
    )
    
    if status == "Accepted":
        task_id = req.get("taskId")
        stage = req.get("stage")
        receiver_id = req.get("receiverId")
        
        if req.get("taskType") == "content-calendar":
            if ObjectId.is_valid(task_id):
                update_field = None
                if stage == "Script":
                    update_field = "assignedScriptwriterId"
                elif stage == "Shoot":
                    update_field = "assignedShooterId"
                elif stage == "Caption":
                    update_field = "assignedCaptionWriterId"
                elif stage == "Thumbnail":
                    update_field = "assignedThumbnailDesignerId"
                elif stage == "Editing":
                    entry = await db.content_calendar_entries.find_one({"_id": ObjectId(task_id)})
                    if entry and entry.get("postReel") == "Post":
                        update_field = "assignedPostDesignerId"
                    else:
                        update_field = "assignedReelEditorId"
                elif stage == "Post/Graphics":
                    update_field = "assignedPostDesignerId"
                elif stage == "Approval":
                    update_field = "assignedApproverId"
                elif stage == "Posting":
                    update_field = "assignedPosterId"
                elif stage == "Brand Person":
                    entry = await db.content_calendar_entries.find_one({"_id": ObjectId(task_id)})
                    if entry:
                        bp_ids = entry.get("assignedBrandPersonIds", [])
                        sender_id = req.get("senderId")
                        if sender_id in bp_ids:
                            bp_ids = [receiver_id if x == sender_id else x for x in bp_ids]
                        else:
                            if receiver_id not in bp_ids:
                                bp_ids.append(receiver_id)
                        logs = entry.get("logs", [])
                        logs.append({
                            "timestamp": datetime.now(IST).isoformat(),
                            "action": "Task Transferred",
                            "details": f"Stage '{stage}' transferred from {req.get('senderName')} to {req.get('receiverName')}.",
                            "userName": "System"
                        })
                        await db.content_calendar_entries.update_one(
                            {"_id": ObjectId(task_id)},
                            {"$set": {"assignedBrandPersonIds": bp_ids, "logs": logs}}
                        )
                
                if update_field:
                    entry = await db.content_calendar_entries.find_one({"_id": ObjectId(task_id)})
                    logs = entry.get("logs", []) if entry else []
                    logs.append({
                        "timestamp": datetime.now(IST).isoformat(),
                        "action": "Task Transferred",
                        "details": f"Stage '{stage}' transferred from {req.get('senderName')} to {req.get('receiverName')}.",
                        "userName": "System"
                    })
                    await db.content_calendar_entries.update_one(
                        {"_id": ObjectId(task_id)},
                        {"$set": {update_field: receiver_id, "logs": logs}}
                    )
        elif req.get("taskType") == "other-work":
            if ObjectId.is_valid(task_id):
                entry = await db.other_work.find_one({"_id": ObjectId(task_id)})
                logs = entry.get("logs", []) if entry else []
                logs.append({
                    "timestamp": datetime.now(IST).isoformat(),
                    "action": "Task Transferred",
                    "details": f"Task transferred from {req.get('senderName')} to {req.get('receiverName')}.",
                    "userName": "System"
                })
                await db.other_work.update_one(
                    {"_id": ObjectId(task_id)},
                    {"$set": {"assigneeId": receiver_id, "logs": logs}}
                )
        elif req.get("taskType") == "digital-marketing":
            if ObjectId.is_valid(task_id):
                await db.projects.update_one(
                    {"_id": ObjectId(task_id)},
                    {"$set": {
                        "assignedEmployeeId": receiver_id,
                        "assignedEmployeeName": req.get("receiverName")
                    }}
                )
                await log_activity(
                    db=db,
                    action="Task Transferred",
                    performedBy="System",
                    userName="System",
                    details=f"Digital Marketing work for Client/Project '{req.get('taskName')}' on date {req.get('stage')} transferred from {req.get('senderName')} to {req.get('receiverName')}.",
                    projectId=task_id
                )
        elif req.get("taskType") in ["wm-task", "wm-tasks"]:
            if ObjectId.is_valid(task_id):
                old_task = await db.wm_tasks.find_one({"_id": ObjectId(task_id)})
                if old_task:
                    await db.wm_tasks.update_one(
                        {"_id": ObjectId(task_id)},
                        {"$set": {
                            "assignedToId": receiver_id,
                            "assignedToName": req.get("receiverName")
                        }}
                    )
                    try:
                        await log_task_activity(
                            db, 
                            task_id, 
                            "Updated", 
                            "System", 
                            "System", 
                            f"Task assigned to {req.get('receiverName')} via transfer from {req.get('senderName')}.",
                            diffs=[{
                                "field": "assignedToName",
                                "old": old_task.get("assignedToName"),
                                "new": req.get("receiverName")
                            }]
                        )
                    except Exception as e_log:
                        print(f"Error logging task activity for wm-task transfer: {e_log}")

    # Create and broadcast notification to sender
    try:
        await create_notification(db, schemas.NotificationCreate(
            employee_id=req["senderId"],
            title=f"Task Transfer {status}",
            message=f"{req['receiverName']} has {status.lower()} your request to transfer task '{req['taskName']}' ({req['stage']}).",
            type="work_transfer",
            reference_id=request_id
        ))
    except Exception as e:
        print(f"Error creating transfer response notification: {e}")
                
    updated = await db.work_transfer_requests.find_one({"_id": ObjectId(request_id)})
    return fix_id(updated)

# --- Gallery CRUD ---
async def get_galleries(db, skip: int = 0, limit: int = 100):
    return await get_items(db, "gallery", skip, limit)

async def create_gallery(db, gallery: schemas.GalleryCreate):
    return await create_item(db, "gallery", gallery.dict())

async def update_gallery(db, gallery_id: str, update: dict):
    return await update_item(db, "gallery", gallery_id, update)

async def delete_gallery(db, gallery_id: str):
    return await delete_item(db, "gallery", gallery_id)

# --- Company Finance CRUD ---
async def get_next_expense_number(db, date_str: str = None):
    try:
        if date_str:
            try:
                # Support "YYYY-MM-DD" or similar ISO strings
                dt = datetime.strptime(date_str.split("T")[0], "%Y-%m-%d")
                prefix = dt.strftime("%y%m")
            except Exception:
                now = datetime.now(pytz.timezone('Asia/Kolkata'))
                prefix = now.strftime("%y%m")
        else:
            now = datetime.now(pytz.timezone('Asia/Kolkata'))
            prefix = now.strftime("%y%m")
        
        # Find highest expenseNo starting with this prefix
        cursor = db.company_finance_transactions.find({"expenseNo": {"$regex": f"^{prefix}"}})
        max_seq = 0
        async for doc in cursor:
            exp_no = str(doc.get("expenseNo", ""))
            if exp_no.startswith(prefix) and len(exp_no) >= len(prefix) + 1:
                try:
                    seq = int(exp_no[len(prefix):])
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    pass
        
        next_seq = max_seq + 1
        return f"{prefix}{next_seq:03d}"
    except Exception as e:
        print(f"Error generating expense number: {e}")
        import random
        now = datetime.now(pytz.timezone('Asia/Kolkata'))
        prefix = now.strftime("%y%m")
        return f"{prefix}{random.randint(100, 999)}"

async def get_finance_transactions(db, payment_method: str = None, type_filter: str = None, search: str = None):
    transactions = []
    
    # 1. Fetch manual transactions from company_finance_transactions
    query = {}
    if payment_method:
        query["paymentMethod"] = payment_method.lower()
    if type_filter:
        query["type"] = type_filter.lower()
        
    cursor = db.company_finance_transactions.find(query)
    async for doc in cursor:
        tx = fix_id(doc)
        transactions.append(tx)
        
    # 2. Auto-sync Invoices into Credit Transactions (if type_filter is None or 'credit')
    if not type_filter or type_filter.lower() == "credit":
        try:
            inv_cursor = db.invoices.find({})
            async for inv in inv_cursor:
                # Skip Proforma invoices and hidden invoices
                if inv.get("invoiceType") == "Proforma Invoice" or inv.get("hiddenFromFinance"):
                    continue
                    
                inv_id = str(inv.get("_id") or inv.get("id", ""))
                if not inv_id:
                    continue
                    
                # Determine payment method
                pm_str = str(inv.get("paymentMode", "")).lower()
                inv_payment_method = "cash" if "cash" in pm_str else "bank"
                
                if payment_method and inv_payment_method != payment_method.lower():
                    continue
                    
                # Extract descriptions & services from line items
                line_items = inv.get("lineItems", [])
                descs = [li.get("description", "") for li in line_items if li.get("description")]
                descriptions = ", ".join(descs) if descs else inv.get("clientName", "Invoice")
                
                srvs = [li.get("subDescription", "") or li.get("description", "") for li in line_items if li.get("subDescription") or li.get("description")]
                services = ", ".join(srvs) if srvs else "Services"
                
                synced_tx = {
                    "id": f"inv_{inv_id}",
                    "_id": f"inv_{inv_id}",
                    "description": inv.get("clientName", "General Client"),
                    "descriptions": inv.get("clientName", "General Client"),
                    "amount": float(inv.get("total", 0.0) or 0.0),
                    "type": "credit",
                    "category": inv.get("clientDepartment") or inv.get("category") or "General",
                    "paymentMethod": inv_payment_method,
                    "date": inv.get("issueDate", "") or inv.get("timestamp", ""),
                    "invoiceNumber": inv.get("invoiceNumber", f"INV-{inv_id[:6]}"),
                    "services": services,
                    "remarks": inv.get("notes", "") or inv.get("remarks", "") or f"Status: {inv.get('status', 'Pending')}",
                    "isSyncedInvoice": True,
                    "invoiceId": inv_id
                }
                transactions.append(synced_tx)
        except Exception as e:
            print(f"Error syncing invoices into finance transactions: {e}")
            
    # 3. Apply search filter in memory if provided
    if search:
        s_lower = search.lower()
        filtered = []
        for tx in transactions:
            if (
                s_lower in str(tx.get("description", "")).lower() or
                s_lower in str(tx.get("descriptions", "")).lower() or
                s_lower in str(tx.get("category", "")).lower() or
                s_lower in str(tx.get("invoiceNumber", "")).lower() or
                s_lower in str(tx.get("expenseNo", "")).lower() or
                s_lower in str(tx.get("things", "")).lower() or
                s_lower in str(tx.get("narrative", "")).lower() or
                s_lower in str(tx.get("remarks", "")).lower()
            ):
                filtered.append(tx)
        transactions = filtered
        
    # Sort by date / id (newest first)
    transactions.sort(key=lambda x: str(x.get("date") or x.get("id") or ""), reverse=True)
    return transactions

async def resequence_all_expenses(db):
    try:
        cursor = db.company_finance_transactions.find({"type": "debit"})
        all_debits = []
        async for tx in cursor:
            all_debits.append(tx)
            
        from collections import defaultdict
        grouped = defaultdict(list)
        
        for tx in all_debits:
            tx_date = tx.get("date")
            if tx_date:
                try:
                    dt = datetime.strptime(tx_date.split("T")[0], "%Y-%m-%d")
                    prefix = dt.strftime("%y%m")
                    grouped[prefix].append(tx)
                except Exception:
                    pass
                    
        for prefix, txs in grouped.items():
            def sort_key(t):
                d = t.get("date") or ""
                return (d, str(t.get("_id")))
                
            txs.sort(key=sort_key)
            
            for idx, tx in enumerate(txs):
                new_exp_no = f"{prefix}{idx + 1:03d}"
                if tx.get("expenseNo") != new_exp_no:
                    await db.company_finance_transactions.update_one(
                        {"_id": tx["_id"]},
                        {"$set": {"expenseNo": new_exp_no}}
                    )
    except Exception as e:
        print(f"Error during resequencing: {e}")

async def create_finance_transaction(db, tx_data: dict, current_user=None):
    if not tx_data.get("date"):
        now = datetime.now(pytz.timezone('Asia/Kolkata'))
        tx_data["date"] = now.strftime("%Y-%m-%d")

    if tx_data.get("type") == "debit" and (not tx_data.get("expenseNo") or str(tx_data.get("expenseNo")).strip() == ""):
        tx_data["expenseNo"] = await get_next_expense_number(db, tx_data.get("date"))
        
    result = await db.company_finance_transactions.insert_one(tx_data)
    if tx_data.get("type") == "debit":
        await resequence_all_expenses(db)
    created = await db.company_finance_transactions.find_one({"_id": result.inserted_id})
    
    # Log activity
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    tx_type = tx_data.get("type", "transaction").capitalize()
    details = f"{tx_type} transaction created: {tx_data.get('category', 'General')} - {tx_data.get('description', '')} (Amount: {tx_data.get('amount')}, Method: {tx_data.get('paymentMethod')})"
    await log_activity(db, "Finance Transaction Created", performed_by_id, performed_by_name, details)
    
    return fix_id(created)

async def update_finance_transaction(db, tx_id: str, update_data: dict, current_user=None):
    # Remove None values
    clean_update = {k: v for k, v in update_data.items() if v is not None}
    if not clean_update:
        return await get_finance_transaction_by_id(db, tx_id)
        
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    
    if tx_id.startswith("inv_"):
        # If updating a synced invoice, update the underlying invoice notes/remarks/clientName
        inv_id = tx_id[4:]
        existing = await get_finance_transaction_by_id(db, tx_id)
        
        inv_update = {}
        if "category" in clean_update:
            inv_update["clientName"] = clean_update["category"]
        if "remarks" in clean_update:
            inv_update["notes"] = clean_update["remarks"]
        if "amount" in clean_update:
            inv_update["total"] = float(clean_update["amount"])
            
        if inv_update:
            try:
                await db.invoices.update_one({"_id": ObjectId(inv_id)}, {"$set": inv_update})
                if existing:
                    log_details, diffs = format_field_changes(existing, clean_update, f"Synced Invoice Transaction '{tx_id}'")
                    await log_activity(db, "Finance Transaction Updated", performed_by_id, performed_by_name, log_details, diffs=diffs)
            except Exception as e:
                print(f"Error updating synced invoice: {e}")
        return await get_finance_transaction_by_id(db, tx_id)
    else:
        try:
            existing = await db.company_finance_transactions.find_one({"_id": ObjectId(tx_id)})
            await db.company_finance_transactions.update_one({"_id": ObjectId(tx_id)}, {"$set": clean_update})
            await resequence_all_expenses(db)
            updated = await db.company_finance_transactions.find_one({"_id": ObjectId(tx_id)})
            
            if existing:
                log_details, diffs = format_field_changes(existing, clean_update, f"Finance Transaction")
                await log_activity(db, "Finance Transaction Updated", performed_by_id, performed_by_name, log_details, diffs=diffs)
                
            return fix_id(updated)
        except Exception as e:
            print(f"Error updating transaction {tx_id}: {e}")
            return None

async def get_finance_transaction_by_id(db, tx_id: str):
    if tx_id.startswith("inv_"):
        inv_id = tx_id[4:]
        try:
            inv = await db.invoices.find_one({"_id": ObjectId(inv_id)})
            if inv:
                line_items = inv.get("lineItems", [])
                descs = [li.get("description", "") for li in line_items if li.get("description")]
                descriptions = ", ".join(descs) if descs else inv.get("clientName", "Invoice")
                srvs = [li.get("subDescription", "") or li.get("description", "") for li in line_items if li.get("subDescription") or li.get("description")]
                services = ", ".join(srvs) if srvs else "Services"
                return {
                    "id": tx_id,
                    "_id": tx_id,
                    "description": descriptions,
                    "descriptions": descriptions,
                    "amount": float(inv.get("total", 0.0) or 0.0),
                    "type": "credit",
                    "category": inv.get("clientName", "General"),
                    "paymentMethod": "cash" if "cash" in str(inv.get("paymentMode", "")).lower() else "bank",
                    "date": inv.get("issueDate", "") or inv.get("timestamp", ""),
                    "invoiceNumber": inv.get("invoiceNumber", f"INV-{inv_id[:6]}"),
                    "services": services,
                    "remarks": inv.get("notes", "") or inv.get("remarks", "") or f"Status: {inv.get('status', 'Pending')}",
                    "isSyncedInvoice": True,
                    "invoiceId": inv_id
                }
        except Exception as e:
            print(f"Error fetching synced invoice by id: {e}")
        return None
    else:
        try:
            doc = await db.company_finance_transactions.find_one({"_id": ObjectId(tx_id)})
            return fix_id(doc) if doc else None
        except Exception:
            return None

async def delete_finance_transaction(db, tx_id: str, current_user=None):
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    if tx_id.startswith("inv_"):
        inv_id = tx_id[4:]
        try:
            existing = await get_finance_transaction_by_id(db, tx_id)
            res = await db.invoices.update_one({"_id": ObjectId(inv_id)}, {"$set": {"hiddenFromFinance": True}})
            if res.modified_count > 0 and existing:
                details = f"Removed synced invoice transaction from finance: {existing.get('category')} - {existing.get('description')} (Amount: {existing.get('amount')})"
                await log_activity(db, "Finance Transaction Deleted", performed_by_id, performed_by_name, details)
            return res.modified_count > 0
        except Exception as e:
            print(f"Error deleting synced invoice: {e}")
            return False
    else:
        try:
            tx = await db.company_finance_transactions.find_one({"_id": ObjectId(tx_id)})
            res = await db.company_finance_transactions.delete_one({"_id": ObjectId(tx_id)})
            if res.deleted_count > 0:
                if tx and tx.get("type") == "debit":
                    await resequence_all_expenses(db)
                if tx:
                    tx_type = tx.get("type", "transaction").capitalize()
                    details = f"Deleted {tx_type.lower()} transaction: {tx.get('category')} - {tx.get('description')} (Amount: {tx.get('amount')})"
                    await log_activity(db, "Finance Transaction Deleted", performed_by_id, performed_by_name, details)
                return True
            return False
        except Exception:
            return False

async def get_finance_balances(db):
    doc = await db.company_finance_balances.find_one({"year": "Global"})
    if not doc:
        default_bal = {"bankOpeningBalance": 0.0, "cashOpeningBalance": 0.0, "year": "Global"}
        await db.company_finance_balances.insert_one(default_bal)
        doc = await db.company_finance_balances.find_one({"year": "Global"})
    return fix_id(doc)

async def update_finance_balances(db, balance_data: dict, current_user=None):
    existing = await db.company_finance_balances.find_one({"year": "Global"})
    clean_data = {k: float(v) if k in ["bankOpeningBalance", "cashOpeningBalance"] else v for k, v in balance_data.items() if v is not None}
    await db.company_finance_balances.update_one(
        {"year": "Global"},
        {"$set": clean_data},
        upsert=True
    )
    doc = await db.company_finance_balances.find_one({"year": "Global"})
    
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    log_details, diffs = format_field_changes(existing, clean_data, f"Finance Balances")
    await log_activity(db, "Finance Balances Updated", performed_by_id, performed_by_name, log_details, diffs=diffs)
    
    return fix_id(doc)

async def get_finance_plans(db):
    return await get_items(db, "company_finance_plans", 0, 1000)

async def create_finance_plan(db, plan_data: dict, current_user=None):
    created = await create_item(db, "company_finance_plans", plan_data)
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    details = f"Created finance plan for category '{plan_data.get('category')}' with amount {plan_data.get('amount')}"
    await log_activity(db, "Finance Plan Created", performed_by_id, performed_by_name, details)
    return created

async def update_finance_plan(db, plan_id: str, update_data: dict, current_user=None):
    existing = await db.company_finance_plans.find_one({"_id": ObjectId(plan_id) if len(plan_id) == 24 else plan_id})
    updated = await update_item(db, "company_finance_plans", plan_id, update_data)
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    if existing:
        log_details, diffs = format_field_changes(existing, update_data, f"Finance Plan")
        await log_activity(db, "Finance Plan Updated", performed_by_id, performed_by_name, log_details, diffs=diffs)
    return updated

async def delete_finance_plan(db, plan_id: str, current_user=None):
    existing = await db.company_finance_plans.find_one({"_id": ObjectId(plan_id) if len(plan_id) == 24 else plan_id})
    deleted = await delete_item(db, "company_finance_plans", plan_id)
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    if deleted and existing:
        details = f"Deleted finance plan for category '{existing.get('category')}' with amount {existing.get('amount')}"
        await log_activity(db, "Finance Plan Deleted", performed_by_id, performed_by_name, details)
    return deleted

async def get_monthly_plan(db, month: str):
    doc = await db.company_finance_monthly_plans.find_one({"month": month})
    if not doc:
        return {"month": month, "values": {}}
    return fix_id(doc)

async def save_monthly_plan(db, month: str, values: dict, current_user=None):
    existing = await db.company_finance_monthly_plans.find_one({"month": month})
    await db.company_finance_monthly_plans.update_one(
        {"month": month},
        {"$set": {"month": month, "values": values}},
        upsert=True
    )
    doc = await db.company_finance_monthly_plans.find_one({"month": month})
    
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    if existing:
        log_details, diffs = format_field_changes(existing, {"values": values}, f"Monthly Plan for {month}")
        await log_activity(db, "Finance Monthly Plan Saved", performed_by_id, performed_by_name, log_details, diffs=diffs)
    else:
        details = f"Saved initial monthly plan values for {month}"
        await log_activity(db, "Finance Monthly Plan Saved", performed_by_id, performed_by_name, details)
        
    return fix_id(doc)

async def get_finance_summary(db):
    all_txs = await get_finance_transactions(db)
    balances = await get_finance_balances(db)
    
    total_income = sum(float(tx.get("amount", 0.0) or 0.0) for tx in all_txs if tx.get("type") == "credit")
    total_expenses = sum(float(tx.get("amount", 0.0) or 0.0) for tx in all_txs if tx.get("type") == "debit")
    net_balance = total_income - total_expenses
    
    bank_opening = float(balances.get("bankOpeningBalance", 0.0) or 0.0)
    cash_opening = float(balances.get("cashOpeningBalance", 0.0) or 0.0)
    
    bank_txs = [tx for tx in all_txs if tx.get("paymentMethod", "").lower() == "bank"]
    cash_txs = [tx for tx in all_txs if tx.get("paymentMethod", "").lower() == "cash"]
    
    bank_credit = sum(float(tx.get("amount", 0.0) or 0.0) for tx in bank_txs if tx.get("type") == "credit")
    bank_debit = sum(float(tx.get("amount", 0.0) or 0.0) for tx in bank_txs if tx.get("type") == "debit")
    bank_closing = bank_opening + bank_credit - bank_debit
    
    cash_credit = sum(float(tx.get("amount", 0.0) or 0.0) for tx in cash_txs if tx.get("type") == "credit")
    cash_debit = sum(float(tx.get("amount", 0.0) or 0.0) for tx in cash_txs if tx.get("type") == "debit")
    cash_in_hand = cash_opening + cash_credit - cash_debit
    
    # Calculate pending payments from invoices
    pending_payments = 0.0
    try:
        inv_cursor = db.invoices.find({"status": {"$in": ["Pending", "Overdue"]}})
        async for inv in inv_cursor:
            if inv.get("invoiceType") != "Proforma Invoice":
                pending_payments += float(inv.get("total", 0.0) or 0.0)
    except Exception as e:
        print(f"Error calculating pending payments: {e}")
        
    return {
        "totalIncome": total_income,
        "totalExpenses": total_expenses,
        "netBalance": net_balance,
        "pendingPayments": pending_payments,
        "bankOpeningBalance": bank_opening,
        "bankClosingBalance": bank_closing,
        "cashOpeningBalance": cash_opening,
        "cashInHand": cash_in_hand,
        "incomeTrend": 12.5,
        "expenseTrend": -4.2
    }

# --- Task Presets CRUD ---
async def get_task_presets(db, skip: int = 0, limit: int = 100):
    return await get_items(db, "task_presets", skip, limit)

async def create_task_preset(db, preset: schemas.TaskPresetCreate):
    return await create_item(db, "task_presets", preset.dict())

async def update_task_preset(db, preset_id: str, update: dict):
    return await update_item(db, "task_presets", preset_id, update)

async def delete_task_preset(db, preset_id: str):
    return await delete_item(db, "task_presets", preset_id)

# --- Research ---
async def create_research(db, data: dict):
    now = get_now()
    data["createdAt"] = now
    data["logs"] = [{
        "action": "Created",
        "byUserId": data.get("createdBy", "Unknown"),
        "byUserName": data.get("createdByName", "Unknown"),
        "timestamp": now
    }]
    result = await db.research.insert_one(data)
    created = await db.research.find_one({"_id": result.inserted_id})
    return fix_id(created)

async def get_research(db, user_id: str, is_admin: bool):
    if is_admin:
        cursor = db.research.find().sort("createdAt", -1)
    else:
        cursor = db.research.find({
            "$or": [
                {"createdBy": user_id},
                {"sharedWith": user_id}
            ]
        }).sort("createdAt", -1)
    
    research_list = await cursor.to_list(length=None)
    return [fix_id(r) for r in research_list]

async def update_research(db, research_id: str, data: dict):
    from bson import ObjectId
    if not data:
        updated = await db.research.find_one({"_id": ObjectId(research_id)})
        return fix_id(updated) if updated else None
    
    updatedBy = data.pop("updatedBy", "Unknown")
    updatedByName = data.pop("updatedByName", "Unknown")
    
    log_entry = {
        "action": "Updated",
        "byUserId": updatedBy,
        "byUserName": updatedByName,
        "timestamp": get_now()
    }
    
    await db.research.update_one(
        {"_id": ObjectId(research_id)}, 
        {"$set": data, "$push": {"logs": log_entry}}
    )
    updated = await db.research.find_one({"_id": ObjectId(research_id)})
    return fix_id(updated) if updated else None

async def delete_research(db, research_id: str):
    from bson import ObjectId
    result = await db.research.delete_one({"_id": ObjectId(research_id)})
    return result.deleted_count > 0

# --- Client Transactions ---
async def get_client_transactions(db):
    cursor = db.client_transactions.find().sort("date", -1)
    rows = await cursor.to_list(length=None)
    return [fix_id(row) for row in rows]

async def create_client_transaction(db, tx_data: dict):
    result = await db.client_transactions.insert_one(tx_data)
    created = await db.client_transactions.find_one({"_id": result.inserted_id})
    return fix_id(created)

async def update_client_transaction(db, tx_id: str, update_data: dict):
    from bson import ObjectId
    if not update_data:
        updated = await db.client_transactions.find_one({"_id": ObjectId(tx_id)})
        return fix_id(updated) if updated else None
    
    await db.client_transactions.update_one(
         {"_id": ObjectId(tx_id)},
         {"$set": update_data}
    )
    updated = await db.client_transactions.find_one({"_id": ObjectId(tx_id)})
    return fix_id(updated) if updated else None

async def delete_client_transaction(db, tx_id: str):
    from bson import ObjectId
    result = await db.client_transactions.delete_one({"_id": ObjectId(tx_id)})
    return result.deleted_count > 0


# --- Row Definitions ---
async def get_row_definitions(db, month: str):
    doc = await db.company_finance_row_definitions.find_one({"month": month})
    if not doc:
        global_doc = await db.company_finance_row_definitions.find_one({"id": "config"})
        if global_doc:
            return fix_id(global_doc)
        return {"month": month, "rows": []}
    return fix_id(doc)

async def save_row_definitions(db, month: str, rows: list, current_user=None):
    existing = await db.company_finance_row_definitions.find_one({"month": month})
    await db.company_finance_row_definitions.update_one(
        {"month": month},
        {"$set": {"month": month, "rows": rows}},
        upsert=True
    )
    # Update global config so new months start with the latest configuration
    await db.company_finance_row_definitions.update_one(
        {"id": "config"},
        {"$set": {"id": "config", "rows": rows}},
        upsert=True
    )
    doc = await db.company_finance_row_definitions.find_one({"month": month})
    
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    if existing:
        log_details, diffs = format_field_changes(existing, {"rows": rows}, f"Row Definitions for {month}")
        await log_activity(db, "Finance Row Definitions Saved", performed_by_id, performed_by_name, log_details, diffs=diffs)
    else:
        details = f"Saved initial row definitions for {month}"
        await log_activity(db, "Finance Row Definitions Saved", performed_by_id, performed_by_name, details)
        
    return fix_id(doc)


# --- Summary Actual Overrides ---
async def get_summary_actual_overrides(db, month: str):
    doc = await db.company_finance_actual_overrides.find_one({"month": month})
    if not doc:
        return {"month": month, "values": {}}
    return fix_id(doc)

async def save_summary_actual_overrides(db, month: str, values: dict, current_user=None):
    existing = await db.company_finance_actual_overrides.find_one({"month": month})
    await db.company_finance_actual_overrides.update_one(
        {"month": month},
        {"$set": {"month": month, "values": values}},
        upsert=True
    )
    doc = await db.company_finance_actual_overrides.find_one({"month": month})
    
    performed_by_id, performed_by_name = await resolve_performer(db, current_user)
    if existing:
        log_details, diffs = format_field_changes(existing, {"values": values}, f"Actual Overrides for {month}")
        await log_activity(db, "Finance Actual Overrides Saved", performed_by_id, performed_by_name, log_details, diffs=diffs)
    else:
        details = f"Saved initial actual overrides for {month}"
        await log_activity(db, "Finance Actual Overrides Saved", performed_by_id, performed_by_name, details)
        
    return fix_id(doc)


# --- Courses CRUD ---
async def create_course(db, course: schemas.CourseCreate):
    course_dict = course.dict()
    course_dict["createdAt"] = get_now()
    course_dict["updatedAt"] = get_now()
    result = await db.courses.insert_one(course_dict)
    doc = await db.courses.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def get_courses(db):
    cursor = db.courses.find().sort("createdAt", -1)
    docs = await cursor.to_list(length=None)
    return [fix_id(d) for d in docs]

async def get_course(db, course_id: str):
    from bson import ObjectId
    doc = await db.courses.find_one({"_id": ObjectId(course_id)})
    return fix_id(doc)

async def update_course(db, course_id: str, course_update: schemas.CourseUpdate):
    from bson import ObjectId
    update_data = course_update.dict(exclude_unset=True)
    if not update_data:
        return await get_course(db, course_id)
    update_data["updatedAt"] = get_now()
    await db.courses.update_one({"_id": ObjectId(course_id)}, {"$set": update_data})
    return await get_course(db, course_id)

async def delete_course(db, course_id: str):
    from bson import ObjectId
    # Cascade delete modules and lectures
    modules = await db.course_modules.find({"course_id": course_id}).to_list(length=None)
    for module in modules:
        await db.course_lectures.delete_many({"module_id": str(module["_id"])})
    await db.course_modules.delete_many({"course_id": course_id})
    result = await db.courses.delete_one({"_id": ObjectId(course_id)})
    return result.deleted_count > 0

# --- Course Modules CRUD ---
async def create_course_module(db, module: schemas.CourseModuleCreate):
    module_dict = module.dict()
    module_dict["createdAt"] = get_now()
    module_dict["updatedAt"] = get_now()
    result = await db.course_modules.insert_one(module_dict)
    doc = await db.course_modules.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def get_course_modules(db, course_id: str):
    cursor = db.course_modules.find({"course_id": course_id}).sort("order", 1)
    docs = await cursor.to_list(length=None)
    return [fix_id(d) for d in docs]

async def update_course_module(db, module_id: str, module_update: schemas.CourseModuleUpdate):
    from bson import ObjectId
    update_data = module_update.dict(exclude_unset=True)
    if update_data:
        update_data["updatedAt"] = get_now()
        await db.course_modules.update_one({"_id": ObjectId(module_id)}, {"$set": update_data})
    doc = await db.course_modules.find_one({"_id": ObjectId(module_id)})
    return fix_id(doc)

async def delete_course_module(db, module_id: str):
    from bson import ObjectId
    # Cascade delete lectures
    await db.course_lectures.delete_many({"module_id": module_id})
    result = await db.course_modules.delete_one({"_id": ObjectId(module_id)})
    return result.deleted_count > 0

# --- Course Lectures CRUD ---
async def create_course_lecture(db, lecture: schemas.CourseLectureCreate):
    lecture_dict = lecture.dict()
    lecture_dict["createdAt"] = get_now()
    lecture_dict["updatedAt"] = get_now()
    result = await db.course_lectures.insert_one(lecture_dict)
    doc = await db.course_lectures.find_one({"_id": result.inserted_id})
    return fix_id(doc)

async def get_course_lectures(db, module_id: str):
    cursor = db.course_lectures.find({"module_id": module_id}).sort("order", 1)
    docs = await cursor.to_list(length=None)
    return [fix_id(d) for d in docs]

async def update_course_lecture(db, lecture_id: str, lecture_update: schemas.CourseLectureUpdate):
    from bson import ObjectId
    update_data = lecture_update.dict(exclude_unset=True)
    if update_data:
        update_data["updatedAt"] = get_now()
        await db.course_lectures.update_one({"_id": ObjectId(lecture_id)}, {"$set": update_data})
    doc = await db.course_lectures.find_one({"_id": ObjectId(lecture_id)})
    return fix_id(doc)

async def delete_course_lecture(db, lecture_id: str):
    from bson import ObjectId
    result = await db.course_lectures.delete_one({"_id": ObjectId(lecture_id)})
    return result.deleted_count > 0

# --- Course Progress CRUD ---
async def update_lecture_progress(db, progress: schemas.LectureProgressBase):
    progress_dict = progress.dict()
    progress_dict["last_watched_at"] = get_now()
    
    # Find existing
    existing = await db.lecture_progress.find_one({
        "employee_id": progress.employee_id,
        "lecture_id": progress.lecture_id
    })
    
    if existing:
        # Update
        if progress.is_completed:
            update_data = {"watched_seconds": progress.watched_seconds, "is_completed": True, "last_watched_at": get_now()}
        else:
            update_data = {"watched_seconds": progress.watched_seconds, "last_watched_at": get_now()}
            
        await db.lecture_progress.update_one(
            {"_id": existing["_id"]},
            {"$set": update_data}
        )
        doc = await db.lecture_progress.find_one({"_id": existing["_id"]})
    else:
        # Create
        result = await db.lecture_progress.insert_one(progress_dict)
        doc = await db.lecture_progress.find_one({"_id": result.inserted_id})
        
    return fix_id(doc)

async def get_course_progress(db, course_id: str, employee_id: str):
    cursor = db.lecture_progress.find({"course_id": course_id, "employee_id": employee_id})
    docs = await cursor.to_list(length=None)
    return [fix_id(d) for d in docs]

async def get_all_course_progress(db, course_id: str):
    cursor = db.lecture_progress.find({"course_id": course_id})
    docs = await cursor.to_list(length=None)
    return [fix_id(d) for d in docs]



async def get_all_user_permissions(db):
    cursor = db.user_permissions.find({})
    perms = await cursor.to_list(length=2000)
    return [fix_id(p) for p in perms]

async def bulk_update_module_permissions(db, request: schemas.ModuleBulkUpdateRequest, performed_by: str = "System", user_name: str = "System User"):
    module_name = request.moduleName
    updates = request.updates
    
    for emp_update in updates:
        emp_id = emp_update.employeeId
        existing_doc = await db.user_permissions.find_one({"employeeId": emp_id})
        if not existing_doc:
            new_doc = {
                "employeeId": emp_id,
                "presetId": None,
                "permissions": [{
                    "moduleName": module_name,
                    "displayName": request.displayName,
                    "tabUrl": request.tabUrl,
                    "canAdd": emp_update.canAdd,
                    "canEdit": emp_update.canEdit,
                    "canDelete": emp_update.canDelete,
                    "canView": emp_update.canView
                }]
            }
            await db.user_permissions.insert_one(new_doc)
        else:
            perms = existing_doc.get("permissions", [])
            mod_found = False
            for m in perms:
                if m.get("moduleName") == module_name:
                    m["canAdd"] = emp_update.canAdd
                    m["canEdit"] = emp_update.canEdit
                    m["canDelete"] = emp_update.canDelete
                    m["canView"] = emp_update.canView
                    m["displayName"] = request.displayName
                    m["tabUrl"] = request.tabUrl
                    mod_found = True
                    break
            if not mod_found:
                perms.append({
                    "moduleName": module_name,
                    "displayName": request.displayName,
                    "tabUrl": request.tabUrl,
                    "canAdd": emp_update.canAdd,
                    "canEdit": emp_update.canEdit,
                    "canDelete": emp_update.canDelete,
                    "canView": emp_update.canView
                })
            
            await db.user_permissions.update_one(
                {"employeeId": emp_id},
                {"$set": {"permissions": perms, "presetId": None}}
            )
    return {"message": "Bulk permissions updated successfully"}


# ==========================================
# STV VOTING SYSTEM MODULE CRUD & ALGORITHM
# ==========================================

from database import db


def calculate_quota(total_valid_votes: int) -> int:

    """
    Dynamic Quota Calculation (PDF Spec Feature 1):
    Formula: ⌊Total Valid Votes / 2⌋ + 1
    Calculated strictly based on submitted valid ballots.
    """
    if total_valid_votes <= 0:
        return 0
    return (total_valid_votes // 2) + 1


async def create_election(data: schemas.ElectionCreate, created_by_id: str):
    """
    Create a new election with candidates.
    Supports electionMonth & electionYear tagging (Req 6.8 Backdated Tagging).
    """
    now = datetime.now()
    election_doc = {
        "title": data.title,
        "description": data.description,
        "maxPreferences": data.maxPreferences,
        "electionMonth": data.electionMonth or now.strftime("%B"),
        "electionYear": data.electionYear or now.year,
        "status": data.status or "active",
        "isDeleted": False,
        "created_by": str(created_by_id),
        "created_at": now,
        "updated_at": now
    }
    result = await db.elections.insert_one(election_doc)
    election_id = str(result.inserted_id)

    # Insert candidates if employee IDs were provided
    candidates_list = []
    if data.candidate_employee_ids:
        for emp_id in data.candidate_employee_ids:
            # Lookup employee details
            emp = await db.employees.find_one({"_id": emp_id})
            if not emp:
                emp = await db.users.find_one({"_id": emp_id})
            
            emp_name = "Unknown Employee"
            dept = None
            desig = None
            avatar = None

            if emp:
                emp_name = emp.get("name") or emp.get("fullName") or f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip() or "Employee"
                dept = emp.get("department") or emp.get("departmentName")
                desig = emp.get("designation") or emp.get("designationTitle") or emp.get("role")
                avatar = emp.get("profilePicture") or emp.get("avatar")

            cand_doc = {
                "electionId": election_id,
                "employee_id": str(emp_id),
                "name": emp_name,
                "department": dept,
                "designation": desig,
                "avatar": avatar,
                "created_at": now
            }
            c_res = await db.candidates.insert_one(cand_doc)
            cand_doc["id"] = str(c_res.inserted_id)
            candidates_list.append(cand_doc)

    election_doc["id"] = election_id
    election_doc["candidates"] = candidates_list
    return election_doc


async def get_elections(month: Optional[str] = None, year: Optional[int] = None):
    """
    List elections with optional month and year filters.
    Includes count of total valid votes cast and total eligible voters.
    """
    query = {"isDeleted": {"$ne": True}}
    if month:
        query["electionMonth"] = month
    if year:
        query["electionYear"] = year

    cursor = db.elections.find(query).sort("created_at", -1)
    elections = await cursor.to_list(length=500)

    # Count total active employees for total eligible count
    total_eligible = await db.employees.count_documents({"status": {"$ne": "inactive"}})
    if total_eligible == 0:
        total_eligible = await db.users.count_documents({})

    result = []
    for el in elections:
        el_id = str(el["_id"])
        c_cursor = db.candidates.find({"electionId": el_id})
        cands = await c_cursor.to_list(length=100)
        formatted_cands = []
        for c in cands:
            formatted_cands.append({
                "id": str(c["_id"]),
                "employee_id": str(c.get("employee_id", "")),
                "name": c.get("name", "Unknown"),
                "department": c.get("department"),
                "designation": c.get("designation"),
                "avatar": c.get("avatar")
            })

        valid_votes = await db.ballots.count_documents({"electionId": el_id, "isSubmitted": True})

        # Fetch winner name if winner candidate ID exists
        winner_name = None
        if el.get("winner_candidate_id"):
            w_cand = next((c for c in formatted_cands if c["id"] == str(el["winner_candidate_id"])), None)
            if w_cand:
                winner_name = w_cand["name"]

        result.append({
            "id": el_id,
            "title": el.get("title", ""),
            "description": el.get("description"),
            "maxPreferences": el.get("maxPreferences", 5),
            "electionMonth": el.get("electionMonth"),
            "electionYear": el.get("electionYear"),
            "status": el.get("status", "active"),
            "candidates": formatted_cands,
            "totalValidVotes": valid_votes,
            "totalEligibleVoters": total_eligible,
            "quota": el.get("quota"),
            "winner_candidate_id": el.get("winner_candidate_id"),
            "winner_name": winner_name,
            "createdAt": el.get("created_at"),
            "updatedAt": el.get("updated_at")
        })

    return result


async def get_election_by_id(election_id: str):
    """
    Get election details by ID.
    """
    el = await db.elections.find_one({"_id": election_id, "isDeleted": {"$ne": True}})
    if not el:
        return None

    c_cursor = db.candidates.find({"electionId": election_id})
    cands = await c_cursor.to_list(length=100)
    formatted_cands = []
    for c in cands:
        formatted_cands.append({
            "id": str(c["_id"]),
            "employee_id": str(c.get("employee_id", "")),
            "name": c.get("name", "Unknown"),
            "department": c.get("department"),
            "designation": c.get("designation"),
            "avatar": c.get("avatar")
        })

    valid_votes = await db.ballots.count_documents({"electionId": election_id, "isSubmitted": True})
    total_eligible = await db.employees.count_documents({"status": {"$ne": "inactive"}})

    winner_name = None
    if el.get("winner_candidate_id"):
        w_cand = next((c for c in formatted_cands if c["id"] == str(el["winner_candidate_id"])), None)
        if w_cand:
            winner_name = w_cand["name"]

    return {
        "id": election_id,
        "title": el.get("title", ""),
        "description": el.get("description"),
        "maxPreferences": el.get("maxPreferences", 5),
        "electionMonth": el.get("electionMonth"),
        "electionYear": el.get("electionYear"),
        "status": el.get("status", "active"),
        "candidates": formatted_cands,
        "totalValidVotes": valid_votes,
        "totalEligibleVoters": total_eligible,
        "quota": el.get("quota"),
        "winner_candidate_id": el.get("winner_candidate_id"),
        "winner_name": winner_name,
        "createdAt": el.get("created_at"),
        "updatedAt": el.get("updated_at")
    }


async def soft_delete_election(election_id: str):
    """
    Soft delete election (Req 6.10).
    """
    res = await db.elections.update_one(
        {"_id": election_id},
        {"$set": {"isDeleted": True, "deletedAt": datetime.now()}}
    )
    return res.modified_count > 0


async def submit_ballot(election_id: str, voter_id: str, preferences: List[str]):
    """
    Submit ranked preferences ballot.
    Validates maxPreferences limit and prevents modifying locked ballots (Req 6.5).
    """
    el = await db.elections.find_one({"_id": election_id, "isDeleted": {"$ne": True}})
    if not el:
        raise ValueError("Election not found")
    
    if el.get("status") == "completed":
        raise ValueError("Election is completed. Voting is closed.")

    max_pref = el.get("maxPreferences", 5)
    candidates_count = await db.candidates.count_documents({"electionId": election_id})
    required_count = min(max_pref, candidates_count) if candidates_count > 0 else max_pref
    if len(preferences) != required_count:
        raise ValueError(f"You must select exactly {required_count} candidate preferences.")

    existing_ballot = await db.ballots.find_one({"electionId": election_id, "voterId": str(voter_id)})
    if existing_ballot and existing_ballot.get("isSubmitted"):
        raise ValueError("Your vote has already been recorded and is locked.")

    now = datetime.now()
    ballot_doc = {
        "electionId": election_id,
        "voterId": str(voter_id),
        "preferences": [str(p) for p in preferences],
        "isSubmitted": True,
        "submittedAt": now,
        "created_at": now
    }

    if existing_ballot:
        await db.ballots.update_one(
            {"_id": existing_ballot["_id"]},
            {"$set": ballot_doc}
        )
        ballot_doc["id"] = str(existing_ballot["_id"])
    else:
        res = await db.ballots.insert_one(ballot_doc)
        ballot_doc["id"] = str(res.inserted_id)

    return ballot_doc


async def get_voter_ballot(election_id: str, voter_id: str):
    """
    Get user's submitted ballot status.
    """
    ballot = await db.ballots.find_one({"electionId": election_id, "voterId": str(voter_id)})
    if not ballot:
        return None
    return {
        "id": str(ballot["_id"]),
        "electionId": ballot.get("electionId"),
        "voterId": ballot.get("voterId"),
        "preferences": ballot.get("preferences", []),
        "isSubmitted": ballot.get("isSubmitted", False),
        "submittedAt": ballot.get("submittedAt")
    }


async def run_stv_round_calculation(election_id: str):
    """
    Core Single Transferable Vote (STV) Engine Implementation.
    Features implemented:
    - Feature 1: Dynamic Quota Calculation based strictly on submitted ballots
    - Feature 2: Priority Allocation Limit (maxPreferences)
    - Feature 3: STV Elimination & Vote Transfer
    - Feature 4: Batch Zero-Vote Elimination
    - Feature 6: Audit Trail & Round Snapshots
    - Req 6.6: Duplicate Candidate Preferences handling (skips duplicates during transfer)
    - Req 6.9: Round Auto-Stop immediately when winner is found
    """
    el = await db.elections.find_one({"_id": election_id, "isDeleted": {"$ne": True}})
    if not el:
        raise ValueError("Election not found")

    # Fetch candidates
    cand_cursor = db.candidates.find({"electionId": election_id})
    raw_candidates = await cand_cursor.to_list(length=200)
    if not raw_candidates:
        raise ValueError("No candidates configured for this election")

    cand_map = {str(c["_id"]): c.get("name", "Candidate") for c in raw_candidates}
    all_candidate_ids = set(cand_map.keys())

    # Fetch valid ballots
    ballot_cursor = db.ballots.find({"electionId": election_id, "isSubmitted": True})
    raw_ballots = await ballot_cursor.to_list(length=10000)

    total_valid_votes = len(raw_ballots)
    quota = calculate_quota(total_valid_votes)

    # Delete previous round records for clean rerun
    await db.election_rounds.delete_many({"electionId": election_id})

    # Prepare ballots list: sanitize candidate IDs and remove consecutive duplicates per ballot
    processed_ballots = []
    for b in raw_ballots:
        raw_prefs = [str(p) for p in b.get("preferences", []) if str(p) in all_candidate_ids]
        # Clean duplicates while preserving preference order (Req 6.6)
        unique_prefs = []
        for p in raw_prefs:
            if p not in unique_prefs:
                unique_prefs.append(p)
        if unique_prefs:
            processed_ballots.append(unique_prefs)

    active_candidate_ids = set(all_candidate_ids)
    winner_id = None
    round_number = 1
    rounds_summary = []

    while active_candidate_ids and not winner_id:
        now = datetime.now()
        # 1. Calculate vote tally for current active candidates
        tally = {cid: 0 for cid in all_candidate_ids}
        
        for prefs in processed_ballots:
            # Find 1st preference candidate who is still active
            first_active = next((p for p in prefs if p in active_candidate_ids), None)
            if first_active:
                tally[first_active] += 1

        # 2. Check Quota condition for any active candidate
        potential_winners = [cid for cid in active_candidate_ids if tally[cid] >= quota and quota > 0]

        if potential_winners:
            # Sort potential winners by highest vote count
            potential_winners.sort(key=lambda cid: tally[cid], reverse=True)
            winner_id = potential_winners[0]
            winner_name = cand_map.get(winner_id, "Winner")

            round_doc = {
                "electionId": election_id,
                "roundNumber": round_number,
                "tally": tally,
                "eliminatedCandidateIds": [],
                "transferredVotes": [],
                "winnerCandidateId": winner_id,
                "winnerName": winner_name,
                "quota": quota,
                "timestamp": now,
                "created_at": now
            }
            res = await db.election_rounds.insert_one(round_doc)
            round_doc["id"] = str(res.inserted_id)
            rounds_summary.append(round_doc)

            # Auto-Stop (Req 6.9): Immediately complete election & break loop
            await db.elections.update_one(
                {"_id": election_id},
                {
                    "$set": {
                        "status": "completed",
                        "winner_candidate_id": winner_id,
                        "quota": quota,
                        "updated_at": now
                    }
                }
            )
            break

        # If no winner and only 1 active candidate remains, declare them winner
        if len(active_candidate_ids) == 1:
            winner_id = list(active_candidate_ids)[0]
            winner_name = cand_map.get(winner_id, "Winner")

            round_doc = {
                "electionId": election_id,
                "roundNumber": round_number,
                "tally": tally,
                "eliminatedCandidateIds": [],
                "transferredVotes": [],
                "winnerCandidateId": winner_id,
                "winnerName": winner_name,
                "quota": quota,
                "timestamp": now,
                "created_at": now
            }
            res = await db.election_rounds.insert_one(round_doc)
            round_doc["id"] = str(res.inserted_id)
            rounds_summary.append(round_doc)

            await db.elections.update_one(
                {"_id": election_id},
                {
                    "$set": {
                        "status": "completed",
                        "winner_candidate_id": winner_id,
                        "quota": quota,
                        "updated_at": now
                    }
                }
            )
            break

        # 3. Elimination logic
        # Feature 4: Batch Zero-Vote Elimination
        zero_vote_candidates = [cid for cid in active_candidate_ids if tally[cid] == 0]

        eliminated_ids = []
        transferred_votes_log = []

        if len(zero_vote_candidates) > 0:
            # Batch eliminate all zero vote candidates in one round
            eliminated_ids = zero_vote_candidates
            for cid in zero_vote_candidates:
                active_candidate_ids.remove(cid)
        else:
            # Find candidate with lowest votes
            active_list = list(active_candidate_ids)
            active_list.sort(key=lambda cid: tally[cid])
            lowest_cand_id = active_list[0]
            eliminated_ids = [lowest_cand_id]
            active_candidate_ids.remove(lowest_cand_id)

            # Track vote transfers for ballots assigned to this eliminated candidate
            for prefs in processed_ballots:
                first_active = next((p for p in prefs if p in (active_candidate_ids | set(eliminated_ids))), None)
                if first_active == lowest_cand_id:
                    # Look for next active preference
                    next_active = next((p for p in prefs if p in active_candidate_ids), None)
                    if next_active:
                        transferred_votes_log.append({
                            "fromCandidateId": lowest_cand_id,
                            "fromCandidateName": cand_map.get(lowest_cand_id),
                            "toCandidateId": next_active,
                            "toCandidateName": cand_map.get(next_active),
                            "count": 1
                        })

        round_doc = {
            "electionId": election_id,
            "roundNumber": round_number,
            "tally": tally,
            "eliminatedCandidateIds": eliminated_ids,
            "transferredVotes": transferred_votes_log,
            "winnerCandidateId": None,
            "winnerName": None,
            "quota": quota,
            "timestamp": now,
            "created_at": now
        }
        res = await db.election_rounds.insert_one(round_doc)
        round_doc["id"] = str(res.inserted_id)
        rounds_summary.append(round_doc)

        round_number += 1

    return {
        "electionId": election_id,
        "quota": quota,
        "totalValidVotes": total_valid_votes,
        "winnerCandidateId": winner_id,
        "winnerName": cand_map.get(winner_id) if winner_id else None,
        "rounds": rounds_summary
    }


async def get_election_rounds_history(election_id: str):
    """
    Fetch election round snapshots history (Audit Trail Feature 6 & Req 6.12).
    """
    cursor = db.election_rounds.find({"electionId": election_id}).sort("roundNumber", 1)
    rounds = await cursor.to_list(length=100)
    
    # Lookup candidate names for formatting
    cand_cursor = db.candidates.find({"electionId": election_id})
    raw_cands = await cand_cursor.to_list(length=200)
    cand_map = {str(c["_id"]): c.get("name", "Candidate") for c in raw_cands}

    result = []
    for r in rounds:
        tally = r.get("tally", {})
        formatted_tally = {}
        for cid, count in tally.items():
            c_name = cand_map.get(cid, cid)
            formatted_tally[cid] = {"candidateName": c_name, "votes": count}

        result.append({
            "id": str(r["_id"]),
            "electionId": r.get("electionId"),
            "roundNumber": r.get("roundNumber"),
            "tally": formatted_tally,
            "eliminatedCandidateIds": r.get("eliminatedCandidateIds", []),
            "eliminatedCandidateNames": [cand_map.get(cid, cid) for cid in r.get("eliminatedCandidateIds", [])],
            "transferredVotes": r.get("transferredVotes", []),
            "winnerCandidateId": r.get("winnerCandidateId"),
            "winnerName": r.get("winnerName"),
            "quota": r.get("quota", 0),
            "timestamp": r.get("timestamp")
        })

    return result


async def get_admin_voter_ballots(election_id: str):
    """
    Fetch individual voter choices for Super Admin / HR inspection (Req 6.11).
    """
    cursor = db.ballots.find({"electionId": election_id, "isSubmitted": True})
    ballots = await cursor.to_list(length=10000)

    cand_cursor = db.candidates.find({"electionId": election_id})
    raw_cands = await cand_cursor.to_list(length=200)
    cand_map = {str(c["_id"]): c.get("name", "Candidate") for c in raw_cands}

    result = []
    for b in ballots:
        voter_id = b.get("voterId")
        voter_name = "Unknown Voter"
        if voter_id:
            emp = await db.employees.find_one({"_id": voter_id})
            if not emp:
                emp = await db.users.find_one({"_id": voter_id})
            if emp:
                voter_name = emp.get("name") or emp.get("fullName") or f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip() or "Employee"

        ranked_preferences = []
        for pid in b.get("preferences", []):
            ranked_preferences.append({
                "candidateId": pid,
                "candidateName": cand_map.get(pid, pid)
            })

        result.append({
            "id": str(b["_id"]),
            "electionId": election_id,
            "voterId": voter_id,
            "voterName": voter_name,
            "preferences": ranked_preferences,
            "isSubmitted": b.get("isSubmitted", True),
            "submittedAt": b.get("submittedAt")
        })

    return result

