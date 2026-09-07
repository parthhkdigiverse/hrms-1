from fastapi import FastAPI, HTTPException, Depends, File, Form, UploadFile, Request, Response, WebSocket, WebSocketDisconnect, Query, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from contextlib import asynccontextmanager
import crud, schemas, database, auth, redis_manager
import uvicorn
import os
import uuid
from bson import ObjectId
from database import get_db
import holidays as pyholidays
from websocket import manager as ws_manager
import google_auth

import asyncio
from datetime import datetime, timedelta
import pytz
import random
from email_utils import send_otp_email
import pytz
async def get_actor_from_request(request: Request, db):
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            from jose import jwt
            from bson import ObjectId
            from auth import SECRET_KEY, ALGORITHM
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
            user_id = payload.get("sub")
            if user_id:
                user = await db.employees.find_one({"_id": ObjectId(user_id) if len(user_id) == 24 else user_id})
                if user:
                    return str(user.get("_id")), user.get("name", "System User")
        except Exception as e:
            print(f"JWT Decode error in get_actor_from_request: {e}", flush=True)
            pass
    else:
        print(f"No Authorization header found in request: {request.url}", flush=True)
    return "System", "System User"

async def content_calendar_reminder_task():
    from database import db
    import crud
    import schemas
    
    print("[Content Calendar Reminder] Task started.", flush=True)
    await asyncio.sleep(10) # wait for startup
    
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Kolkata'))
            today_str = now.strftime("%Y-%m-%d")
            
            # Check for Morning Reminder (e.g. 9:00 AM - 9:09 AM)
            if now.hour == 9 and now.minute < 10:
                entries = await db.content_calendar_entries.find({"postingDate": today_str}).to_list(length=1000)
                if entries:
                    admins = await db.employees.find({"role": {"$regex": "^Admin$", "$options": "i"}}).to_list(length=100)
                    admin_ids = [str(a["_id"]) if "_id" in a else a.get("id") for a in admins]
                    
                    for entry in entries:
                        entry_id = str(entry["_id"]) if "_id" in entry else entry.get("id")
                        client_id = entry.get("clientId")
                        client = None
                        if client_id and len(client_id) == 24:
                            client = await db.clients.find_one({"_id": ObjectId(client_id)})
                        client_name = client.get("companyName", "Unknown Client") if client else "Unknown Client"
                        
                        for target_id in admin_ids:
                            if not target_id: continue
                            existing_notif = await db.notifications.find_one({
                                "employee_id": target_id,
                                "type": "content_calendar_reminder",
                                "reference_id": entry_id,
                                "title": "Morning Schedule Reminder"
                            })
                            if not existing_notif:
                                await crud.create_notification(db, schemas.NotificationCreate(
                                    employee_id=target_id,
                                    title="Morning Schedule Reminder",
                                    message=f"Content for client '{client_name}' is scheduled to be posted today.",
                                    type="content_calendar_reminder",
                                    reference_id=entry_id
                                ))

            # Check for EOD Reminder (e.g. 18:00 PM - 18:09 PM)
            if now.hour == 18 and now.minute < 10:
                entries = await db.content_calendar_entries.find({"postingDate": today_str}).to_list(length=1000)
                if entries:
                    admins = await db.employees.find({"role": {"$regex": "^Admin$", "$options": "i"}}).to_list(length=100)
                    admin_ids = [str(a["_id"]) if "_id" in a else a.get("id") for a in admins]
                    
                    for entry in entries:
                        ig_link = str(entry.get("postingLinkOfIg") or "").strip()
                        final_link = str(entry.get("finalPostLink") or "").strip()
                        
                        if not ig_link and not final_link:
                            entry_id = str(entry["_id"]) if "_id" in entry else entry.get("id")
                            client_id = entry.get("clientId")
                            client = None
                            if client_id and len(client_id) == 24:
                                client = await db.clients.find_one({"_id": ObjectId(client_id)})
                            client_name = client.get("companyName", "Unknown Client") if client else "Unknown Client"
                            
                            for target_id in admin_ids:
                                if not target_id: continue
                                existing_notif = await db.notifications.find_one({
                                    "employee_id": target_id,
                                    "type": "content_calendar_reminder",
                                    "reference_id": entry_id,
                                    "title": "Post Due: Missing Link"
                                })
                                if not existing_notif:
                                    await crud.create_notification(db, schemas.NotificationCreate(
                                        employee_id=target_id,
                                        title="Post Due: Missing Link",
                                        message=f"Post for client '{client_name}' scheduled today is missing a post link.",
                                        type="content_calendar_reminder",
                                        reference_id=entry_id
                                    ))

        except Exception as e:
            print(f"[Content Calendar Reminder] Error: {e}", flush=True)
            
        await asyncio.sleep(300) # Sleep for 5 minutes

async def feedback_reminder_task():
    from database import db
    import crud
    import schemas
    
    print("[Feedback Reminder] Task started.", flush=True)
    await asyncio.sleep(15) # wait for startup
    
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Kolkata'))
            today_str = now.strftime("%Y-%m-%d")
            
            # Check for Morning Reminder (e.g. 9:00 AM - 9:09 AM)
            if now.hour == 9 and now.minute < 10:
                # Find projects where nextFeedbackDate is today or overdue
                projects = await db.projects.find({
                    "nextFeedbackDate": {"$lte": today_str},
                    "status": {"$ne": "completed"}
                }).to_list(length=1000)
                
                if projects:
                    for project in projects:
                        project_id = str(project["_id"]) if "_id" in project else project.get("id")
                        client_name = project.get("clientName", "Unknown Client")
                        project_title = project.get("title", "Unknown Project")
                        
                        target_id = project.get("teamLeaderId")
                        if not target_id:
                            # fallback to admin
                            admins = await db.employees.find({"role": {"$regex": "^Admin$", "$options": "i"}}).to_list(length=1)
                            if admins:
                                target_id = str(admins[0]["_id"]) if "_id" in admins[0] else admins[0].get("id")
                                
                        if target_id:
                            # Check if we already notified today to prevent spam within the 10 min window
                            # Using start of day for check
                            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                            existing_notif = await db.notifications.find_one({
                                "employee_id": target_id,
                                "type": "feedback_reminder",
                                "reference_id": project_id,
                                "title": "Feedback Collection Due"
                            }, sort=[("_id", -1)])
                            
                            should_notify = True
                            if existing_notif and existing_notif.get("created_at"):
                                # check if created_at was today
                                created_at = existing_notif.get("created_at")
                                if hasattr(created_at, "replace"):
                                    if created_at >= start_of_day:
                                        should_notify = False
                            
                            if should_notify:
                                await crud.create_notification(db, schemas.NotificationCreate(
                                    employee_id=target_id,
                                    title="Feedback Collection Due",
                                    message=f"It is time to collect feedback for project '{project_title}' ({client_name}).",
                                    type="feedback_reminder",
                                    reference_id=project_id
                                ))

        except Exception as e:
            print(f"[Feedback Reminder] Error: {e}", flush=True)
            
        await asyncio.sleep(300) # Sleep for 5 minutes

async def activity_logs_cleanup_task():
    """Runs a periodic cleanup to remove activity logs older than 45 days and chats older than 90 days."""
    from database import db
    import datetime
    
    print("[Logs Cleanup] Task started.", flush=True)
    await asyncio.sleep(30)  # Wait for startup
    
    while True:
        try:
            print("[Logs Cleanup] Starting periodic cleanup of database...", flush=True)
            # 1. Activity Logs (45 days)
            cutoff_logs = datetime.datetime.now() - datetime.timedelta(days=45)
            cutoff_logs_str = cutoff_logs.strftime("%Y-%m-%d %H:%M:%S")
            result_logs = await db.task_logs.delete_many({
                "timestamp": {"$lt": cutoff_logs_str}
            })
            print(f"[Logs Cleanup] Deleted {result_logs.deleted_count} logs older than '{cutoff_logs_str}'.", flush=True)
            
            # 2. Chat Messages (90 days) - preserving saved messages
            cutoff_chats = datetime.datetime.now() - datetime.timedelta(days=90)
            cutoff_chats_str = cutoff_chats.isoformat()
            result_chats = await db.messages.delete_many({
                "timestamp": {"$lt": cutoff_chats_str},
                "$or": [
                    {"savedBy": {"$exists": False}},
                    {"savedBy": None},
                    {"savedBy": {"$size": 0}}
                ]
            })
            print(f"[Logs Cleanup] Deleted {result_chats.deleted_count} chat messages older than '{cutoff_chats_str}'.", flush=True)
        except Exception as e:
            print(f"[Logs Cleanup] Error during cleanup: {e}", flush=True)
        await asyncio.sleep(86400) # Check once a day (24 hours)
            
async def auto_inactive_employee_task():
    from database import db
    import crud
    
    print("[Auto Inactive Employee] Task started.", flush=True)
    await asyncio.sleep(20) # wait for startup
    
    while True:
        try:
            settings = await crud.get_system_settings(db)
            if settings.get("autoInactiveAfterResignation") == True:
                now = datetime.now(pytz.timezone('Asia/Kolkata'))
                today_midnight = datetime(now.year, now.month, now.day, 0, 0, 0)
                today_str = now.strftime("%Y-%m-%d")
                
                active_employees = await db.employees.find({
                    "status": "active",
                    "hasResignation": True,
                    "$or": [
                        {"resignationDate": {"$lt": today_midnight}},
                        {"resignationDate": {"$lt": today_str}}
                    ]
                }).to_list(length=1000)
                
                for emp in active_employees:
                    emp_id = str(emp["_id"]) if "_id" in emp else emp.get("id")
                    print(f"[Auto Inactive] Setting employee {emp.get('name')} ({emp_id}) to inactive. Resignation date was {emp.get('resignationDate')}.", flush=True)
                    
                    await db.employees.update_one(
                        {"_id": emp["_id"]},
                        {"$set": {"status": "inactive"}}
                    )
                    
                    await crud.log_activity(
                        db=db,
                        action="Employee Inactivated",
                        performedBy="System",
                        userName="System Auto-Inactivator",
                        details=f"Automatically set status to inactive on next day of resignation date ({emp.get('resignationDate')})."
                    )
        except Exception as e:
            print(f"[Auto Inactive Employee] Error: {e}", flush=True)
            
        await asyncio.sleep(1800) # Check every 30 minutes

async def auto_mark_present_inactive_hrms_users_task():
    from database import db
    from datetime import datetime
    import pytz
    
    print("[Auto Mark Present] Task started.", flush=True)
    await asyncio.sleep(40) # wait for startup
    
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Kolkata'))
            today_str = now.strftime("%Y-%m-%d")
            today_midnight = datetime(now.year, now.month, now.day, 0, 0, 0)
            
            inactive_users = await db.employees.find({
                "status": "active",
                "activelyUsingHRMS": False
            }).to_list(length=1000)
            
            for user in inactive_users:
                emp_id = str(user["_id"]) if "_id" in user else user.get("id")
                if not emp_id: continue
                
                existing = await db.attendance.find_one({
                    "employeeId": emp_id,
                    "date": {"$regex": f"^{today_str}"}
                })
                
                if not existing:
                    punch_in_time = today_midnight.replace(hour=9, minute=0, second=0)
                    punch_out_time = today_midnight.replace(hour=18, minute=0, second=0)
                    
                    new_attendance = {
                        "employeeId": emp_id,
                        "date": today_str,
                        "punchIn": punch_in_time,
                        "punchOut": punch_out_time,
                        "status": "Present",
                        "totalHours": 9.0,
                        "totalBreakHours": 0.0,
                        "regularHours": 9.0,
                        "overtimeHours": 0.0,
                        "location": {"lat": 0, "lng": 0, "address": "Auto Marked Present"}
                    }
                    await db.attendance.insert_one(new_attendance)
        except Exception as e:
            print(f"[Auto Mark Present] Error: {e}", flush=True)
            
        await asyncio.sleep(3600) # Check every hour

async def monthly_report_scheduler_task():
    from database import db
    import crud
    from datetime import timedelta
    
    print("[Monthly Report Scheduler] Task started.", flush=True)
    await asyncio.sleep(20) # wait for startup
    
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Kolkata'))
            
            # Run every night at 23:45
            if now.hour == 23 and now.minute >= 45:
                await crud.sync_monthly_marketing_reports(db)
                print(f"[Monthly Report Scheduler] Synced reports for {now.strftime('%B %Y')}", flush=True)
                await asyncio.sleep(7200) # Sleep for 2 hours to avoid running multiple times
                continue
                
            # On the 1st of the month, run a final sync for the previous month shortly after midnight
            if now.day == 1 and now.hour == 0 and now.minute >= 5:
                prev_day = now - timedelta(days=1)
                await crud.sync_monthly_marketing_reports(db, date_str=prev_day.strftime("%Y-%m-%d"))
                print(f"[Monthly Report Scheduler] Final sync for previous month: {prev_day.strftime('%B %Y')}", flush=True)
                await asyncio.sleep(7200) # Sleep for 2 hours
                continue
                
        except Exception as e:
            print(f"[Monthly Report Scheduler] Error: {e}", flush=True)
            
        await asyncio.sleep(300) # Sleep for 5 minutes

@asynccontextmanager
async def lifespan(app):
    # --- Startup ---
    # Database migration: clean up department and designation for admin users
    try:
        from database import db
        print("[Admin Migration] Cleaning up department and designation for admin users...", flush=True)
        admin_roles_list = ["admin", "super admin", "superadmin", "administrator", "founder"]
        admin_query = {
            "$or": [
                {"role": {"$regex": r"^(admin|super\s*admin|superadmin|administrator|founder)$", "$options": "i"}},
                {"role": {"$in": admin_roles_list}}
            ]
        }
        await db.employees.update_many(
            admin_query,
            {"$set": {"department": "", "designation": ""}}
        )
        print("[Admin Migration] Completed admin cleanup.", flush=True)

        # Create Default Admin & Sub-Admin Presets if they don't exist
        print("[Admin Migration] Checking for Admin and Sub-Admin presets...", flush=True)
        default_presets = [
            {"name": "Admin", "description": "System Administrator Preset with Full Access", "presetType": "role"},
            {"name": "Sub Admin", "description": "Sub Administrator Preset", "presetType": "role"}
        ]
        
        modules_config = [
            {'moduleName': 'employee-list', 'displayName': 'Employee List', 'tabUrl': '/employees'},
            {'moduleName': 'org-structure', 'displayName': 'Org Structure', 'tabUrl': '/employees/organization/departments'},
            {'moduleName': 'employee-attendance', 'displayName': 'Employee Attendance List', 'tabUrl': '/employees/attendance'},
            {'moduleName': 'leave-requests', 'displayName': 'Leave Requests', 'tabUrl': '/employees/leave'},
            {'moduleName': 'employee-documents', 'displayName': 'Employee Documents', 'tabUrl': '/employees/documents'},
            {'moduleName': 'document-generator', 'displayName': 'Document Generator', 'tabUrl': '/employees/documents/generate'},
            {'moduleName': 'salary-structure', 'displayName': 'Salary Structure', 'tabUrl': '/payroll/salary-structure'},
            {'moduleName': 'payroll-processing', 'displayName': 'Payroll Processing', 'tabUrl': '/payroll'},
            {'moduleName': 'payslips', 'displayName': 'Payslips', 'tabUrl': '/payroll/payslips'},
            {'moduleName': 'bonuses-deductions', 'displayName': 'Bonuses & Deductions', 'tabUrl': '/payroll/bonuses'},
            {'moduleName': 'company-finance-transactions', 'displayName': 'Transactions', 'tabUrl': '/company-finance'},
            {'moduleName': 'company-finance-plan', 'displayName': 'Plan', 'tabUrl': '/company-finance/plan'},
            {'moduleName': 'company-finance-summary', 'displayName': 'Summary', 'tabUrl': '/company-finance/summary'},
            {'moduleName': 'company-finance-client-transactions', 'displayName': 'Client Transactions', 'tabUrl': '/company-finance/client-transactions'},
            {'moduleName': 'company-finance-audit-logs', 'displayName': 'Audit Logs', 'tabUrl': '/company-finance/logs'},
            {'moduleName': 'interviews', 'displayName': 'Interviews', 'tabUrl': '/recruitment/hiring-board'},
            {'moduleName': 'hirings', 'displayName': 'Hirings', 'tabUrl': '/recruitment'},
            {'moduleName': 'attendance', 'displayName': 'Attendance', 'tabUrl': '/attendance'},
            {'moduleName': 'leave', 'displayName': 'Leave', 'tabUrl': '/leave'},
            {'moduleName': 'schedule', 'displayName': 'Schedule', 'tabUrl': '/schedule'},
            {'moduleName': 'projects', 'displayName': 'Projects', 'tabUrl': '/work-management/projects'},
            {'moduleName': 'tasks', 'displayName': 'Development', 'tabUrl': '/work-management/development'},
            {'moduleName': 'personal-tasks', 'displayName': 'Tasks', 'tabUrl': '/tasks'},
            {'moduleName': 'daily-progress', 'displayName': 'Daily Progress', 'tabUrl': '/work-management/daily-progress'},
            {'moduleName': 'work-logs', 'displayName': 'Work Logs', 'tabUrl': '/work-management/work-logs'},
            {'moduleName': 'sales', 'displayName': 'Sales', 'tabUrl': '/work-management/sales'},
            {'moduleName': 'clients', 'displayName': 'Clients', 'tabUrl': '/work-management/clients'},
            {'moduleName': 'marketing', 'displayName': 'Digital Marketing', 'tabUrl': '/work-management/digital-marketing'},
            {'moduleName': 'creative', 'displayName': 'Social Media Management', 'tabUrl': '/work-management/smm'},
            {'moduleName': 'research', 'displayName': 'Research', 'tabUrl': '/work-management/research'},
            {'moduleName': 'seating-arrangement', 'displayName': 'Seating Arrangement', 'tabUrl': '/workspace/seating'},
            {'moduleName': 'resource-management', 'displayName': 'Resource Management', 'tabUrl': '/workspace/resource'},
            {'moduleName': 'voting', 'displayName': 'Elections', 'tabUrl': '/voting'},
            {'moduleName': 'remarks', 'displayName': 'Penalty', 'tabUrl': '/penalty'},
            {'moduleName': 'review', 'displayName': 'Remarks', 'tabUrl': '/remarks'},
            {'moduleName': 'invoice', 'displayName': 'Invoice', 'tabUrl': '/invoice'},
            {'moduleName': 'chat', 'displayName': 'Chat', 'tabUrl': '/chat'},
            {'moduleName': 'activity-tracker', 'displayName': 'Activity Tracker', 'tabUrl': '/activity-tracker'},
            {'moduleName': 'activity-logs', 'displayName': 'Activity Logs', 'tabUrl': '/activity-logs'},
            {'moduleName': 'gallery', 'displayName': 'Gallery', 'tabUrl': '/workspace/gallery'},
            {'moduleName': 'training', 'displayName': 'Course Library', 'tabUrl': '/training'},
            {'moduleName': 'admin-courses', 'displayName': 'Manage Courses', 'tabUrl': '/admin/courses'},
            {'moduleName': 'dashboard', 'displayName': 'Dashboard', 'tabUrl': '/'},
            {'moduleName': 'access-control', 'displayName': 'Access Control', 'tabUrl': '/settings'},
            {'moduleName': 'settings', 'displayName': 'Settings', 'tabUrl': '/settings'}
        ]
        
        for preset_info in default_presets:
            existing = await db.permission_presets.find_one({
                "name": {"$regex": f"^{preset_info['name']}$", "$options": "i"}, 
                "presetType": "role"
            })
            if not existing:
                is_admin = (preset_info['name'] == 'Admin')
                permissions = []
                for mod in modules_config:
                    permissions.append({
                        "moduleName": mod["moduleName"],
                        "displayName": mod["displayName"],
                        "tabUrl": mod["tabUrl"],
                        "canAdd": is_admin,
                        "canEdit": is_admin,
                        "canDelete": is_admin,
                        "canView": is_admin
                    })
                
                await db.permission_presets.insert_one({
                    "name": preset_info['name'],
                    "description": preset_info['description'],
                    "presetType": preset_info['presetType'],
                    "department": None,
                    "designation": None,
                    "permissions": permissions
                })
                print(f"[Admin Migration] Created default preset for {preset_info['name']}", flush=True)

    except Exception as e:
        print(f"[Admin Migration] Error: {e}", flush=True)

    # Database migration: clean up registered_pcs duplicate hostnames and restore raw/original casing
    try:
        from database import db
        print("[PC Migration] Starting registered_pcs database cleanup/migration...", flush=True)
        cursor = db.registered_pcs.find({})
        all_pcs = await cursor.to_list(length=10000)
        
        # Keep track of unique lowercased hostnames to detect duplicate casing
        seen_pcs = {} # { hostname_lower: doc }
        
        for pc in all_pcs:
            orig_hostname = pc.get("hostname", "")
            if not orig_hostname:
                continue
            
            hostname_lower = orig_hostname.lower()
            
            # Check if this hostname is already processed in lowercase form
            if hostname_lower in seen_pcs:
                # Merge current duplicate with the already seen lowercase one
                target_pc = seen_pcs[hostname_lower]
                
                # Merge settings
                merged_fields = {}
                
                # Take activeEmployee if target is empty and current has it
                if not target_pc.get("activeEmployee") and pc.get("activeEmployee"):
                    merged_fields["activeEmployee"] = pc.get("activeEmployee")
                
                # Boolean flags: if either is True, keep True
                for flag in ["blockChrome", "blockYoutube"]:
                    if pc.get(flag) is True and target_pc.get(flag) is not True:
                        merged_fields[flag] = True
                
                # Lists: union lists
                for list_field in ["blockApps", "blockUrls"]:
                    target_list = list(target_pc.get(list_field) or [])
                    current_list = list(pc.get(list_field) or [])
                    combined = list(set(target_list + current_list))
                    if len(combined) != len(target_list):
                        merged_fields[list_field] = combined
                
                # Newer timestamps/info
                if pc.get("lastSeen") and (not target_pc.get("lastSeen") or pc.get("lastSeen") > target_pc.get("lastSeen")):
                    merged_fields["lastSeen"] = pc.get("lastSeen")
                    if pc.get("ipAddress"):
                        merged_fields["ipAddress"] = pc.get("ipAddress")
                    if pc.get("os"):
                        merged_fields["os"] = pc.get("os")
                    if pc.get("osVersion"):
                        merged_fields["osVersion"] = pc.get("osVersion")
                
                # Preserve the casing of whichever document is non-lowercase (if applicable)
                if orig_hostname != hostname_lower and target_pc.get("hostname") == hostname_lower:
                    merged_fields["hostname"] = orig_hostname
                
                # Update the target lowercase document with merged fields
                if merged_fields:
                    await db.registered_pcs.update_one({"_id": target_pc["_id"]}, {"$set": merged_fields})
                    # Update our in-memory reference
                    seen_pcs[hostname_lower].update(merged_fields)
                
                # Delete the duplicate document (since it has been merged)
                await db.registered_pcs.delete_one({"_id": pc["_id"]})
                print(f"[PC Migration] Merged and deleted duplicate registered PC: {orig_hostname}", flush=True)
            else:
                # If the current document is NOT lowercase, check if there is an existing lowercase document in db
                if orig_hostname != hostname_lower:
                    # Let's search db for existing lowercase document
                    existing_lower = await db.registered_pcs.find_one({"hostname": hostname_lower})
                    if existing_lower:
                        # Found existing lowercase document! We merge this one into existing_lower.
                        seen_pcs[hostname_lower] = existing_lower
                        
                        # Process merging
                        target_pc = existing_lower
                        merged_fields = {}
                        
                        # Preserve original non-lowercase casing in the merged doc!
                        merged_fields["hostname"] = orig_hostname
                        
                        if not target_pc.get("activeEmployee") and pc.get("activeEmployee"):
                            merged_fields["activeEmployee"] = pc.get("activeEmployee")
                        for flag in ["blockChrome", "blockYoutube"]:
                            if pc.get(flag) is True and target_pc.get(flag) is not True:
                                merged_fields[flag] = True
                        for list_field in ["blockApps", "blockUrls"]:
                            target_list = list(target_pc.get(list_field) or [])
                            current_list = list(pc.get(list_field) or [])
                            combined = list(set(target_list + current_list))
                            if len(combined) != len(target_list):
                                merged_fields[list_field] = combined
                        if pc.get("lastSeen") and (not target_pc.get("lastSeen") or pc.get("lastSeen") > target_pc.get("lastSeen")):
                            merged_fields["lastSeen"] = pc.get("lastSeen")
                            if pc.get("ipAddress"):
                                merged_fields["ipAddress"] = pc.get("ipAddress")
                            if pc.get("os"):
                                merged_fields["os"] = pc.get("os")
                            if pc.get("osVersion"):
                                merged_fields["osVersion"] = pc.get("osVersion")
                        
                        if merged_fields:
                            await db.registered_pcs.update_one({"_id": target_pc["_id"]}, {"$set": merged_fields})
                            seen_pcs[hostname_lower].update(merged_fields)
                        
                        # Delete the duplicate document
                        await db.registered_pcs.delete_one({"_id": pc["_id"]})
                        print(f"[PC Migration] Merged and deleted duplicate registered PC: {orig_hostname} into existing lowercase", flush=True)
                    else:
                        # Lowercase document does not exist, so keep the current uppercase/raw casing as is!
                        seen_pcs[hostname_lower] = pc
                else:
                    # Already lowercase, keep it as reference
                    seen_pcs[hostname_lower] = pc
        print("[PC Migration] Finished registered_pcs database migration.", flush=True)
    except Exception as e:
        print(f"[PC Migration] Error running registered_pcs migration: {e}", flush=True)
    
    # Seed the employee_id counter if it doesn't exist yet
    try:
        from database import db
        existing_counter = await db.counters.find_one({"_id": "employee_id"})
        if not existing_counter:
            count = await db.employees.count_documents({})
            await db.counters.insert_one({"_id": "employee_id", "seq": count})
            print(f"Seeded employee_id counter at {count}")
    except Exception as e:
        print(f"Error seeding employee counter: {e}")
    # Seed default document types if they don't exist
    try:
        from database import db
        existing_types = await db.document_types.count_documents({})
        if existing_types == 0:
            defaults = [
                {"name": "Security Cheque", "description": "Cheque collected for security purpose"},
                {"name": "Degree Certificate", "description": "Highest education degree certificate"},
                {"name": "10th Marksheet", "description": "10th grade marksheet/certificate"},
                {"name": "12th Marksheet", "description": "12th grade marksheet/certificate"},
                {"name": "Passport Photo", "description": "Recent passport size photograph"},
                {"name": "Security Deposite (Employee - 10000)", "description": "Security deposit for standard employee structure (INR 10,000)"},
                {"name": "Security Deposite (Intern - 2000)", "description": "Security deposit for intern structure (INR 2,000)"}
            ]
            await db.document_types.insert_many(defaults)
            print("Seeded default document types")
    except Exception as e:
        print(f"Error seeding default document types: {e}")
    
    # Start the global input tracker
    try:
        import platform
        import os
        from database import db
        import input_tracker
        run_tracker = os.environ.get("RUN_TRACKER", "true").lower() == "true"
        if platform.system() != "Linux" and run_tracker:
            input_tracker.start_tracker(db)
        else:
            print("[Tracker] Skipping input tracker start (disabled or running on Linux/Production server).")
    except Exception as e:
        print(f"Error starting global input tracker: {e}")
 
    # Auto-register local PC device
    try:
        from database import db
        import socket
        import platform
        hostname = socket.gethostname()
        os_name = platform.system()
        os_version = platform.release()
        
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
            
        if os_name != "Linux":
            await crud.register_pc_device(db, hostname, local_ip, os_name, os_version)
            print(f"[PC Registration] Registered device: {hostname} ({local_ip})")
        else:
            print("[PC Registration] Skipping registration (running on Linux/Production server).")
    except Exception as e:
        print(f"[PC Registration] Failed to register PC: {e}")

    # Ensure database indexes are created to speed up loading across high-traffic collections
    try:
        from database import db
        print("[Database Indexing] Ensuring indexes for high-traffic collections...", flush=True)
        # Chat Messages
        await db.messages.create_index([("groupId", 1), ("timestamp", 1)])
        await db.messages.create_index([("senderId", 1), ("receiverId", 1), ("timestamp", 1)])
        await db.messages.create_index([("receiverId", 1), ("senderId", 1), ("timestamp", 1)])
        
        # Work Management Tasks
        await db.wm_tasks.create_index([("department", 1), ("status", 1)])
        await db.wm_tasks.create_index([("assignedToId", 1), ("status", 1)])
        await db.wm_tasks.create_index([("projectId", 1)])
        await db.wm_tasks.create_index([("dueDate", 1)])
        await db.wm_tasks.create_index([("postingDate", 1)])
        
        # Projects
        await db.projects.create_index([("department", 1), ("status", 1)])
        await db.projects.create_index([("clientId", 1)])
        await db.projects.create_index([("teamLeaderId", 1)])
        
        # Employees & Attendance
        await db.employees.create_index([("email", 1)])
        await db.employees.create_index([("department", 1)])
        await db.attendance.create_index([("employeeId", 1), ("date", -1)])
        await db.attendance.create_index([("date", -1)])
        
        # Logs & Clients
        await db.task_logs.create_index([("taskId", 1), ("timestamp", -1)])
        await db.task_logs.create_index([("projectId", 1), ("timestamp", -1)])
        await db.clients.create_index([("department", 1)])

        # Marketing Reports & Remarks
        await db.marketing_daily_reports.create_index([("clientId", 1), ("date", -1)])
        await db.marketing_daily_reports.create_index([("projectId", 1), ("date", -1)])
        await db.marketing_monthly_reports.create_index([("clientId", 1), ("month", 1)])
        await db.marketing_project_daily_remarks.create_index([("projectId", 1), ("date", -1)])

        # Notifications, Content Calendar, User Input Stats, and general log timestamps
        await db.notifications.create_index([("userId", 1), ("_id", -1)])
        await db.content_calendar_entries.create_index([("postingDate", 1)])
        await db.user_input_stats.create_index([("employeeId", 1), ("date", -1)])
        await db.task_logs.create_index([("timestamp", -1)])
        
        print("[Database Indexing] All database indexes verified/created successfully.", flush=True)
    except Exception as e:
        print(f"[Database Indexing] Failed to create indexes: {e}", flush=True)

    try:
        await redis_manager.init_redis()
    except Exception as e:
        print(f"[Redis Init Warning] {e}", flush=True)

    reminder_task = asyncio.create_task(content_calendar_reminder_task())
    feedback_task = asyncio.create_task(feedback_reminder_task())
    monthly_report_task = asyncio.create_task(monthly_report_scheduler_task())
    logs_cleanup_task = asyncio.create_task(activity_logs_cleanup_task())
    auto_inactive_task = asyncio.create_task(auto_inactive_employee_task())
    auto_present_task = asyncio.create_task(auto_mark_present_inactive_hrms_users_task())
    yield
    # --- Shutdown ---
    try:
        await redis_manager.close_redis()
    except Exception as e:
        print(f"[Redis Shutdown Warning] {e}", flush=True)

    try:
        import input_tracker
        input_tracker.stop_tracker()
    except Exception as e:
        print(f"Error stopping global input tracker: {e}")
        
    try:
        if not reminder_task.done():
            reminder_task.cancel()
        if not feedback_task.done():
            feedback_task.cancel()
        if not monthly_report_task.done():
            monthly_report_task.cancel()
        if not logs_cleanup_task.done():
            logs_cleanup_task.cancel()
        if not auto_inactive_task.done():
            auto_inactive_task.cancel()
    except Exception:
        pass
    # Reload trigger: 1

app = FastAPI(title="HRMS API", lifespan=lifespan)

from fastapi.exceptions import RequestValidationError
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    import json
    import os
    import sys
    try:
        body = await request.body()
        body_str = body.decode("utf-8", errors="ignore")
    except Exception:
        body_str = "could not read body"
    
    errors = exc.errors()
    sys.stderr.write(f"\nVALIDATION_ERROR_DETAILS: {json.dumps({'url': str(request.url), 'errors': errors, 'body': body_str})}\n")
    sys.stderr.flush()
    
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        err_path = os.path.join(current_dir, "validation_error.json")
        with open(err_path, "w", encoding="utf-8") as f:
            json.dump({
                "url": str(request.url),
                "errors": errors,
                "body": body_str
            }, f, indent=2)
    except Exception as ex:
        sys.stderr.write(f"Error writing validation error to file: {ex}\n")
        sys.stderr.flush()
    return JSONResponse(
        status_code=422,
        content={"detail": errors}
    )

# CORS: read allowed origins from env (comma-separated), fallback to localhost for dev
_default_origins = "http://localhost:3535,http://127.0.0.1:3535,http://localhost:3550,http://127.0.0.1:3550"
_allowed_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import shutil
from fastapi.staticfiles import StaticFiles

class SafeStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except Exception as ex:
            if hasattr(ex, "status_code") and ex.status_code == 404:
                ext = os.path.splitext(path)[1].lower()
                # Only return placeholder SVG for image files, NOT for binary/download files
                if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']:
                    placeholder_svg = (
                        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="%23cbd5e1" stroke-width="1.5">'
                        '<rect width="20" height="20" x="2" y="2" rx="2" ry="2" fill="%23f8fafc"/>'
                        '<circle cx="8.5" cy="8.5" r="1.5" fill="%23cbd5e1"/>'
                        '<path d="m22 14-4.586-4.586a2 2 0 0 0-2.828 0L4 20"/>'
                        '</svg>'
                    )
                    return Response(content=placeholder_svg, media_type="image/svg+xml")
                # For .exe, .zip, .pdf and other binary files — re-raise so proper 404 is returned
            raise ex

# Ensure uploads directory exists
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

app.mount("/uploads", SafeStaticFiles(directory=UPLOAD_DIR), name="uploads")

from fastapi import Request
from jose import jwt

EXEMPT_PATHS = ["/login", "/time", "/", "/docs", "/openapi.json", "/redoc"]

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
        
    path = request.url.path
    if path in EXEMPT_PATHS or path.startswith("/uploads") or path.startswith("/chat/upload") or path.startswith("/ws") or path.startswith("/socket.io"):
        return await call_next(request)
        
    # Temporarily disabled per user request
    # authorization = request.headers.get("Authorization")
    # if not authorization or not authorization.startswith("Bearer "):
    #     return JSONResponse(status_code=401, content={"detail": "Could not validate credentials"})
        
    # token = authorization.split(" ")[1]
    # try:
    #     payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    #     request.state.user_id = payload.get("sub")
    # except Exception as e:
    #     print(f"Auth error: {e}")
    #     return JSONResponse(status_code=401, content={"detail": "Could not validate credentials"})
        
    response = await call_next(request)
    return response

@app.get("/")
async def root():
    return {"message": "Welcome to HRMS API"}

MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MB

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File size exceeds the 512 MB limit")
    filename = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as buffer:
        buffer.write(contents)
    return {"url": f"/uploads/{filename}"}

@app.delete("/upload/{filename}")
async def delete_file(filename: str):
    import os
    # Prevent directory traversal
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return {"message": "File deleted successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error deleting file: {str(e)}")
    else:
        raise HTTPException(status_code=404, detail="File not found")

@app.post("/upload-profile-photo/{user_id}")
async def upload_profile_photo(user_id: str, file: UploadFile = File(...), db=Depends(get_db)):
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File size exceeds the 512 MB limit")

    # Fetch the employee first so we can grab the old photo filename
    user = await crud.get_employee(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_photo = user.get("profilePhoto") if isinstance(user, dict) else getattr(user, "profilePhoto", None)

    filename = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as buffer:
        buffer.write(contents)

    update_data = schemas.EmployeeUpdate(profilePhoto=filename)
    updated_user = await crud.update_employee(db, user_id, update_data)

    if not updated_user:
        # DB update failed – remove the newly uploaded file to avoid orphaned files
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail="Failed to update user profile photo")

    # Delete the old profile photo from disk now that the DB is updated
    if old_photo:
        old_file_path = os.path.join(UPLOAD_DIR, old_photo)
        if os.path.exists(old_file_path):
            try:
                os.remove(old_file_path)
            except Exception as e:
                print(f"Warning: could not delete old profile photo '{old_photo}': {e}")

    return updated_user

@app.get("/attachments/open/{filename:path}")
async def open_attachment(filename: str):
    import re
    import mimetypes
    import urllib.parse
    from fastapi.responses import FileResponse
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    clean_name = re.sub(r'^[a-f0-9]+_', '', filename)
    content_type, _ = mimetypes.guess_type(file_path)
    if not content_type:
        content_type = "application/octet-stream"
        
    ascii_name = clean_name.encode('ascii', errors='ignore').decode('ascii')
    encoded_name = urllib.parse.quote(clean_name)
    headers = {
        "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
    }
    return FileResponse(
        path=file_path,
        media_type=content_type,
        headers=headers
    )

@app.get("/attachments/download/{filename:path}")
async def download_attachment(filename: str):
    import re
    import urllib.parse
    from fastapi.responses import FileResponse
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    clean_name = re.sub(r'^[a-f0-9]+_', '', filename)
    
    ascii_name = clean_name.encode('ascii', errors='ignore').decode('ascii')
    encoded_name = urllib.parse.quote(clean_name)
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
    }
    return FileResponse(
        path=file_path,
        media_type="application/octet-stream",
        headers=headers
    )

@app.post("/chat/upload")
async def upload_chat_file(file: UploadFile = File(...)):
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File size exceeds the 512 MB limit")
    filename = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as buffer:
        buffer.write(contents)
    return {"url": f"/uploads/{filename}", "filename": filename}

# Auth
@app.post("/login", response_model=schemas.LoginResponse)
async def login(login_data: schemas.LoginRequest, db=Depends(get_db)):
    user = await crud.authenticate_user(db, login_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("status", "").lower() == "inactive":
        raise HTTPException(status_code=403, detail="Your account has been deactivated. Please contact the administrator.")
    
    # Check OTP requirements
    settings = await db.system_settings.find_one({}) or {}
    otp_roles = settings.get("otpRequiredRoles", [])
    
    raw_role = user.get("role", "").strip().lower()
    if raw_role == "admin":
        mapped_role = "admin"
    elif raw_role == "hr":
        mapped_role = "hr"
    else:
        mapped_role = "employee"
        
    if mapped_role not in otp_roles:
        # Skip OTP and return token directly
        return {"message": "Login successful", "user": user, "token": user["token"], "require_otp": False}

    # Generate 6-digit OTP
    otp = str(random.randint(100000, 999999))
    expiry = datetime.now(pytz.timezone('Asia/Kolkata')) + timedelta(minutes=5)
    
    # Save OTP to user document
    await db.employees.update_one(
        {"email": login_data.email},
        {"$set": {"login_otp": otp, "login_otp_expiry": expiry.isoformat()}}
    )
    
    # Send email
    send_otp_email(login_data.email, otp)
    
    return {"message": "OTP sent to your email", "require_otp": True}

@app.post("/login/verify-otp", response_model=schemas.LoginResponse)
async def verify_otp(otp_data: schemas.VerifyOTPRequest, db=Depends(get_db)):
    user = await db.employees.find_one({"email": otp_data.email})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    stored_otp = user.get("login_otp")
    expiry_str = user.get("login_otp_expiry")
    
    if not stored_otp or not expiry_str:
        raise HTTPException(status_code=400, detail="OTP not requested or expired")
        
    expiry = datetime.fromisoformat(expiry_str)
    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    if now > expiry:
        # Clear expired OTP
        await db.employees.update_one({"email": otp_data.email}, {"$unset": {"login_otp": "", "login_otp_expiry": ""}})
        raise HTTPException(status_code=400, detail="OTP expired")
        
    if stored_otp != otp_data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
        
    # Clear OTP upon successful verification
    await db.employees.update_one({"email": otp_data.email}, {"$unset": {"login_otp": "", "login_otp_expiry": ""}})
    
    # Generate JWT token
    user_id = str(user["_id"])
    user_fixed = crud.fix_id(user)
    
    permissions_doc = await db.user_permissions.find_one({"employeeId": user_id})
    if permissions_doc:
        user_fixed["permissions"] = crud.fix_id(permissions_doc).get("permissions", [])
    else:
        user_fixed["permissions"] = []
        
    token = auth.create_access_token(data={"sub": user_id, "role": user.get("role", "")})
    user_fixed["token"] = token
    
    return {"message": "Login successful", "user": user_fixed, "token": token, "require_otp": False}

@app.post("/finance-otp/request")
async def request_finance_otp(req: schemas.FinanceOTPRequest, db=Depends(get_db)):
    settings = await db.system_settings.find_one({}) or {}
    finance_email = settings.get("financeOtpEmail")
    if not finance_email:
        finance_email = settings.get("companyEmail")
    
    if not finance_email:
        raise HTTPException(status_code=400, detail="Finance or Company email not configured in settings")
        
    otp = str(random.randint(100000, 999999))
    expiry = datetime.now(pytz.timezone('Asia/Kolkata')) + timedelta(minutes=10)
    
    # Save OTP to user document
    await db.employees.update_one(
        {"email": req.email},
        {"$set": {"finance_otp": otp, "finance_otp_expiry": expiry.isoformat()}}
    )
    
    # Send email to finance_email instead of user email
    send_otp_email(finance_email, otp)
    
    return {"message": "OTP sent to finance admin email"}

@app.post("/finance-otp/verify")
async def verify_finance_otp(otp_data: schemas.VerifyOTPRequest, db=Depends(get_db)):
    user = await db.employees.find_one({"email": otp_data.email})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    stored_otp = user.get("finance_otp")
    expiry_str = user.get("finance_otp_expiry")
    
    if not stored_otp or not expiry_str:
        raise HTTPException(status_code=400, detail="OTP not requested or expired")
        
    expiry = datetime.fromisoformat(expiry_str)
    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    if now > expiry:
        await db.employees.update_one({"email": otp_data.email}, {"$unset": {"finance_otp": "", "finance_otp_expiry": ""}})
        raise HTTPException(status_code=400, detail="OTP expired")
        
    if stored_otp != otp_data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
        
    await db.employees.update_one({"email": otp_data.email}, {"$unset": {"finance_otp": "", "finance_otp_expiry": ""}})
    
    return {"message": "Finance OTP verified successfully"}

# Employee Endpoints
@app.get("/time")
async def get_system_time():
    now = crud.get_now()
    return {
        "datetime": now.isoformat(),
        "timestamp": now.timestamp(),
        "timezone": "Asia/Kolkata"
    }

@app.get("/employees", response_model=List[schemas.Employee])
@redis_manager.cached_api(namespace="hrms:employees", ttl=300)
async def read_employees(request: Request, skip: int = 0, limit: int = 10000, include_inactive: bool = False, db=Depends(get_db)):
    return await crud.get_employees(db, skip=skip, limit=limit, include_inactive=include_inactive)

@app.get("/employees/{employee_id}", response_model=schemas.Employee)
@redis_manager.cached_api(namespace="hrms:employees", ttl=300)
async def read_employee(employee_id: str, request: Request, db=Depends(get_db)):
    employee = await crud.get_employee(db, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee

ROLE_HIERARCHY = {
    "super admin": 0,
    "admin": 0,
    "superadmin": 0,
    "administrator": 0,
    "founder": 0,
    "super_admin": 0,
    "sub-admin": 1,
    "hr": 2,
    "manager": 3,
    "team leader": 4,
    "employee": 5,
    "intern": 6
}

def get_role_level(role: str) -> int:
    if not role:
        return 5
    return ROLE_HIERARCHY.get(role.lower().strip(), 5)

@app.post("/employees", response_model=schemas.Employee)
async def create_employee(employee: schemas.EmployeeCreate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    
    if performed_by != "System":
        actor = await db.employees.find_one({"_id": ObjectId(performed_by) if len(performed_by) == 24 else performed_by})
        if actor:
            actor_role = actor.get("role", "").lower()
            target_role = (employee.role or "").lower()
            
            actor_level = get_role_level(actor_role)
            target_level = get_role_level(target_role)
            
            if actor_level > 0 and target_level < actor_level:
                raise HTTPException(status_code=403, detail="You do not have permission to create this role")

    try:
        created = await crud.create_employee(db, employee, performed_by=performed_by, user_name=user_name)
        await redis_manager.invalidate_namespace("hrms:employees")
        return created
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@app.put("/employees/{employee_id}", response_model=schemas.Employee)
async def update_employee(employee_id: str, employee_update: schemas.EmployeeUpdate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    if performed_by != "System" and performed_by != employee_id:
        actor = await db.employees.find_one({"_id": ObjectId(performed_by) if len(performed_by) == 24 else performed_by})
        if not actor:
            raise HTTPException(status_code=403, detail="You do not have permission to modify this employee's details")
        
        actor_role = actor.get("role", "").lower()
        actor_level = get_role_level(actor_role)
        
        target_emp = await db.employees.find_one({"_id": ObjectId(employee_id) if len(employee_id) == 24 else employee_id})
        if target_emp:
            target_emp_role = target_emp.get("role", "").lower()
            new_role = (employee_update.role or "").lower() if employee_update.role is not None else target_emp_role
            
            target_emp_level = get_role_level(target_emp_role)
            new_role_level = get_role_level(new_role)
            
            if actor_level > 0:
                if target_emp_level < actor_level:
                    raise HTTPException(status_code=403, detail="You cannot modify a profile with a higher role")
                if new_role_level < actor_level:
                    raise HTTPException(status_code=403, detail="You cannot assign a role higher than your own")
    try:
        updated = await crud.update_employee(db, employee_id, employee_update, performed_by=performed_by, user_name=user_name)
        if not updated:
            raise HTTPException(status_code=404, detail="Employee not found")
        await redis_manager.invalidate_namespace("hrms:employees")
        return updated
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@app.delete("/employees/{employee_id}")
async def delete_employee(employee_id: str, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    await crud.delete_employee(db, employee_id, performed_by=performed_by, user_name=user_name)
    await redis_manager.invalidate_namespace("hrms:employees")
    return {"message": "Employee deleted successfully"}

@app.post("/employees/transfer-responsibilities")
async def transfer_responsibilities(payload: dict, request: Request, db=Depends(get_db)):
    from_emp_id = payload.get("fromEmployeeId")
    to_emp_id = payload.get("toEmployeeId")
    if not from_emp_id or not to_emp_id:
        raise HTTPException(status_code=400, detail="Missing fromEmployeeId or toEmployeeId")

    actor_id, actor_name = await get_actor_from_request(request, db)
    if actor_id != "System":
        actor = await db.employees.find_one({"_id": ObjectId(actor_id) if len(actor_id) == 24 else actor_id})
        if not actor or actor.get("role", "").lower() not in ["admin", "super admin", "manager"]:
            raise HTTPException(status_code=403, detail="Only admins can transfer responsibilities")

    from_emp = await db.employees.find_one({"_id": ObjectId(from_emp_id) if len(from_emp_id) == 24 else from_emp_id})
    to_emp = await db.employees.find_one({"_id": ObjectId(to_emp_id) if len(to_emp_id) == 24 else to_emp_id})
    if not from_emp or not to_emp:
        raise HTTPException(status_code=404, detail="One or both employees not found")

    from_name = f"{from_emp.get('firstName', '')} {from_emp.get('lastName', '')}".strip()
    to_name = f"{to_emp.get('firstName', '')} {to_emp.get('lastName', '')}".strip()

    # 1. Update Clients
    await db.clients.update_many({"assignedEmployeeId": from_emp_id}, {"$set": {"assignedEmployeeId": to_emp_id, "assignedEmployeeName": to_name}})
    creative_roles = [
        "assignedScriptwriterId", "assignedReelEditorId", "assignedPostDesignerId",
        "assignedShooterId", "assignedApproverId", "assignedPosterId",
        "assignedCaptionWriterId", "assignedThumbnailDesignerId"
    ]
    for role_id_field in creative_roles:
        name_field = role_id_field[:-2] + "Name"
        await db.clients.update_many({role_id_field: from_emp_id}, {"$set": {role_id_field: to_emp_id, name_field: to_name}})

    # 2. Update Projects
    await db.projects.update_many({"assignedEmployeeId": from_emp_id}, {"$set": {"assignedEmployeeId": to_emp_id, "assignedEmployeeName": to_name}})
    await db.projects.update_many({"teamLeaderId": from_emp_id}, {"$set": {"teamLeaderId": to_emp_id, "teamLeaderName": to_name}})
    
    dm_assignee_fields = ["revenueAssigneeId", "followerAssigneeId", "userRemarkAssigneeId", "clientRemarkAssigneeId"]
    for dm_field in dm_assignee_fields:
        await db.projects.update_many({dm_field: from_emp_id}, {"$set": {dm_field: to_emp_id}})

    for role_id_field in creative_roles:
        name_field = role_id_field[:-2] + "Name"
        await db.projects.update_many({role_id_field: from_emp_id}, {"$set": {role_id_field: to_emp_id, name_field: to_name}})

    async for proj in db.projects.find({"assignedTeamIds": from_emp_id}):
        updated_team_ids = [to_emp_id if tid == from_emp_id else tid for tid in proj.get("assignedTeamIds", [])]
        unique_team_ids = list(dict.fromkeys(updated_team_ids))
        await db.projects.update_one({"_id": proj["_id"]}, {"$set": {"assignedTeamIds": unique_team_ids}})

    # 3. Update active Tasks
    await db.wm_tasks.update_many(
        {"assignedToId": from_emp_id, "status": {"$ne": "completed"}},
        {"$set": {"assignedToId": to_emp_id, "assignedToName": to_name}}
    )

    await crud.log_activity(
        db,
        action="Employee Responsibilities Transferred",
        performedBy=actor_id,
        userName=actor_name,
        details=f"All work and responsibilities transferred from {from_name} to {to_name}."
    )

    await redis_manager.invalidate_namespace("hrms:employees")
    await redis_manager.invalidate_namespace("hrms:tasks")
    await redis_manager.invalidate_namespace("hrms:wm_tasks")
    await redis_manager.invalidate_namespace("hrms:work")

    return {"message": f"Successfully transferred all responsibilities from {from_name} to {to_name}."}


# Attendance Endpoints
@app.get("/attendance", response_model=List[schemas.Attendance])
@redis_manager.cached_api(namespace="hrms:attendance", ttl=60)
async def read_attendance(
    request: Request,
    userId: Optional[str] = None,
    role: Optional[str] = None,
    skip: int = 0, 
    limit: int = 10000, 
    db=Depends(get_db)
):
    is_admin = role and role.lower() in ["admin", "super admin", "hr"]
    return await crud.get_attendance(db, employee_id=None if is_admin else userId, skip=skip, limit=limit)

@app.get("/attendance/status/{employee_id}")
async def get_attendance_status(employee_id: str, db=Depends(get_db)):
    status = await crud.get_attendance_status(db, employee_id)
    return status if status else {"status": "Logged Out"}

@app.post("/attendance/punch-in/{employee_id}")
async def punch_in(employee_id: str, request: Request, payload: Optional[schemas.PunchInRequest] = None, db=Depends(get_db)):
    punch_in_time = payload.punch_in_time if payload else None
    performed_by, user_name = await get_actor_from_request(request, db)
    result = await crud.punch_in(
        db, 
        employee_id, 
        punch_in_time=punch_in_time, 
        performed_by=performed_by, 
        user_name=user_name,
        punch_in_activity_type=payload.activityType if payload else None,
        punch_in_activity_subtype=payload.activitySubtype if payload else None,
        punch_in_activity_value=payload.activityValue if payload else None,
        punch_in_task_id=payload.taskId if payload else None
    )
    if not result:
        raise HTTPException(status_code=400, detail="Punch in failed")
    await redis_manager.invalidate_namespace("hrms:attendance")
    return result

@app.post("/attendance", response_model=schemas.Attendance)
async def create_attendance(attendance: schemas.AttendanceCreate, db=Depends(get_db)):
    res = await crud.create_manual_attendance(db, attendance)
    await redis_manager.invalidate_namespace("hrms:attendance")
    return res

@app.put("/attendance/{attendance_id}", response_model=schemas.Attendance)
async def update_attendance(attendance_id: str, attendance_update: schemas.AttendanceUpdate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    updated = await crud.update_attendance(db, attendance_id, attendance_update, performed_by=performed_by, user_name=user_name)
    if not updated:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    await redis_manager.invalidate_namespace("hrms:attendance")
    return updated

@app.delete("/attendance/{attendance_id}")
async def delete_attendance(attendance_id: str, db=Depends(get_db)):
    success = await crud.delete_attendance(db, attendance_id)
    if not success:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    await redis_manager.invalidate_namespace("hrms:attendance")
    return {"message": "Attendance record deleted successfully"}

@app.post("/attendance/multi-delete")
async def delete_multiple_attendance(request: dict, db=Depends(get_db)):
    attendance_ids = request.get("ids", [])
    if not attendance_ids:
        raise HTTPException(status_code=400, detail="List of IDs required")
    await crud.delete_multiple_attendance(db, attendance_ids)
    await redis_manager.invalidate_namespace("hrms:attendance")
    return {"message": f"Deleted {len(attendance_ids)} records"}

@app.post("/attendance/punch-out/{employee_id}")
async def punch_out(employee_id: str, request: Request, payload: Optional[schemas.PunchOutRequest] = None, db=Depends(get_db)):
    punch_out_time = payload.punch_out_time if payload else None
    performed_by, user_name = await get_actor_from_request(request, db)
    result = await crud.punch_out(db, employee_id, punch_out_time=punch_out_time, performed_by=performed_by, user_name=user_name)
    if not result:
        raise HTTPException(status_code=400, detail="Punch out failed")
    await redis_manager.invalidate_namespace("hrms:attendance")
    return result

@app.post("/attendance/break-in/{employee_id}")
async def break_in(employee_id: str, db=Depends(get_db)):
    result = await crud.break_in(db, employee_id)
    if not result:
        raise HTTPException(status_code=400, detail="Break in failed")
    await redis_manager.invalidate_namespace("hrms:attendance")
    return result

@app.post("/attendance/break-out/{employee_id}")
async def break_out(employee_id: str, resume_task: bool = False, db=Depends(get_db)):
    result = await crud.break_out(db, employee_id, resume_task=resume_task)
    if not result:
        raise HTTPException(status_code=400, detail="Break out failed")
    await redis_manager.invalidate_namespace("hrms:attendance")
    return result

# Leave Endpoints
@app.get("/leaves", response_model=List[schemas.LeaveRequest])
@redis_manager.cached_api(namespace="hrms:leave", ttl=120)
async def read_leave_requests(request: Request, skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_all_leave_requests(db, skip=skip, limit=limit)

@app.get("/leaves/employee/{employee_id}", response_model=List[schemas.LeaveRequest])
@redis_manager.cached_api(namespace="hrms:leave", ttl=120)
async def read_user_leave_requests(request: Request, employee_id: str, skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_user_leave_requests(db, employee_id, skip=skip, limit=limit)

@app.post("/leaves", response_model=schemas.LeaveRequest)
async def create_leave_request(leave: schemas.LeaveRequestCreate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    res = await crud.create_leave_request(db, leave, performed_by=performed_by, user_name=user_name)
    await redis_manager.invalidate_namespace("hrms:leave")
    return res

@app.put("/leaves/{leave_id}", response_model=schemas.LeaveRequest)
async def update_leave_request(leave_id: str, leave_update: schemas.LeaveRequestUpdate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    res = await crud.update_leave_request(db, leave_id, leave_update.dict(exclude_unset=True), performed_by=performed_by, user_name=user_name)
    await redis_manager.invalidate_namespace("hrms:leave")
    return res

@app.patch("/leaves/{leave_id}/status", response_model=schemas.LeaveRequest)
async def update_leave_status(leave_id: str, update_data: schemas.LeaveRequestUpdate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    res = await crud.update_leave_request_status(
        db, 
        leave_id, 
        update_data.status, 
        update_data.approved_by, 
        update_data.approved_by_role,
        update_data.approved_by_id,
        update_data.approved_by_photo,
        update_data.reject_reason,
        update_data.approve_reason,
        performed_by=performed_by,
        user_name=user_name
    )
    await redis_manager.invalidate_namespace("hrms:leave")
    return res

@app.delete("/leaves/{leave_id}")
async def delete_leave_request(leave_id: str, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    success = await crud.delete_leave_request(db, leave_id, performed_by=performed_by, user_name=user_name)
    if not success:
        raise HTTPException(status_code=404, detail="Leave request not found")
    return {"message": "Leave request deleted successfully"}

# Announcement Endpoints
@app.get("/announcements", response_model=List[schemas.Announcement])
async def read_announcements(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_announcements(db, skip=skip, limit=limit)

# Dashboard Endpoints
@app.get("/dashboard-stats", response_model=schemas.DashboardStats)
async def read_dashboard_stats(db=Depends(get_db)):
    return await crud.get_dashboard_stats(db)

@app.get("/analytics/overview", response_model=schemas.AnalyticsOverview)
async def read_analytics_overview(months: int = 6, db=Depends(get_db)):
    return await crud.get_analytics_overview(db, months=months)

# Payroll Endpoints
@app.get("/payroll", response_model=List[schemas.Payroll])
async def read_payroll(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_payroll(db, skip=skip, limit=limit)

@app.post("/payroll", response_model=schemas.Payroll)
async def create_payroll(payroll: schemas.PayrollBase, db=Depends(get_db)):
    return await crud.create_item(db, "payroll", payroll.dict())

@app.post("/payroll/process")
async def process_payroll(request: dict, request_obj: Request, db=Depends(get_db)):
    # request should contain month and year
    month = request.get("month")
    year = request.get("year")
    if not month or not year:
        raise HTTPException(status_code=400, detail="Month and year required")
    performed_by, user_name = await get_actor_from_request(request_obj, db)
    return await crud.run_payroll_processing(db, month, year, performed_by=performed_by, user_name=user_name)

@app.put("/payroll/{payroll_id}", response_model=schemas.Payroll)
async def update_payroll(payroll_id: str, request: dict, db=Depends(get_db)):
    res = await crud.update_item(db, "payroll", payroll_id, request)
    if not res:
        raise HTTPException(status_code=404, detail="Payroll record not found")
    return res

@app.get("/salary-structures", response_model=List[schemas.SalaryStructure])
async def read_salary_structures(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_salary_structures(db, skip=skip, limit=limit)

@app.get("/salary-structures/{employee_id}", response_model=schemas.SalaryStructure)
async def read_salary_structure(employee_id: str, db=Depends(get_db)):
    res = await crud.get_salary_structure_by_employee(db, employee_id)
    if not res:
        raise HTTPException(status_code=404, detail="Salary structure not found")
    return res

@app.post("/salary-structures", response_model=schemas.SalaryStructure)
async def upsert_salary_structure(salary: schemas.SalaryStructureCreate, db=Depends(get_db)):
    return await crud.create_or_update_salary_structure(db, salary)

@app.get("/bonus-deductions", response_model=List[schemas.BonusDeduction])
async def read_bonus_deductions(month: Optional[str] = None, year: Optional[int] = None, db=Depends(get_db)):
    return await crud.get_bonus_deductions_with_remarks(db, month, year)

@app.post("/bonus-deductions", response_model=schemas.BonusDeduction)
async def create_bonus_deduction(item: schemas.BonusDeductionCreate, db=Depends(get_db)):
    return await crud.create_bonus_deduction(db, item)

@app.put("/bonus-deductions/{item_id}", response_model=schemas.BonusDeduction)
async def update_bonus_deduction(item_id: str, request: dict, db=Depends(get_db)):
    res = await crud.update_item(db, "bonus_deductions", item_id, request)
    if not res:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    return res

@app.delete("/bonus-deductions/{item_id}")
async def delete_bonus_deduction(item_id: str, db=Depends(get_db)):
    await crud.update_item(db, "bonus_deductions", item_id, {"status": "deleted"})
    return {"message": "Adjustment deleted successfully"}

# Notification Endpoints
@app.get("/notifications/{employee_id}", response_model=List[schemas.Notification])
async def read_notifications(employee_id: str, db=Depends(get_db)):
    return await crud.get_notifications_by_user(db, employee_id)

@app.post("/notifications", response_model=schemas.Notification)
async def create_notification(notification: schemas.NotificationCreate, db=Depends(get_db)):
    return await crud.create_notification(db, notification)

@app.put("/notifications/{notification_id}/read", response_model=schemas.Notification)
async def mark_notification_read(notification_id: str, db=Depends(get_db)):
    return await crud.mark_notification_as_read(db, notification_id)

@app.put("/notifications/read-all/{employee_id}")
async def mark_all_notifications_read(employee_id: str, db=Depends(get_db)):
    return await crud.mark_all_notifications_as_read(db, employee_id)

# Department Endpoints
@app.get("/departments", response_model=List[schemas.Department])
@redis_manager.cached_api(namespace="hrms:settings", ttl=600)
async def read_departments(request: Request, skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_departments(db, skip=skip, limit=limit)

@app.post("/departments", response_model=schemas.Department)
async def create_department(department: schemas.DepartmentCreate, db=Depends(get_db)):
    res = await crud.create_department(db, department)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.put("/departments/{department_id}", response_model=schemas.Department)
async def update_department(department_id: str, department_update: schemas.DepartmentUpdate, db=Depends(get_db)):
    res = await crud.update_department(db, department_id, department_update)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.delete("/departments/{department_id}")
async def delete_department(department_id: str, db=Depends(get_db)):
    await crud.delete_department(db, department_id)
    await redis_manager.invalidate_namespace("hrms:settings")
    return {"message": "Department deleted successfully"}

# SubDepartment Endpoints
@app.get("/sub-departments", response_model=List[schemas.SubDepartment])
@redis_manager.cached_api(namespace="hrms:settings", ttl=600)
async def read_sub_departments(request: Request, skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_sub_departments(db, skip=skip, limit=limit)

@app.post("/sub-departments", response_model=schemas.SubDepartment)
async def create_sub_department(sub_department: schemas.SubDepartmentCreate, db=Depends(get_db)):
    res = await crud.create_sub_department(db, sub_department)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.put("/sub-departments/{sub_department_id}", response_model=schemas.SubDepartment)
async def update_sub_department(sub_department_id: str, sub_department_update: schemas.SubDepartmentUpdate, db=Depends(get_db)):
    res = await crud.update_sub_department(db, sub_department_id, sub_department_update)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.delete("/sub-departments/{sub_department_id}")
async def delete_sub_department(sub_department_id: str, db=Depends(get_db)):
    await crud.delete_sub_department(db, sub_department_id)
    await redis_manager.invalidate_namespace("hrms:settings")
    return {"message": "SubDepartment deleted successfully"}

# Designation Endpoints
@app.get("/designations", response_model=List[schemas.Designation])
@redis_manager.cached_api(namespace="hrms:settings", ttl=600)
async def read_designations(request: Request, skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_designations(db, skip=skip, limit=limit)

@app.post("/designations", response_model=schemas.Designation)
async def create_designation(designation: schemas.DesignationCreate, db=Depends(get_db)):
    res = await crud.create_designation(db, designation)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.put("/designations/{designation_id}", response_model=schemas.Designation)
async def update_designation(designation_id: str, designation_update: schemas.DesignationUpdate, db=Depends(get_db)):
    res = await crud.update_designation(db, designation_id, designation_update)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.delete("/designations/{designation_id}")
async def delete_designation(designation_id: str, db=Depends(get_db)):
    await crud.delete_designation(db, designation_id)
    await redis_manager.invalidate_namespace("hrms:settings")
    return {"message": "Designation deleted successfully"}

# Configuration Endpoints (Companies, Roles, Relations)
@app.get("/companies", response_model=List[schemas.Company])
@redis_manager.cached_api(namespace="hrms:settings", ttl=600)
async def read_companies(request: Request, skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_companies(db, skip, limit)
@app.post("/companies", response_model=schemas.Company)
async def create_company(company: schemas.CompanyCreate, db=Depends(get_db)):
    res = await crud.create_company(db, company)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.get("/roles", response_model=List[schemas.Role])
@redis_manager.cached_api(namespace="hrms:settings", ttl=600)
async def read_roles(request: Request, skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_roles(db, skip, limit)
@app.post("/roles", response_model=schemas.Role)
async def create_role(role: schemas.RoleCreate, db=Depends(get_db)):
    res = await crud.create_role(db, role)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res

@app.get("/relations", response_model=List[schemas.Relation])
@redis_manager.cached_api(namespace="hrms:settings", ttl=600)
async def read_relations(request: Request, skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_relations(db, skip, limit)
@app.post("/relations", response_model=schemas.Relation)
async def create_relation(relation: schemas.RelationCreate, db=Depends(get_db)):
    res = await crud.create_relation(db, relation)
    await redis_manager.invalidate_namespace("hrms:settings")
    return res


# Recruitment Endpoints
@app.get("/job-openings", response_model=List[schemas.JobOpening])
async def read_job_openings(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_job_openings(db, skip, limit)
@app.post("/job-openings", response_model=schemas.JobOpening)
async def create_job_opening(job: schemas.JobOpeningCreate, db=Depends(get_db)): return await crud.create_job_opening(db, job)
@app.put("/job-openings/{job_id}", response_model=schemas.JobOpening)
async def update_job_opening(job_id: str, job_update: schemas.JobOpeningUpdate, db=Depends(get_db)):
    return await crud.update_job_opening(db, job_id, job_update)
@app.delete("/job-openings/{job_id}")
async def delete_job_opening(job_id: str, db=Depends(get_db)):
    await crud.delete_job_opening(db, job_id)
    return {"message": "Job opening deleted successfully"}

@app.get("/applications", response_model=List[schemas.Application])
async def read_applications(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_applications(db, skip, limit)
@app.post("/applications", response_model=schemas.Application)
async def create_application(app: schemas.ApplicationCreate, db=Depends(get_db)): return await crud.create_application(db, app)
@app.put("/applications/{app_id}", response_model=schemas.Application)
async def update_application(app_id: str, app_update: schemas.ApplicationUpdate, db=Depends(get_db)):
    return await crud.update_application(db, app_id, app_update)
@app.delete("/applications/{app_id}")
async def delete_application(app_id: str, db=Depends(get_db)):
    await crud.delete_application(db, app_id)
    return {"message": "Application deleted successfully"}

@app.get("/applications/{app_id}/logs")
async def read_application_logs(app_id: str, db=Depends(get_db)):
    return await crud.get_application_logs(db, app_id)

@app.get("/interns", response_model=List[schemas.Intern])
async def read_interns(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_interns(db, skip, limit)
@app.post("/interns", response_model=schemas.Intern)
async def create_intern(intern: schemas.InternCreate, db=Depends(get_db)): return await crud.create_intern(db, intern)

# Referral / Job Reference Endpoints
@app.get("/referrals", response_model=List[schemas.Referral])
async def read_referrals(employee_id: Optional[str] = None, db=Depends(get_db)):
    if employee_id:
        return await crud.get_employee_referrals(db, employee_id)
    return await crud.get_referrals(db)

@app.post("/referrals", response_model=schemas.Referral)
async def create_referral(referral: schemas.ReferralCreate, db=Depends(get_db)):
    return await crud.create_referral(db, referral)

@app.put("/referrals/{referral_id}", response_model=schemas.Referral)
async def update_referral(referral_id: str, referral_update: schemas.ReferralUpdate, db=Depends(get_db)):
    return await crud.update_referral(db, referral_id, referral_update)

@app.delete("/referrals/{referral_id}")
async def delete_referral(referral_id: str, db=Depends(get_db)):
    await crud.delete_referral(db, referral_id)
    return {"message": "Referral deleted successfully"}

# Asset & Expense Endpoints
@app.get("/assets", response_model=List[schemas.Asset])
async def read_assets(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_assets(db, skip, limit)
@app.post("/assets", response_model=schemas.Asset)
async def create_asset(asset: schemas.AssetCreate, db=Depends(get_db)): return await crud.create_asset(db, asset)
@app.put("/assets/{asset_id}", response_model=schemas.Asset)
async def update_asset(asset_id: str, asset_update: schemas.AssetUpdate, db=Depends(get_db)): return await crud.update_asset(db, asset_id, asset_update)
@app.delete("/assets/{asset_id}")
async def delete_asset(asset_id: str, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    await crud.delete_asset(db, asset_id, performedBy=performedBy, userName=userName)
    return {"message": "Asset deleted successfully"}

@app.delete("/assets/by-category/{category_name}")
async def delete_assets_by_category(category_name: str, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    """Delete all assets belonging to a specific category (handles both 'category' and 'type' fields)."""
    result = await db.assets.delete_many({
        "$or": [
            {"category": category_name},
            {"type": category_name}
        ]
    })
    return {"deleted": result.deleted_count, "category": category_name}

@app.get("/assets/logs")
async def read_asset_logs(asset_id: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_asset_logs(db, asset_id)

# Asset Category Endpoints
@app.get("/asset-categories", response_model=List[schemas.AssetCategory])
async def read_asset_categories(skip: int = 0, limit: int = 100, db=Depends(get_db)):
    return await crud.get_asset_categories(db, skip, limit)

@app.post("/asset-categories", response_model=schemas.AssetCategory)
async def create_asset_category(category: schemas.AssetCategoryCreate, db=Depends(get_db)):
    return await crud.create_asset_category(db, category)

@app.put("/asset-categories/{category_id}", response_model=schemas.AssetCategory)
async def update_asset_category(category_id: str, category_update: schemas.AssetCategoryUpdate, db=Depends(get_db)):
    return await crud.update_asset_category(db, category_id, category_update)

@app.delete("/asset-categories/{category_id}")
async def delete_asset_category(category_id: str, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    await crud.delete_asset_category(db, category_id, performed_by=performedBy, user_name=userName)
    return {"message": "Category deleted successfully"}

@app.get("/asset-categories/logs")
async def read_category_logs(category_id: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_category_logs(db, category_id)

@app.get("/expense-claims", response_model=List[schemas.ExpenseClaim])
async def read_expense_claims(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_expense_claims(db, skip, limit)
@app.post("/expense-claims", response_model=schemas.ExpenseClaim)
async def create_expense_claim(claim: schemas.ExpenseClaimCreate, db=Depends(get_db)): return await crud.create_expense_claim(db, claim)

# Holiday & Performance Endpoints
@app.get("/holidays", response_model=List[schemas.Holiday])
async def read_holidays(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_holidays(db, skip, limit)
@app.post("/holidays", response_model=schemas.Holiday)
async def create_holiday(holiday: schemas.HolidayCreate, db=Depends(get_db)): return await crud.create_holiday(db, holiday)
@app.put("/holidays/{holiday_id}", response_model=schemas.Holiday)
async def update_holiday(holiday_id: str, holiday: schemas.HolidayUpdate, db=Depends(get_db)): return await crud.update_holiday(db, holiday_id, holiday)
@app.delete("/holidays/{holiday_id}")
async def delete_holiday(holiday_id: str, db=Depends(get_db)):
    await crud.delete_holiday(db, holiday_id)
    return {"message": "Holiday deleted"}

@app.delete("/holidays")
async def delete_all_holidays(db=Depends(get_db)):
    await crud.delete_all_holidays(db)
    return {"message": "All holidays deleted"}

import urllib.request

GOOGLE_ICS_MAP = {
    "IN": "en.indian#holiday@group.v.calendar.google.com",
    "US": "en.usa#holiday@group.v.calendar.google.com",
    "GB": "en.uk#holiday@group.v.calendar.google.com",
    "AU": "en.australian#holiday@group.v.calendar.google.com",
    "CA": "en.canadian#holiday@group.v.calendar.google.com"
}

@app.get("/holidays/fetch-external")
async def fetch_external_holidays(country: str = "IN", year: int = 2026):
    try:
        fetched = []
        if country in GOOGLE_ICS_MAP:
            calendar_id = GOOGLE_ICS_MAP[country]
            url = f"https://calendar.google.com/calendar/ical/{calendar_id.replace('#', '%23').replace('@', '%40')}/public/basic.ics"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                content = response.read().decode('utf-8')
            
            current_event = {}
            for line in content.splitlines():
                if line.startswith('BEGIN:VEVENT'):
                    current_event = {}
                elif line.startswith('DTSTART;VALUE=DATE:'):
                    date_str = line.split(':')[1]
                    if date_str.startswith(str(year)):
                        current_event['date'] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                elif line.startswith('SUMMARY:'):
                    current_event['name'] = line.split(':', 1)[1]
                elif line.startswith('END:VEVENT'):
                    if 'date' in current_event and 'name' in current_event:
                        # Avoid duplicates on the exact same date and name
                        if not any(f['date'] == current_event['date'] and f['name'] == current_event['name'] for f in fetched):
                            fetched.append({
                                "date": current_event['date'],
                                "name": current_event['name'],
                                "type": "National",
                                "company": ""
                            })
        else:
            # Fallback to python holidays package
            country_holidays = pyholidays.country_holidays(country, years=year)
            for date, name in country_holidays.items():
                fetched.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "name": name,
                    "type": "National",
                    "company": ""
                })
        
        # Sort by date
        fetched.sort(key=lambda x: x["date"])
        return fetched
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error fetching holidays: {str(e)}")

@app.post("/holidays/bulk")
async def create_holidays_bulk(payload: schemas.HolidayBulkCreate, db=Depends(get_db)):
    return await crud.create_holidays_bulk(db, payload)

@app.get("/kpi-records", response_model=List[schemas.KPI])
async def read_kpi_records(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_kpi_records(db, skip, limit)
@app.post("/kpi-records", response_model=schemas.KPI)
async def create_kpi_record(kpi: schemas.KPICreate, db=Depends(get_db)): return await crud.create_kpi_record(db, kpi)

@app.get("/reviews", response_model=List[schemas.Review])
async def read_reviews(employeeId: Optional[str] = None, skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_reviews(db, employeeId, skip, limit)
@app.post("/reviews", response_model=schemas.Review)
async def create_review(review: schemas.ReviewCreate, db=Depends(get_db)): return await crud.create_review(db, review)
@app.put("/reviews/{review_id}", response_model=schemas.Review)
async def update_review(review_id: str, update: schemas.ReviewUpdate, db=Depends(get_db)): return await crud.update_review(db, review_id, update)
@app.delete("/reviews/{review_id}")
async def delete_review(review_id: str, db=Depends(get_db)): return await crud.delete_review(db, review_id)

@app.get("/remarks", response_model=List[schemas.Remark])
async def read_remarks(skip: int = 0, limit: int = 10000, db=Depends(get_db)): return await crud.get_remarks(db, skip, limit)
@app.post("/remarks", response_model=schemas.Remark)
async def create_remark(remark: schemas.RemarkCreate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    return await crud.create_remark(db, remark, performed_by=performed_by, user_name=user_name)
@app.put("/remarks/{remark_id}", response_model=schemas.Remark)
async def update_remark(remark_id: str, update: schemas.RemarkUpdate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    return await crud.update_remark(db, remark_id, update, performed_by=performed_by, user_name=user_name)
@app.delete("/remarks/{remark_id}")
async def delete_remark(remark_id: str, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    await crud.delete_remark(db, remark_id, performed_by=performed_by, user_name=user_name)
    return {"message": "Remark soft-deleted successfully"}

@app.post("/remarks/{remark_id}/restore")
async def restore_remark(remark_id: str, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    await crud.restore_remark(db, remark_id, performed_by=performed_by, user_name=user_name)
    return {"message": "Remark restored successfully"}

@app.delete("/remarks/{remark_id}/permanent")
async def permanently_delete_remark(remark_id: str, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    await crud.permanently_delete_remark(db, remark_id, performed_by=performed_by, user_name=user_name)
    return {"message": "Remark permanently deleted"}

# Penalty Type Endpoints
@app.get("/penalty-types", response_model=List[schemas.PenaltyType])
async def read_penalty_types(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_penalty_types(db, skip=skip, limit=limit)

@app.post("/penalty-types", response_model=schemas.PenaltyType)
async def create_penalty_type(penalty_type: schemas.PenaltyTypeCreate, db=Depends(get_db)):
    return await crud.create_penalty_type(db, penalty_type)

@app.put("/penalty-types/{penalty_id}", response_model=schemas.PenaltyType)
async def update_penalty_type(penalty_id: str, penalty_update: schemas.PenaltyTypeUpdate, db=Depends(get_db)):
    return await crud.update_penalty_type(db, penalty_id, penalty_update)

@app.delete("/penalty-types/{penalty_id}")
async def delete_penalty_type(penalty_id: str, db=Depends(get_db)):
    await crud.delete_penalty_type(db, penalty_id)
    return {"message": "Penalty type deleted successfully"}

# Event Endpoints
@app.get("/events", response_model=List[schemas.Event])
async def read_events(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    events = await crud.get_events(db, skip, limit)
    holidays = await crud.get_holidays(db, 0, 1000)
    for h in holidays:
        title = h["name"] if "Holiday" in h["name"] else f"{h['name']} (Holiday)"
        events.append({
            "id": h["id"],
            "title": title,
            "description": f"{h.get('type', 'Public')} Holiday",
            "date": h["date"],
            "time": "All Day",
            "type": "Holiday"
        })
    # Sort by date — normalize mixed types (datetime, date, str, None) to comparable strings
    def _sort_key(x):
        d = x.get("date") if isinstance(x, dict) else getattr(x, "date", None)
        if d is None:
            return ""
        if hasattr(d, "isoformat"):
            return d.isoformat()
        return str(d)
    events.sort(key=_sort_key)
    return events
@app.post("/events", response_model=schemas.Event)
async def create_event(event: schemas.EventCreate, db=Depends(get_db)): return await crud.create_event(db, event)
@app.put("/events/{event_id}", response_model=schemas.Event)
async def update_event(event_id: str, event_update: schemas.EventUpdate, db=Depends(get_db)): return await crud.update_event(db, event_id, event_update)
@app.delete("/events/{event_id}")
async def delete_event(event_id: str, db=Depends(get_db)): return await crud.delete_event(db, event_id)

# Client Endpoints
@app.get("/clients", response_model=List[schemas.Client])
@redis_manager.cached_api(namespace="hrms:work", ttl=180)
async def read_clients(request: Request, skip: int = 0, limit: int = 10000, userId: str = None, role: str = None, db=Depends(get_db)):
    user_info = {"sub": userId, "role": role} if userId else None
    return await crud.get_clients(db, skip=skip, limit=limit, user_info=user_info)

@app.get("/clients/{client_id}", response_model=schemas.Client)
async def read_client(client_id: str, db=Depends(get_db)):
    client = await crud.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client

@app.post("/clients", response_model=schemas.Client)
async def create_client(client: schemas.ClientCreate, db=Depends(get_db)):
    res = await crud.create_client(db, client=client)
    await redis_manager.invalidate_namespace("hrms:work")
    return res

@app.put("/clients/{client_id}", response_model=schemas.Client)
async def update_client(client_id: str, client_update: schemas.ClientUpdate, db=Depends(get_db)):
    print("DEBUG: update_client incoming payload:", client_update.dict(exclude_unset=True))
    res = await crud.update_client(db, client_id, client_update)
    await redis_manager.invalidate_namespace("hrms:work")
    return res

@app.post("/clients/{client_id}/meetings", response_model=schemas.Client)
async def add_client_meeting(client_id: str, meeting: schemas.Meeting, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    res = await crud.add_client_meeting(db, client_id, meeting, performedBy=performedBy, userName=userName)
    await redis_manager.invalidate_namespace("hrms:work")
    return res

@app.put("/clients/{client_id}/meetings/{meeting_idx}", response_model=schemas.Client)
async def update_client_meeting(client_id: str, meeting_idx: int, meeting: schemas.Meeting, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    res = await crud.update_client_meeting(db, client_id, meeting_idx, meeting, performedBy=performedBy, userName=userName)
    await redis_manager.invalidate_namespace("hrms:work")
    return res

@app.delete("/clients/{client_id}/meetings/{meeting_idx}", response_model=schemas.Client)
async def delete_client_meeting(client_id: str, meeting_idx: int, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    res = await crud.delete_client_meeting(db, client_id, meeting_idx, performedBy=performedBy, userName=userName)
    await redis_manager.invalidate_namespace("hrms:work")
    return res

@app.delete("/clients/{client_id}")
async def delete_client(client_id: str, db=Depends(get_db)):
    success = await crud.delete_client(db, client_id)
    if not success:
        raise HTTPException(status_code=404, detail="Client not found")
    await redis_manager.invalidate_namespace("hrms:work")
    return {"message": "Client deleted successfully"}

# Project Endpoints
@app.get("/projects")
@redis_manager.cached_api(namespace="hrms:work", ttl=180)
async def read_projects(request: Request, userId: Optional[str] = None, role: Optional[str] = None, skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_projects(db, userId=userId, role=role, skip=skip, limit=limit)

@app.post("/projects", response_model=schemas.Project)
async def create_project(project: schemas.ProjectCreate, db=Depends(get_db)):
    res = await crud.create_project(db, project=project)
    await redis_manager.invalidate_namespace("hrms:work")
    return res

@app.put("/projects/{project_id}", response_model=schemas.Project)
async def update_project(project_id: str, project_update: schemas.ProjectUpdate, db=Depends(get_db)):
    res = await crud.update_project(db, project_id, project_update)
    await redis_manager.invalidate_namespace("hrms:work")
    return res

@app.delete("/projects/{project_id}")
async def delete_project(project_id: str, db=Depends(get_db)):
    success = await crud.delete_project(db, project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    await redis_manager.invalidate_namespace("hrms:work")
    return {"message": "Project deleted successfully"}

@app.put("/projects/{project_id}/modules/notebook", response_model=schemas.Project)
async def update_module_notebook(project_id: str, payload: schemas.ModuleNotebookUpdate, db=Depends(get_db)):
    updated = await crud.update_module_notebook(db, project_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Project or module not found")
    return updated

@app.post("/projects/{project_id}/modules/comments", response_model=schemas.Project)
async def add_module_comment(project_id: str, payload: schemas.ModuleCommentCreate, db=Depends(get_db)):
    updated = await crud.add_module_comment(db, project_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Project or module not found")
    return updated

@app.post("/projects/{project_id}/finance-follow-ups", response_model=schemas.Project)
async def add_project_finance_follow_up(project_id: str, follow_up: schemas.FinanceFollowUp, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    return await crud.add_project_finance_follow_up(db, project_id, follow_up, performedBy=performedBy, userName=userName)


# WM Task Endpoints
# General Task Endpoints
@app.get("/tasks", response_model=List[schemas.Task])
@redis_manager.cached_api(namespace="hrms:tasks", ttl=60)
async def get_tasks_api(request: Request, userId: str = None, role: str = None, skip: int = 0, limit: int = 100, db=Depends(get_db)):
    return await crud.get_tasks(db, userId, role, skip, limit)

@app.post("/tasks", response_model=schemas.Task)
async def create_task_api(task: schemas.TaskCreate, db=Depends(get_db)):
    new_task = await crud.create_task(db, task)
    await redis_manager.invalidate_namespace("hrms:tasks")
    await ws_manager.broadcast_all("task_update", {"taskId": str(new_task.get("id"))})
    return new_task

@app.put("/tasks/{task_id}", response_model=schemas.Task)
async def update_task_api(task_id: str, task: schemas.TaskUpdate, db=Depends(get_db)):
    updated = await crud.update_task(db, task_id, task)
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    await redis_manager.invalidate_namespace("hrms:tasks")
    await ws_manager.broadcast_all("task_update", {"taskId": task_id})
    return updated

@app.delete("/tasks/{task_id}")
async def delete_task_api(task_id: str, db=Depends(get_db)):
    success = await crud.delete_task(db, task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    await redis_manager.invalidate_namespace("hrms:tasks")
    await ws_manager.broadcast_all("task_update", {"taskId": task_id})
    return {"message": "Task deleted successfully"}

@app.get("/tasks/{task_id}/activities")
async def read_task_activities(task_id: str, db=Depends(get_db)):
    return await crud.get_task_activities(db, task_id)

@app.get("/dev-board-data")
@redis_manager.cached_api(namespace="hrms:tasks", ttl=60)
async def get_dev_board_data(
    request: Request,
    userId: Optional[str] = None, 
    role: Optional[str] = None,
    db=Depends(get_db)
):
    import asyncio
    wm_tasks_coro = crud.get_wm_tasks(db, userId, role, skip=0, limit=10000)
    projects_coro = crud.get_projects(db, userId, role, skip=0, limit=10000)
    employees_coro = crud.get_employees(db, skip=0, limit=10000, include_inactive=False)
    
    transfer_all_coro = crud.get_all_transfer_requests(db, None, "wm-task")
    transfer_out_coro = crud.get_outgoing_transfer_requests(db, userId or "", "wm-task")
    
    results = await asyncio.gather(
        wm_tasks_coro,
        projects_coro,
        employees_coro,
        transfer_all_coro,
        transfer_out_coro
    )
    
    projects = [
        {
            "id": p.get("id"),
            "title": p.get("title"),
            "department": p.get("department"),
            "status": p.get("status"),
            "teamLeaderId": p.get("teamLeaderId"),
            "isPhaseWise": p.get("isPhaseWise"),
            "phases": p.get("phases")
        } for p in results[1]
    ]
    
    employees = [
        {
            "id": e.get("id"),
            "firstName": e.get("firstName"),
            "lastName": e.get("lastName"),
            "name": e.get("name"),
            "department": e.get("department")
        } for e in results[2]
    ]
    
    return {
        "wmTasks": results[0],
        "projects": projects,
        "employees": employees,
        "transferRequestsAll": results[3],
        "transferRequestsOutgoing": results[4]
    }

# --- Clubbed Page Data Endpoints ---
# These endpoints reduce multiple frontend API calls into a single request per page.

@app.get("/dashboard-data")
@redis_manager.cached_api(namespace="hrms:dashboard", ttl=60)
async def get_dashboard_data(
    request: Request,
    userId: Optional[str] = None,
    role: Optional[str] = None,
    db=Depends(get_db)
):
    """Clubbed endpoint for the main Dashboard page. Replaces 7 separate calls."""
    import asyncio
    is_admin = role and role.lower() in ["admin", "super admin", "hr"]
    
    coros = [
        crud.get_system_settings(db),
        crud.get_all_leave_requests(db, skip=0, limit=10000),
    ]
    
    # Admin-only data
    if is_admin:
        coros.extend([
            crud.get_employees(db, skip=0, limit=10000),
            crud.get_interns(db, skip=0, limit=10000),
            crud.get_attendance(db, skip=0, limit=10000),
            crud.get_applications(db, skip=0, limit=10000),
            crud.get_assets(db, skip=0, limit=10000),
        ])
    
    results = await asyncio.gather(*coros)
    
    response = {
        "systemSettings": results[0],
        "leaves": results[1],
    }
    
    if is_admin:
        employees = [
            {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
             "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
             "role": e.get("role"), "email": e.get("email"), "photoUrl": e.get("photoUrl"),
             "status": e.get("status"), "joiningDate": e.get("joiningDate")}
            for e in results[2]
        ]
        response["employees"] = employees
        response["interns"] = results[3]
        response["attendance"] = results[4]
        response["applications"] = results[5]
        response["assets"] = results[6]
    
    return response

@app.get("/attendance-page-data")
@redis_manager.cached_api(namespace="hrms:attendance", ttl=60)
async def get_attendance_page_data(
    request: Request,
    userId: Optional[str] = None,
    role: Optional[str] = None,
    db=Depends(get_db)
):
    """Clubbed endpoint for the Attendance page. Replaces 4 separate calls."""
    import asyncio
    is_admin = role and role.lower() in ["admin", "super admin", "hr"]
    
    coros = [
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_attendance(db, employee_id=None if is_admin else userId, skip=0, limit=10000),
        crud.get_system_settings(db),
    ]
    
    if is_admin:
        coros.append(crud.get_time_recoveries(db, skip=0, limit=10000))
    elif userId:
        coros.append(crud.get_employee_time_recoveries(db, userId))
    
    results = await asyncio.gather(*coros)
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
         "role": e.get("role"), "email": e.get("email"), "photoUrl": e.get("photoUrl"),
         "status": e.get("status")}
        for e in results[0]
    ]
    
    return {
        "employees": employees,
        "attendance": results[1],
        "systemSettings": results[2],
        "recoveryRequests": results[3] if len(results) > 3 else [],
    }

@app.get("/hr-tasks-data")
@redis_manager.cached_api(namespace="hrms:tasks", ttl=60)
async def get_hr_tasks_data(request: Request, db=Depends(get_db)):
    """Clubbed endpoint for the HR Tasks page. Replaces 4 separate calls."""
    import asyncio
    results = await asyncio.gather(
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_tasks(db, skip=0, limit=10000),
        crud.get_all_leave_requests(db, skip=0, limit=10000),
        crud.get_document_requests(db),
    )
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
         "role": e.get("role")}
        for e in results[0]
    ]
    
    return {
        "employees": employees,
        "tasks": results[1],
        "leaves": results[2],
        "documentRequests": results[3],
    }

@app.get("/leave-page-data")
@redis_manager.cached_api(namespace="hrms:leave", ttl=60)
async def get_leave_page_data(
    request: Request,
    userId: Optional[str] = None,
    db=Depends(get_db)
):
    """Clubbed endpoint for the Leave page. Replaces 4 separate calls."""
    import asyncio
    
    leaves_coro = crud.get_user_leave_requests(db, userId, skip=0, limit=10000) if userId else crud.get_all_leave_requests(db, skip=0, limit=10000)
    
    results = await asyncio.gather(
        leaves_coro,
        crud.get_holidays(db, skip=0, limit=10000),
        crud.get_companies(db, skip=0, limit=10000),
        crud.get_system_settings(db),
    )
    
    return {
        "leaves": results[0],
        "holidays": results[1],
        "companies": results[2],
        "systemSettings": results[3],
    }

@app.get("/sales-page-data")
@redis_manager.cached_api(namespace="hrms:sales", ttl=60)
async def get_sales_page_data(request: Request, db=Depends(get_db)):
    """Clubbed endpoint for the Sales page. Replaces 5 separate calls."""
    import asyncio
    results = await asyncio.gather(
        crud.get_leads(db, skip=0, limit=10000),
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_sales_targets(db),
        crud.get_incentive_slabs(db),
        crud.get_system_settings(db),
    )
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
         "role": e.get("role"), "photoUrl": e.get("photoUrl")}
        for e in results[1]
    ]
    
    return {
        "leads": results[0],
        "employees": employees,
        "salesTargets": results[2],
        "incentiveSlabs": results[3],
        "systemSettings": results[4],
    }

@app.get("/work-logs-data")
@redis_manager.cached_api(namespace="hrms:work", ttl=60)
async def get_work_logs_data(request: Request, db=Depends(get_db)):
    """Clubbed endpoint for the Work Logs page. Replaces 3 separate calls."""
    import asyncio
    results = await asyncio.gather(
        crud.get_attendance(db, skip=0, limit=10000),
        crud.get_wm_tasks(db, skip=0, limit=10000),
        crud.get_employees(db, skip=0, limit=10000),
    )
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
         "role": e.get("role")}
        for e in results[2]
    ]
    
    return {
        "attendance": results[0],
        "wmTasks": results[1],
        "employees": employees,
    }

@app.get("/research-page-data")
@redis_manager.cached_api(namespace="hrms:work", ttl=60)
async def get_research_page_data(
    request: Request,
    userId: Optional[str] = None,
    role: Optional[str] = None,
    db=Depends(get_db)
):
    """Clubbed endpoint for the Research page. Replaces 4 separate calls."""
    import asyncio
    is_admin = role and role.lower() in ["admin", "super admin"]
    
    coros = [
        crud.get_research(db, userId or "", is_admin),
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_projects(db, skip=0, limit=10000),
    ]
    
    if userId:
        coros.append(crud.get_attendance_status(db, userId))
    
    results = await asyncio.gather(*coros)
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department")}
        for e in results[1]
    ]
    
    projects = [
        {"id": p.get("id"), "title": p.get("title"), "department": p.get("department"),
         "status": p.get("status")}
        for p in results[2]
    ]
    
    return {
        "research": results[0],
        "employees": employees,
        "projects": projects,
        "attendanceStatus": results[3] if len(results) > 3 else None,
    }

@app.get("/projects-page-data")
@redis_manager.cached_api(namespace="hrms:work", ttl=60)
async def get_projects_page_data(
    request: Request,
    userId: Optional[str] = None,
    role: Optional[str] = None,
    db=Depends(get_db)
):
    """Clubbed endpoint for the Projects page. Replaces 5 separate calls."""
    import asyncio
    results = await asyncio.gather(
        crud.get_projects(db, userId, role, skip=0, limit=10000),
        crud.get_wm_tasks(db, userId, role, skip=0, limit=10000),
        crud.get_leads(db, skip=0, limit=10000),
        crud.get_clients(db, skip=0, limit=10000),
        crud.get_employees(db, skip=0, limit=10000),
    )
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
         "role": e.get("role")}
        for e in results[4]
    ]
    
    return {
        "projects": results[0],
        "wmTasks": results[1],
        "leads": results[2],
        "clients": results[3],
        "employees": employees,
    }

@app.get("/my-tasks-page-data")
@redis_manager.cached_api(namespace="hrms:tasks", ttl=60)
async def get_my_tasks_page_data(
    request: Request,
    userId: Optional[str] = None,
    role: Optional[str] = None,
    db=Depends(get_db)
):
    """Clubbed endpoint for the My Tasks page. Replaces 3 separate calls."""
    import asyncio
    results = await asyncio.gather(
        crud.get_tasks(db, userId, role, skip=0, limit=10000, exclude_department="HR"),
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_departments(db, skip=0, limit=10000)
    )
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "email": e.get("email"), "department": e.get("department"), 
         "designation": e.get("designation")}
        for e in results[1]
    ]
    
    return {
        "tasks": results[0],
        "employees": employees,
        "departments": results[2]
    }

@app.get("/employee-attendance-page-data")
@redis_manager.cached_api(namespace="hrms:attendance", ttl=60)
async def get_employee_attendance_page_data(request: Request, db=Depends(get_db)):
    """Clubbed endpoint for the Employee Attendance page. Replaces 6 separate calls."""
    import asyncio
    results = await asyncio.gather(
        crud.get_attendance(db, skip=0, limit=10000),
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_departments(db, skip=0, limit=10000),
        crud.get_system_settings(db),
        crud.get_time_recoveries(db, skip=0, limit=10000),
        crud.get_all_leave_requests(db, skip=0, limit=10000)
    )
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
         "role": e.get("role"), "email": e.get("email"), "photoUrl": e.get("photoUrl"),
         "joiningDate": e.get("joiningDate"), "status": e.get("status")}
        for e in results[1]
    ]
    
    return {
        "attendance": results[0],
        "employees": employees,
        "departments": results[2],
        "systemSettings": results[3],
        "timeRecovery": results[4],
        "leaves": results[5]
    }

@app.get("/my-tasks-view-data")
@redis_manager.cached_api(namespace="hrms:tasks", ttl=60)
async def get_my_tasks_view_data(
    request: Request,
    userId: Optional[str] = None,
    role: Optional[str] = None,
    db=Depends(get_db)
):
    """Clubbed endpoint for the MyTasksView component. Replaces 8 separate calls."""
    from datetime import datetime, timedelta
    start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    results = await asyncio.gather(
        crud.get_tasks(db, userId, role, skip=0, limit=10000),
        crud.get_wm_tasks(db, userId, role, skip=0, limit=10000),
        crud.get_all_content_calendar_entries(db),
        crud.get_all_other_work(db),
        crud.get_projects(db, skip=0, limit=10000),
        crud.get_clients(db, skip=0, limit=10000),
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_leads(db, skip=0, limit=10000),
        db.marketing_daily_reports.find({"date": {"$gte": start_date, "$lte": end_date}}).to_list(10000),
        db.marketing_project_daily_remarks.find({"date": {"$gte": start_date, "$lte": end_date}}).to_list(10000)
    )
    
    employees = [
        {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
         "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
         "role": e.get("role"), "email": e.get("email")}
        for e in results[6]
    ]
    
    return {
        "tasks": results[0],
        "wmTasks": results[1],
        "contentCalendar": results[2],
        "otherWork": results[3],
        "projects": results[4],
        "clients": results[5],
        "employees": employees,
        "leads": results[7],
        "dailyReports": [crud.fix_id(r) for r in results[8]],
        "projectRemarks": [crud.fix_id(r) for r in results[9]]
    }

@app.get("/wm-tasks", response_model=List[schemas.WMTask])
@redis_manager.cached_api(namespace="hrms:wm_tasks", ttl=60)
async def read_wm_tasks(request: Request, userId: Optional[str] = None, role: Optional[str] = None, skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_wm_tasks(db, userId=userId, role=role, skip=skip, limit=limit)

@app.get("/wm-tasks/{task_id}", response_model=schemas.WMTask)
@redis_manager.cached_api(namespace="hrms:wm_tasks", ttl=60)
async def read_wm_task(task_id: str, request: Request, db=Depends(get_db)):
    task = await crud.get_wm_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.post("/wm-tasks", response_model=schemas.WMTask)
async def create_wm_task(task: schemas.WMTaskCreate, db=Depends(get_db)):
    if task.status == "pending" and (not task.reasonForPending or not task.reasonForPending.strip()):
        raise HTTPException(status_code=400, detail="Reason for pending is required when marking a task as pending")
    if task.status == "in-progress" and task.assignedToId:
        existing = await db.wm_tasks.find_one({"assignedToId": task.assignedToId, "status": "in-progress"})
        if existing:
            raise HTTPException(status_code=400, detail="already a task in progress")
    res = await crud.create_wm_task(db, task=task)
    await redis_manager.invalidate_namespace("hrms:wm_tasks")
    await redis_manager.invalidate_namespace("hrms:tasks")
    return res

@app.put("/wm-tasks/{task_id}", response_model=schemas.WMTask)
async def update_wm_task(task_id: str, task_update: schemas.WMTaskUpdate, db=Depends(get_db)):
    from bson import ObjectId
    if task_update.status == "pending":
        reason = task_update.reasonForPending
        if not reason or not reason.strip():
            # Check existing task in db
            existing = await db.wm_tasks.find_one({"_id": ObjectId(task_id)})
            if not existing or not existing.get("reasonForPending") or not existing.get("reasonForPending").strip():
                raise HTTPException(status_code=400, detail="Reason for pending is required when marking a task as pending")
                
    if task_update.status == "in-progress":
        current_task = await db.wm_tasks.find_one({"_id": ObjectId(task_id)})
        assignee_id = task_update.assignedToId or (current_task.get("assignedToId") if current_task else None)
        if assignee_id:
            existing = await db.wm_tasks.find_one({
                "assignedToId": assignee_id,
                "status": "in-progress",
                "_id": {"$ne": ObjectId(task_id)}
            })
            if existing:
                raise HTTPException(status_code=400, detail="already a task in progress")

    updated = await crud.update_wm_task(db, task_id, task_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    await redis_manager.invalidate_namespace("hrms:wm_tasks")
    await redis_manager.invalidate_namespace("hrms:tasks")
    return updated

@app.delete("/wm-tasks/{task_id}")
async def delete_wm_task(task_id: str, db=Depends(get_db)):
    success = await crud.delete_wm_task(db, task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    await redis_manager.invalidate_namespace("hrms:wm_tasks")
    await redis_manager.invalidate_namespace("hrms:tasks")
    return {"message": "Task deleted successfully"}

# Activity Log Endpoints
@app.get("/task-logs", response_model=List[schemas.TaskLog])
async def read_task_logs(
    taskId: Optional[str] = None, 
    projectId: Optional[str] = None, 
    clientId: Optional[str] = None, 
    dailyReportId: Optional[str] = None,
    monthlyReportId: Optional[str] = None,
    db=Depends(get_db)
):
    return await crud.get_task_logs(db, taskId=taskId, projectId=projectId, clientId=clientId, dailyReportId=dailyReportId, monthlyReportId=monthlyReportId)

@app.get("/admin/activity-logs")
async def read_all_activity_logs(
    performedBy: Optional[str] = None,
    action: Optional[str] = None,
    search: Optional[str] = None,
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db=Depends(get_db),
    current_user=Depends(auth.require_admin_or_activity_logs)
):
    logs, total = await crud.get_all_activity_logs(
        db,
        performedBy=performedBy,
        action=action,
        search=search,
        startDate=startDate,
        endDate=endDate,
        limit=limit,
        skip=skip
    )
    return {"logs": logs, "total": total}


@app.post("/task-logs", response_model=schemas.TaskLog)
async def create_task_log(log: schemas.TaskLogBase, db=Depends(get_db)):
    await crud.log_activity(
        db=db,
        action=log.action,
        performedBy=log.performedBy,
        userName=log.userName,
        details=log.details,
        taskId=log.taskId,
        projectId=log.projectId,
        clientId=log.clientId,
        leadId=log.leadId,
        dailyReportId=log.dailyReportId,
        monthlyReportId=log.monthlyReportId
    )
    doc = await db.task_logs.find_one({"clientId": log.clientId, "action": log.action}, sort=[("_id", -1)])
    if doc:
        doc["id"] = str(doc["_id"])
    return doc

from pydantic import BaseModel
class TaskLogUpdate(BaseModel):
    details: str

@app.put("/task-logs/{log_id}", response_model=schemas.TaskLog)
async def update_task_log(log_id: str, update: TaskLogUpdate, db=Depends(get_db)):
    doc = await crud.update_task_log(db, log_id, update.details)
    if not doc:
        raise HTTPException(status_code=404, detail="Log not found")
    return doc

@app.delete("/task-logs/{log_id}")
async def delete_task_log(log_id: str, db=Depends(get_db)):
    success = await crud.delete_task_log(db, log_id)
    if not success:
        raise HTTPException(status_code=404, detail="Log not found")
    return {"message": "Log deleted successfully"}

# Marketing Reports Endpoints
@app.post("/marketing/reports/daily/generate-yesterday")
async def generate_yesterday_marketing_reports(db=Depends(get_db)):
    return await crud.generate_missing_daily_reports_for_yesterday(db)

@app.post("/marketing/reports/daily", response_model=schemas.MarketingDailyReport)
async def create_marketing_daily_report(report: schemas.MarketingDailyReportCreate, db=Depends(get_db)):
    return await crud.create_marketing_daily_report(db, report)

@app.get("/marketing/reports/daily", response_model=List[schemas.MarketingDailyReport])
async def get_marketing_daily_reports(client_id: str = None, date: str = None, start_date: str = None, end_date: str = None, userId: str = None, role: str = None, db=Depends(get_db)):
    user_info = {"sub": userId, "role": role} if userId else None
    return await crud.get_marketing_daily_reports(db, client_id, date, start_date, end_date, user_info=user_info)

@app.put("/marketing/reports/daily/{report_id}", response_model=schemas.MarketingDailyReport)
async def update_marketing_daily_report(report_id: str, report: schemas.MarketingDailyReportUpdate, db=Depends(get_db)):
    updated = await crud.update_marketing_daily_report(db, report_id, report)
    if not updated:
        raise HTTPException(status_code=404, detail="Daily report not found")
    return updated

@app.delete("/marketing/reports/daily/{report_id}")
async def delete_marketing_daily_report(report_id: str, db=Depends(get_db)):
    success = await crud.delete_marketing_daily_report(db, report_id)
    if not success:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Daily report not found")
    return {"message": "Daily report deleted"}

@app.post("/marketing/reports/daily/bulk-delete-leads")
async def bulk_delete_daily_leads(req: schemas.BulkDeleteLeadsRequest, db=Depends(get_db)):
    urls_to_delete = await crud.bulk_clear_leads_files(db, req.ids, "marketing_daily_reports")
    import os
    for url in urls_to_delete:
        parts = url.split('/uploads/')
        if len(parts) > 1:
            filename = parts[1]
            file_path = os.path.join(UPLOAD_DIR, os.path.basename(filename))
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
    return {"message": f"Cleared {len(urls_to_delete)} leads files."}

@app.post("/marketing/project-remarks", response_model=schemas.ProjectDailyRemark)
async def upsert_project_daily_remark(remark: schemas.ProjectDailyRemarkCreate, db=Depends(get_db)):
    doc = remark.model_dump(mode='json')
    result = await db.marketing_project_daily_remarks.update_one(
        {"projectId": remark.projectId, "date": str(remark.date)},
        {"$set": doc},
        upsert=True
    )
    saved = await db.marketing_project_daily_remarks.find_one({"projectId": remark.projectId, "date": str(remark.date)})
    return crud.fix_id(saved)

@app.get("/marketing/project-remarks", response_model=List[schemas.ProjectDailyRemark])
async def get_project_daily_remarks(clientId: Optional[str] = None, projectId: Optional[str] = None, startDate: Optional[str] = None, endDate: Optional[str] = None, db=Depends(get_db)):
    query = {}
    if clientId:
        query["clientId"] = clientId
    if projectId:
        query["projectId"] = projectId
    if startDate and endDate:
        query["date"] = {"$gte": startDate, "$lte": endDate}
    elif startDate:
        query["date"] = startDate
        
    remarks = await db.marketing_project_daily_remarks.find(query).to_list(1000)
    return [crud.fix_id(r) for r in remarks]

@app.post("/marketing/reports/monthly", response_model=schemas.MarketingMonthlyReport)
async def create_marketing_monthly_report(report: schemas.MarketingMonthlyReportCreate, db=Depends(get_db)):
    return await crud.create_marketing_monthly_report(db, report)

@app.get("/marketing/reports/monthly", response_model=List[schemas.MarketingMonthlyReport])
async def get_marketing_monthly_reports(client_id: str = None, month: Optional[List[str]] = Query(None), userId: str = None, role: str = None, db=Depends(get_db)):
    user_info = {"sub": userId, "role": role} if userId else None
    return await crud.get_marketing_monthly_reports(db, client_id, month, user_info=user_info)

@app.put("/marketing/reports/monthly/{report_id}", response_model=schemas.MarketingMonthlyReport)
async def update_marketing_monthly_report(report_id: str, report: schemas.MarketingMonthlyReportUpdate, db=Depends(get_db)):
    updated = await crud.update_marketing_monthly_report(db, report_id, report)
    if not updated:
        raise HTTPException(status_code=404, detail="Monthly report not found")
    return updated

@app.delete("/marketing/reports/monthly/{report_id}")
async def delete_marketing_monthly_report(report_id: str, db=Depends(get_db)):
    success = await crud.delete_marketing_monthly_report(db, report_id)
    if not success:
        raise HTTPException(status_code=404, detail="Monthly report not found")
    return {"message": "Monthly report deleted"}
# Chat Endpoints
@app.get("/chat/messages/{sender_id}/{receiver_id}", response_model=List[schemas.ChatMessage])
@redis_manager.cached_api(namespace="hrms:chat", ttl=30)
async def read_chat_messages(request: Request, sender_id: str, receiver_id: str, group_id: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_messages(db, sender_id, receiver_id, group_id)

@app.get("/chat/groups/{user_id}", response_model=List[schemas.ChatGroup])
@redis_manager.cached_api(namespace="hrms:chat", ttl=30)
async def read_chat_groups(request: Request, user_id: str, db=Depends(get_db)):
    return await crud.get_chat_groups(db, user_id)

@app.post("/chat/groups", response_model=schemas.ChatGroup)
async def create_chat_group(group: schemas.ChatGroupCreate, db=Depends(get_db)):
    res = await crud.create_chat_group(db, group)
    await redis_manager.invalidate_namespace("hrms:chat")
    return res

@app.put("/chat/groups/{group_id}", response_model=schemas.ChatGroup)
async def update_chat_group(group_id: str, group: schemas.ChatGroupUpdate, db=Depends(get_db)):
    res = await crud.update_chat_group(db, group_id, group)
    await redis_manager.invalidate_namespace("hrms:chat")
    return res

@app.delete("/chat/groups/{group_id}")
async def delete_chat_group(group_id: str, db=Depends(get_db)):
    await crud.delete_chat_group(db, group_id)
    await redis_manager.invalidate_namespace("hrms:chat")
    return {"message": "Group deleted successfully"}

@app.post("/chat/messages", response_model=schemas.ChatMessage)
async def create_chat_message(message: schemas.ChatMessageCreate, db=Depends(get_db)):
    saved_msg = await crud.create_message(db, message)
    await redis_manager.invalidate_namespace("hrms:chat")
    
    # Broadcast to active clients in real-time
    try:
        from fastapi.encoders import jsonable_encoder
        json_msg = jsonable_encoder(saved_msg)
        
        group_id = json_msg.get("groupId")
        if group_id:
            # Group or general channel message
            is_group = await db.chat_groups.find_one({"_id": ObjectId(group_id)}) if len(group_id) == 24 else None
            
            member_ids = []
            if is_group:
                member_ids = [str(m) for m in is_group.get("members", [])]
            else:
                # General channel: broadcast to all active employees
                employees = await db.employees.find().to_list(length=1000)
                member_ids = [str(emp["_id"]) for emp in employees]
                
            await ws_manager.broadcast_to_group(member_ids, "new_message", json_msg)
        else:
            # Personal message: broadcast to both receiver and sender
            receiver_id = json_msg.get("receiverId")
            sender_id = json_msg.get("senderId")
            recipients = [receiver_id, sender_id]
            await ws_manager.broadcast_to_group(recipients, "new_message", json_msg)
    except Exception as e:
        import logging
        logging.getLogger("websocket").warning(f"Error broadcasting new message: {e}")
        
    return saved_msg

@app.put("/chat/messages/{message_id}", response_model=schemas.ChatMessage)
async def update_chat_message(message_id: str, update: schemas.ChatMessageUpdate, db=Depends(get_db)):
    updated = await crud.update_message(db, message_id, update.text)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    return updated

@app.delete("/chat/messages/{message_id}")
async def delete_chat_message(message_id: str, request: Request, deleteFor: str = "everyone", db=Depends(get_db)):
    performed_by = None
    try:
        body = await request.json()
        performed_by = body.get("performedBy")
    except Exception:
        pass
    result = await crud.delete_message(db, message_id, delete_for=deleteFor, user_id=performed_by)
    try:
        from websocket import manager as ws_manager
        await ws_manager.broadcast_all("message_deleted", {"messageId": message_id, "deleteFor": deleteFor})
    except Exception:
        pass
    return result

@app.post("/chat/mark-seen/{sender_id}/{receiver_id}")
async def mark_messages_as_seen(sender_id: str, receiver_id: str, db=Depends(get_db)):
    await crud.mark_messages_as_seen(db, sender_id, receiver_id)
    await redis_manager.invalidate_namespace("hrms:chat")
    
    # Broadcast seen event to active clients in real-time
    try:
        is_group = await db.chat_groups.find_one({"_id": ObjectId(sender_id)}) if len(sender_id) == 24 else None
        is_channel = await db.chat_channels.find_one({"_id": ObjectId(sender_id)}) if len(sender_id) == 24 else None
        
        event_data = {
            "chatId": sender_id,
            "userId": receiver_id
        }
        
        if is_group or is_channel or sender_id.startswith("gen-"):
            # Group or General channel
            member_ids = []
            if is_group:
                member_ids = [str(m) for m in is_group.get("members", [])]
            else:
                # General channel
                employees = await db.employees.find().to_list(length=1000)
                member_ids = [str(emp["_id"]) for emp in employees]
            # Filter out the reader themselves
            recipients = [m for m in member_ids if m != receiver_id]
            await ws_manager.broadcast_to_group(recipients, "messages_seen", event_data)
        else:
            # Personal chat: send to the other user (sender_id)
            personal_event_data = {
                "chatId": receiver_id, # From the other user's perspective, this chat is with receiver_id
                "userId": receiver_id
            }
            await ws_manager.send_personal_message(sender_id, "messages_seen", personal_event_data)
    except Exception as e:
        import logging
        logging.getLogger("websocket").warning(f"Error broadcasting messages_seen event: {e}")
        
    return {"message": "Messages marked as seen"}

@app.get("/chat/unread-counts/{user_id}")
async def get_unread_counts(user_id: str, db=Depends(get_db)):
    # Aggregated counts for both personal and group chats
    unread_counts = {}
    
    # Support both string and ObjectId user ID types to prevent database type mismatch bugs
    user_id_obj = ObjectId(user_id) if len(user_id) == 24 else None
    user_ids = [user_id]
    if user_id_obj:
        user_ids.append(user_id_obj)
    
    # Get all valid employee IDs to filter out deleted or external users not in the chat list
    employees = await db.employees.find({}, {"_id": 1}).to_list(length=10000)
    valid_employee_ids = [str(e["_id"]) for e in employees]
    valid_employee_objs = [ObjectId(e_id) for e_id in valid_employee_ids if len(e_id) == 24]
    valid_senders = valid_employee_ids + valid_employee_objs

    # 1. Personal Chats: messages where receiverId == user_id (string or Obj) and seenBy does not contain user_id
    cursor_personal = db.messages._collection.aggregate([
        {"$match": {
            "$or": [{"receiverId": user_id}, {"receiverId": user_id_obj}],
            "senderId": {"$nin": user_ids, "$in": valid_senders},
            "seenBy": {"$nin": user_ids}
        }},
        {"$group": {"_id": "$senderId", "count": {"$sum": 1}}}
    ])
    personal_results = await cursor_personal.to_list(length=1000)
    for r in personal_results:
        unread_counts[str(r["_id"])] = r["count"]
        
    # 2. Group Chats: messages where groupId is present and seenBy does not contain user_id
    user_groups = await crud.get_chat_groups(db, user_id)
    group_ids = [g["id"] for g in user_groups]
    channels = await crud.get_chat_channels(db)
    channel_ids = [c["id"] for c in channels]
    group_ids.extend(channel_ids)
    
    # Ensure query_group_ids contains both string and ObjectId formats for every group ID to prevent Mongo type mismatch bugs
    query_group_ids = []
    for g_id in group_ids:
        query_group_ids.append(g_id)
        if len(g_id) == 24:
            try:
                query_group_ids.append(ObjectId(g_id))
            except:
                pass
    
    cursor_groups = db.messages._collection.aggregate([
        {"$match": {
            "groupId": {"$in": query_group_ids},
            "senderId": {"$nin": user_ids},
            "seenBy": {"$nin": user_ids}
        }},
        {"$group": {"_id": "$groupId", "count": {"$sum": 1}}}
    ])
    group_results = await cursor_groups.to_list(length=1000)
    for r in group_results:
        unread_counts[str(r["_id"])] = r["count"]
        
    return unread_counts

@app.get("/chat/summaries/{user_id}")
@redis_manager.cached_api(namespace="hrms:chat", ttl=30)
async def get_chat_summaries(request: Request, user_id: str, db=Depends(get_db)):
    return await crud.get_chat_summaries(db, user_id)

@app.post("/chat/messages/{message_id}/toggle-save")
async def toggle_save_message(message_id: str, user_id: str, db=Depends(get_db)):
    status = await crud.toggle_save_message(db, message_id, user_id)
    return {"isSaved": status}

@app.post("/chat/messages/{message_id}/toggle-pin")
async def toggle_pin_message(message_id: str, db=Depends(get_db)):
    status = await crud.toggle_pin_message(db, message_id)
    return {"isPinned": status}

# Chat Channels
@app.get("/chat/channels", response_model=List[schemas.ChatChannel])
async def get_chat_channels(db=Depends(get_db)):
    return await crud.get_chat_channels(db)

@app.post("/chat/channels", response_model=schemas.ChatChannel)
async def create_chat_channel(channel: schemas.ChatChannelCreate, db=Depends(get_db)):
    return await crud.create_chat_channel(db, channel)

@app.put("/chat/channels/{channel_id}", response_model=schemas.ChatChannel)
async def update_chat_channel(channel_id: str, channel: schemas.ChatChannelUpdate, db=Depends(get_db)):
    return await crud.update_chat_channel(db, channel_id, channel)

@app.delete("/chat/channels/{channel_id}")
async def delete_chat_channel(channel_id: str, db=Depends(get_db)):
    return await crud.delete_chat_channel(db, channel_id)

@app.get("/chat/saved-messages/{user_id}")
async def get_saved_messages(user_id: str, db=Depends(get_db)):
    return await crud.get_saved_messages(db, user_id)

@app.get("/chat/files/{user_id}/{other_id}")
async def get_chat_files(user_id: str, other_id: str, is_group: bool = False, db=Depends(get_db)):
    return await crud.get_chat_files(db, user_id, other_id, is_group)

@app.post("/chat/messages/{message_id}/toggle-archive")
async def toggle_archive_message(message_id: str, user_id: str, db=Depends(get_db)):
    status = await crud.toggle_archive_message(db, message_id, user_id)
    return {"isArchived": status}

@app.post("/chat/messages/{message_id}/toggle-complete")
async def toggle_complete_message(message_id: str, user_id: str, db=Depends(get_db)):
    status = await crud.toggle_complete_message(db, message_id, user_id)
    return {"isCompleted": status}

async def _broadcast_message_update(db, message_id: str):
    msg_doc = await db.messages.find_one({"_id": ObjectId(message_id)})
    if msg_doc:
        from fastapi.encoders import jsonable_encoder
        json_msg = jsonable_encoder(crud.fix_id(msg_doc))
        try:
            group_id = json_msg.get("groupId")
            if group_id:
                is_group = await db.chat_groups.find_one({"_id": ObjectId(group_id)}) if len(group_id) == 24 else None
                member_ids = [str(m) for m in is_group.get("members", [])] if is_group else [str(emp["_id"]) for emp in await db.employees.find().to_list(1000)]
                await ws_manager.broadcast_to_group(member_ids, "message_updated", json_msg)
            else:
                recipients = [json_msg.get("receiverId"), json_msg.get("senderId")]
                await ws_manager.broadcast_to_group([r for r in recipients if r], "message_updated", json_msg)
        except Exception:
            pass

@app.post("/chat/messages/{message_id}/reaction")
async def toggle_reaction(message_id: str, user_id: str, emoji: str, db=Depends(get_db)):
    reactions = await crud.toggle_reaction(db, message_id, user_id, emoji)
    if reactions is None:
        raise HTTPException(status_code=404, detail="Message not found")
    await _broadcast_message_update(db, message_id)
    return {"reactions": reactions}

@app.put("/employees/{employee_id}/status")
async def update_employee_status(employee_id: str, status: Optional[str] = None, emoji: Optional[str] = None, db=Depends(get_db)):
    res = await crud.update_employee_status(db, employee_id, status, emoji)
    await redis_manager.invalidate_namespace("hrms:employees")
    return res

@app.post("/chat/messages/{message_id}/vote")
async def vote_on_poll(message_id: str, user_id: str, option_id: str, db=Depends(get_db)):
    options = await crud.vote_poll(db, message_id, user_id, option_id)
    if options is None:
        raise HTTPException(status_code=404, detail="Poll not found")
    await _broadcast_message_update(db, message_id)
    return {"options": options}

@app.post("/chat/typing")
async def set_typing_status(chat_id: str, user_id: str, is_typing: bool, db=Depends(get_db)):
    await crud.set_typing_status(db, chat_id, user_id, is_typing)
    return {"status": "ok"}

@app.get("/chat/online-users")
async def get_online_users(db=Depends(get_db)):
    return list(ws_manager.active_connections.keys())

@app.get("/chat/typing/{chat_id}")
async def get_typing_status(chat_id: str, user_id: str, db=Depends(get_db)):
    typing_users = await crud.get_typing_users(db, chat_id, user_id)
    return {"typingUsers": typing_users}

@app.get("/chat/ws-info")
async def get_ws_info(request: Request):
    backend_port = os.environ.get("BACKEND_PORT", os.environ.get("PORT", "8000"))
    try:
        port_val = int(backend_port)
    except ValueError:
        port_val = 8000
    
    backend_url = os.environ.get("BACKEND_URL", "")
    ws_url = None
    if backend_url:
        scheme = "wss" if backend_url.startswith("https") else "ws"
        clean_url = backend_url.replace("https://", "").replace("http://", "").rstrip("/")
        if "localhost" not in clean_url and "127.0.0.1" not in clean_url and not clean_url.endswith("/api"):
            ws_url = f"{scheme}://{clean_url}/api/chat/ws"
        else:
            ws_url = f"{scheme}://{clean_url}/chat/ws"
    else:
        # Fallback using request host
        scheme = "wss" if request.url.scheme == "https" else "ws"
        clean_url = request.url.netloc.rstrip("/")
        if "localhost" not in clean_url and "127.0.0.1" not in clean_url:
            ws_url = f"{scheme}://{clean_url}/api/chat/ws"
        else:
            ws_url = f"{scheme}://{clean_url}/chat/ws"
        
    return {"port": port_val, "url": ws_url}

@app.websocket("/chat/ws/{user_id}")
async def chat_websocket_endpoint(websocket: WebSocket, user_id: str):
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            # Await client-sent JSON messages
            data = await websocket.receive_json()
            if isinstance(data, dict):
                msg_type = data.get("type")
                if msg_type == "typing":
                    chat_id = data.get("chatId")
                    is_typing = data.get("isTyping", False)
                    if chat_id:
                        event_data = {
                            "chatId": chat_id,
                            "userId": user_id,
                            "isTyping": is_typing
                        }
                        # Find recipient list
                        is_group = await database.db.chat_groups.find_one({"_id": ObjectId(chat_id)}) if len(chat_id) == 24 else None
                        is_channel = await database.db.chat_channels.find_one({"_id": ObjectId(chat_id)}) if len(chat_id) == 24 else None
                        
                        if is_group or is_channel or chat_id.startswith("gen-"):
                            # Broadcast to group members
                            member_ids = []
                            if is_group:
                                # Convert ObjectId members to strings
                                member_ids = [str(m) for m in is_group.get("members", [])]
                            else:
                                # General channel: broadcast to all active employees
                                employees = await database.db.employees.find().to_list(length=1000)
                                member_ids = [str(emp["_id"]) for emp in employees]
                            
                            # Filter out the typing user themselves to avoid echo
                            recipients = [m for m in member_ids if m != user_id]
                            await ws_manager.broadcast_to_group(recipients, "typing_status", event_data)
                        else:
                            # Personal chat: send directly to the recipient (which is chat_id)
                            personal_event_data = {
                                "chatId": user_id,
                                "userId": user_id,
                                "isTyping": is_typing
                            }
                            await ws_manager.send_personal_message(chat_id, "typing_status", personal_event_data)
    except WebSocketDisconnect:
        await ws_manager.disconnect(user_id, websocket)
    except Exception as e:
        import logging
        logging.getLogger("websocket").warning(f"WebSocket error for user {user_id}: {e}")
        await ws_manager.disconnect(user_id, websocket)

# Employee Document Endpoints
@app.post("/employee-documents", response_model=schemas.EmployeeDocument)
async def create_employee_document(document: schemas.EmployeeDocumentCreate, db=Depends(get_db)):
    return await crud.create_employee_document(db, document)

@app.get("/employee-documents", response_model=List[schemas.EmployeeDocument])
async def read_employee_documents(employeeId: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_employee_documents(db, employee_id=employeeId)

@app.put("/employee-documents/{doc_id}", response_model=schemas.EmployeeDocument)
async def update_employee_document(doc_id: str, doc_update: schemas.EmployeeDocumentUpdate, db=Depends(get_db)):
    updated = await crud.update_employee_document(db, doc_id, doc_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Document not found")
    return updated

@app.delete("/employee-documents/{doc_id}")
async def delete_employee_document(doc_id: str, db=Depends(get_db)):
    success = await crud.delete_employee_document(db, doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document deleted successfully"}

# Document Type Endpoints
@app.post("/document-types", response_model=schemas.DocumentType)
async def create_document_type(doc_type: schemas.DocumentTypeCreate, db=Depends(get_db)):
    return await crud.create_document_type(db, doc_type)

@app.get("/document-types", response_model=List[schemas.DocumentType])
async def read_document_types(db=Depends(get_db)):
    return await crud.get_document_types(db)

@app.put("/document-types/{type_id}", response_model=schemas.DocumentType)
async def update_document_type(type_id: str, type_update: schemas.DocumentTypeUpdate, db=Depends(get_db)):
    updated = await crud.update_document_type(db, type_id, type_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Document type not found")
    return updated

@app.delete("/document-types/{type_id}")
async def delete_document_type(type_id: str, db=Depends(get_db)):
    success = await crud.delete_document_type(db, type_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document type not found")
    return {"message": "Document type deleted successfully"}


# Document Templates Endpoints
@app.post("/document-templates", response_model=schemas.DocumentTemplate)
async def create_document_template(template: schemas.DocumentTemplateCreate, db=Depends(get_db)):
    return await crud.create_document_template(db, template)

@app.get("/document-templates", response_model=List[schemas.DocumentTemplate])
async def read_document_templates(skip: int = 0, limit: int = 100, db=Depends(get_db)):
    return await crud.get_document_templates(db, skip=skip, limit=limit)

@app.get("/document-templates/{template_id}", response_model=schemas.DocumentTemplate)
async def read_document_template(template_id: str, db=Depends(get_db)):
    template = await crud.get_document_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Document template not found")
    return template

@app.put("/document-templates/{template_id}", response_model=schemas.DocumentTemplate)
async def update_document_template(template_id: str, template_update: schemas.DocumentTemplateUpdate, db=Depends(get_db)):
    updated = await crud.update_document_template(db, template_id, template_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Document template not found")
    return updated

@app.delete("/document-templates/{template_id}")
async def delete_document_template(template_id: str, db=Depends(get_db)):
    success = await crud.delete_document_template(db, template_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document template not found")
    return {"message": "Document template deleted successfully"}


# Document Request Endpoints
@app.post("/document-requests", response_model=schemas.DocumentRequest)
async def create_document_request(request: schemas.DocumentRequestCreate, db=Depends(get_db)):
    return await crud.create_document_request(db, request)

@app.get("/document-requests", response_model=List[schemas.DocumentRequest])
async def read_document_requests(employeeId: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_document_requests(db, employee_id=employeeId)

@app.put("/document-requests/{req_id}", response_model=schemas.DocumentRequest)
async def update_document_request(req_id: str, req_update: schemas.DocumentRequestUpdate, db=Depends(get_db)):
    updated = await crud.update_document_request(db, req_id, req_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Request not found")
    return updated

@app.delete("/document-requests/{req_id}")
async def delete_document_request(req_id: str, db=Depends(get_db)):
    success = await crud.delete_document_request(db, req_id)
    if not success:
        raise HTTPException(status_code=404, detail="Request not found")
    return {"message": "Request deleted successfully"}

# Employee Daily Report Endpoints
@app.post("/employee-daily-reports", response_model=schemas.EmployeeDailyReport)
async def create_daily_report(report: schemas.EmployeeDailyReportCreate, db=Depends(get_db)):
    return await crud.create_employee_daily_report(db, report)

@app.get("/employee-daily-reports", response_model=List[schemas.EmployeeDailyReport])
async def read_daily_reports(employeeId: Optional[str] = None, department: Optional[str] = None, date: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_employee_daily_reports(db, employee_id=employeeId, department=department, date=date)

@app.put("/employee-daily-reports/{report_id}", response_model=schemas.EmployeeDailyReport)
async def update_daily_report(report_id: str, report_update: schemas.EmployeeDailyReportUpdate, db=Depends(get_db)):
    updated = await crud.update_employee_daily_report(db, report_id, report_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Report not found")
    return updated

@app.get("/leads/{lead_id}/logs", response_model=List[schemas.TaskLog])
async def read_lead_logs(lead_id: str, db=Depends(get_db)):
    return await crud.get_lead_logs(db, lead_id)

# System Settings Endpoints
@app.get("/system-settings", response_model=schemas.SystemSettings)
async def get_system_settings(db=Depends(get_db)):
    return await crud.get_system_settings(db)

@app.put("/system-settings", response_model=schemas.SystemSettings)
async def update_system_settings(settings_update: schemas.SystemSettingsUpdate, db=Depends(get_db)):
    return await crud.update_system_settings(db, settings_update)

# Seating Arrangement Endpoints
@app.get("/seating-arrangement")
async def get_seating_arrangement(db=Depends(get_db)):
    arrangement = await db.seating_arrangement.find_one({})
    if not arrangement:
        return {"desks": []}
    return {"desks": arrangement.get("desks", [])}

@app.post("/seating-arrangement")
async def save_seating_arrangement(payload: dict, db=Depends(get_db)):
    desks = payload.get("desks", [])
    await db.seating_arrangement.update_one({}, {"$set": {"desks": desks}}, upsert=True)
    return {"status": "success", "desks": desks}

# Sales Lead Endpoints
@app.get("/leads", response_model=List[schemas.Lead])
async def read_leads(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_leads(db, skip=skip, limit=limit)

@app.post("/leads", response_model=schemas.Lead)
async def create_lead(lead: schemas.LeadCreate, db=Depends(get_db)):
    return await crud.create_lead(db, lead)

@app.post("/leads/bulk", response_model=List[schemas.Lead])
async def create_leads_bulk(leads: List[schemas.LeadCreate], db=Depends(get_db)):
    return await crud.create_leads_bulk(db, leads)

@app.put("/leads/bulk-assign", response_model=dict)
async def bulk_assign_leads(payload: schemas.BulkAssignLeads, db=Depends(get_db)):
    result = await crud.bulk_assign_leads(db, payload.leadIds, payload.assignedTo, payload.performedBy, payload.userName)
    return {"message": "Leads assigned successfully", "modified_count": result}

@app.post("/leads/bulk-delete", response_model=dict)
async def bulk_delete_leads(payload: schemas.BulkDeleteLeads, db=Depends(get_db)):
    result = await crud.bulk_delete_leads(db, payload.leadIds)
    return {"message": "Leads deleted successfully", "deleted_count": result}

@app.put("/leads/{lead_id}", response_model=schemas.Lead)
async def update_lead(lead_id: str, lead_update: schemas.LeadUpdate, db=Depends(get_db)):
    result = await crud.update_lead(db, lead_id, lead_update)
    if not result:
        raise HTTPException(status_code=404, detail="Lead not found")
    return result
        
@app.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str, db=Depends(get_db)):
    success = await crud.delete_lead(db, lead_id)
    if not success:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"message": "Lead deleted successfully"}

@app.post("/leads/{lead_id}/follow-ups", response_model=schemas.Lead)
async def add_lead_follow_up(lead_id: str, follow_up: schemas.FollowUp, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    return await crud.add_lead_follow_up(db, lead_id, follow_up, performedBy=performedBy, userName=userName)

@app.put("/leads/{lead_id}/follow-ups/{follow_up_idx}", response_model=schemas.Lead)
async def update_lead_follow_up(lead_id: str, follow_up_idx: int, follow_up: schemas.FollowUp, performedBy: Optional[str] = None, userName: Optional[str] = None, db=Depends(get_db)):
    return await crud.update_lead_follow_up(db, lead_id, follow_up_idx, follow_up, performedBy=performedBy, userName=userName)

# Sales Target Routes
@app.get("/sales-targets")
async def read_sales_targets(month: Optional[str] = None, year: Optional[int] = None, type: Optional[str] = None, db=Depends(get_db)):
    try:
        targets = await crud.get_sales_targets(db, month, year, type)
        return targets
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sales-targets", response_model=schemas.SalesTarget)
async def upsert_sales_target(target: schemas.SalesTargetCreate, db=Depends(get_db)):
    try:
        return await crud.create_or_update_sales_target(db, target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/sales-targets/{target_id}", response_model=schemas.SalesTarget)
async def update_sales_target(target_id: str, target_update: schemas.SalesTargetUpdate, db=Depends(get_db)):
    updated = await crud.update_sales_target(db, target_id, target_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Target not found")
    return updated

@app.delete("/sales-targets/{target_id}")
async def delete_sales_target(target_id: str, db=Depends(get_db)):
    await crud.delete_sales_target(db, target_id)
    return {"message": "Sales target deleted successfully"}

# Incentive Slab Routes
@app.get("/incentive-slabs", response_model=List[schemas.IncentiveSlab])
async def read_incentive_slabs(db=Depends(get_db)):
    return await crud.get_incentive_slabs(db)

@app.post("/incentive-slabs", response_model=schemas.IncentiveSlab)
async def create_incentive_slab(slab: schemas.IncentiveSlabCreate, db=Depends(get_db)):
    return await crud.create_incentive_slab(db, slab)

@app.put("/incentive-slabs/{slab_id}", response_model=schemas.IncentiveSlab)
async def update_incentive_slab(slab_id: str, slab_update: schemas.IncentiveSlabUpdate, db=Depends(get_db)):
    updated = await crud.update_incentive_slab(db, slab_id, slab_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Slab not found")
    return updated

@app.delete("/incentive-slabs/{slab_id}")
async def delete_incentive_slab(slab_id: str, db=Depends(get_db)):
    await crud.delete_incentive_slab(db, slab_id)
    return {"message": "Incentive slab deleted successfully"}

@app.post("/sales-targets/recalculate-all")
async def recalculate_all_sales_targets(db=Depends(get_db)):
    """Manually trigger recalculation of all sales targets from all paid invoices."""
    print("Recalculate all sales targets triggered!")
    try:
        targets = await db.sales_targets.find({}).to_list(length=10000)
        recalculated = 0
        for t in targets:
            emp_id = str(t.get("employeeId", ""))
            target_type = t.get("type", "Monthly")
            month = t.get("month")
            year = t.get("year")
            week = t.get("week")
            start_date = t.get("startDate")
            end_date = t.get("endDate")
            
            await crud.recalculate_sales_target(
                db, emp_id, month, year, target_type, week,
                startDate=start_date, endDate=end_date
            )
            recalculated += 1
        
        print(f"Successfully recalculated {recalculated} targets")
        return {"message": f"Successfully recalculated {recalculated} sales targets"}
    except Exception as e:
        print(f"Error in recalculate-all: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# User Permission Routes
@app.get("/user-permissions/all", response_model=List[schemas.UserPermission])
async def read_all_user_permissions(db=Depends(get_db)):
    return await crud.get_all_user_permissions(db)

@app.get("/user-permissions/{employee_id}", response_model=Optional[schemas.UserPermission])
async def read_user_permissions(employee_id: str, db=Depends(get_db)):
    return await crud.get_user_permissions(db, employee_id)

@app.post("/user-permissions/{employee_id}", response_model=schemas.UserPermission)
async def update_user_permissions(employee_id: str, permissions: schemas.UserPermissionUpdate, request: Request, db=Depends(get_db)):
    performed_by, user_name = await get_actor_from_request(request, db)
    return await crud.save_user_permissions(db, employee_id, permissions, performed_by=performed_by, user_name=user_name)

# Permission Presets Routes
@app.get("/permission-presets", response_model=List[schemas.PermissionPreset])
async def read_permission_presets(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_permission_presets(db, skip, limit)

@app.get("/permission-presets/{preset_id}", response_model=Optional[schemas.PermissionPreset])
async def read_permission_preset(preset_id: str, db=Depends(get_db)):
    preset = await crud.get_permission_preset(db, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset

@app.post("/permission-presets", response_model=schemas.PermissionPreset)
async def create_permission_preset(preset: schemas.PermissionPresetCreate, db=Depends(get_db)):
    return await crud.create_permission_preset(db, preset)

@app.put("/permission-presets/{preset_id}", response_model=schemas.PermissionPreset)
async def update_permission_preset(preset_id: str, preset: schemas.PermissionPresetUpdate, db=Depends(get_db)):
    updated = await crud.update_permission_preset(db, preset_id, preset)
    if not updated:
        raise HTTPException(status_code=404, detail="Preset not found")
    return updated

@app.delete("/task-presets/{preset_id}")
async def delete_task_preset_entry(preset_id: str, db=Depends(get_db)):
    success = await crud.delete_task_preset(db, preset_id)
    if not success:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"status": "success"}

@app.delete("/permission-presets/{preset_id}")
async def delete_permission_preset(preset_id: str, db=Depends(get_db)):
    deleted = await crud.delete_permission_preset(db, preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"message": "Preset deleted"}

# Department Routes
# (Using existing routes defined earlier in the file)

# Time Recovery Endpoints
@app.post('/time-recovery', response_model=schemas.TimeRecovery)
async def create_time_recovery(recovery: schemas.TimeRecoveryCreate, db=Depends(get_db)):
    return await crud.create_time_recovery(db, recovery)

@app.get('/time-recovery', response_model=List[schemas.TimeRecovery])
async def read_time_recoveries(skip: int = 0, limit: int = 10000, db=Depends(get_db)):
    return await crud.get_time_recoveries(db, skip=skip, limit=limit)

@app.get('/time-recovery/employee/{employee_id}', response_model=List[schemas.TimeRecovery])
async def read_employee_time_recoveries(employee_id: str, db=Depends(get_db)):
    return await crud.get_employee_time_recoveries(db, employee_id)

@app.put('/time-recovery/{recovery_id}/status', response_model=schemas.TimeRecovery)
async def update_time_recovery_status(recovery_id: str, status: str, db=Depends(get_db)):
    return await crud.update_time_recovery_status(db, recovery_id, status)

# Invoice Endpoints
@app.post("/invoices", response_model=schemas.Invoice)
async def create_invoice(invoice: schemas.InvoiceCreate, db=Depends(get_db)):
    return await crud.create_invoice(db, invoice)

@app.get("/invoices", response_model=List[schemas.Invoice])
async def read_invoices(skip: int = 0, limit: int = 10000, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    return await crud.get_invoices(db, current_user, skip=skip, limit=limit)

@app.get("/invoices/deleted", response_model=List[schemas.Invoice])
async def get_deleted_invoices(db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    if current_user.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    return await crud.get_deleted_invoices(db)

@app.post("/invoices/{invoice_id}/restore")
async def restore_invoice(invoice_id: str, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    if current_user.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    success = await crud.restore_invoice(db, invoice_id)
    if not success:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"message": "Invoice restored successfully"}

@app.get("/invoices/next-number")
async def get_next_number(type: str = "Tax Invoice", taxType: str = "CGST+SGST", db=Depends(get_db)):
    next_num = await crud.get_next_invoice_number(db, invoice_type=type, tax_type=taxType)
    return {"nextInvoiceNumber": next_num}

@app.get("/invoices/{invoice_id}", response_model=schemas.Invoice)
async def read_invoice(invoice_id: str, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    db_invoice = await crud.get_invoice(db, invoice_id, current_user)
    if db_invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return db_invoice

@app.put("/invoices/{invoice_id}", response_model=schemas.Invoice)
async def update_invoice(invoice_id: str, invoice_update: schemas.InvoiceUpdate, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    updated = await crud.update_invoice(db, invoice_id, invoice_update, current_user)
    if updated is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return updated

@app.post("/invoices/{invoice_id}/convert-to-tax", response_model=schemas.Invoice)
async def convert_invoice_to_tax(invoice_id: str, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    db_invoice = await crud.get_invoice(db, invoice_id, current_user)
    if not db_invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if db_invoice.get("invoiceType") != "Proforma Invoice":
        raise HTTPException(status_code=400, detail="Only Proforma Invoices can be converted")
    
    next_num = await crud.get_next_invoice_number(db, invoice_type="Tax Invoice")
    
    # Copy data to create a new invoice
    new_invoice_data = dict(db_invoice)
    new_invoice_data.pop("id", None)
    new_invoice_data.pop("_id", None)
    
    new_invoice_data["invoiceType"] = "Tax Invoice"
    new_invoice_data["invoiceNumber"] = next_num
    
    # Use schema to validate and parse
    new_invoice = schemas.InvoiceCreate(**new_invoice_data)
    created_invoice = await crud.create_invoice(db, new_invoice)
    
    return created_invoice

@app.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: str, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    success = await crud.delete_invoice(db, invoice_id)
    if not success:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"message": "Invoice deleted successfully"}


# --- Schedules API ---
@app.get("/schedules", response_model=List[schemas.Schedule])
async def get_schedules(employeeId: Optional[str] = None, date: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_schedules(db, employee_id=employeeId, date_str=date, date_from=date_from, date_to=date_to)

@app.post("/schedules", response_model=schemas.Schedule)
async def create_schedule(schedule: schemas.ScheduleCreate, db=Depends(get_db), _token=Depends(auth.require_auth)):
    from datetime import datetime, date
    # Check for overlaps
    all_schedules = await crud.get_schedules(db, date_str=str(schedule.date))
    check_ids = [str(schedule.employeeId)] + [str(x) for x in getattr(schedule, "attendees", []) or []]
    
    if all_schedules:
        for existing in all_schedules:
            existing_emp = str(existing.get("employeeId"))
            existing_attendees = [str(x) for x in existing.get("attendees", []) or []]
            
            overlapping_check_ids = [cid for cid in check_ids if cid == existing_emp or cid in existing_attendees]
            if not overlapping_check_ids:
                continue
                
            ex_start = existing.get("startTime")
            ex_end = existing.get("endTime")
            if ex_start and ex_end:
                if max(schedule.startTime, ex_start) < min(schedule.endTime, ex_end):
                    if any(cid != str(_token.get("sub")) for cid in overlapping_check_ids):
                        raise HTTPException(status_code=400, detail="Cannot assign an overlapping schedule to someone else or an attendee.")

    schedule_data = schedule.model_dump()
    dt_val = schedule_data.get("date")
    if type(dt_val) is date:
        schedule_data["date"] = datetime.combine(dt_val, datetime.min.time())
    elif isinstance(dt_val, str):
        schedule_data["date"] = datetime.strptime(dt_val, "%Y-%m-%d")

    return await crud.create_schedule(db, schedule_data)

@app.put("/schedules/{schedule_id}", response_model=schemas.Schedule)
async def update_schedule(schedule_id: str, schedule: schemas.ScheduleUpdate, db=Depends(get_db), _token=Depends(auth.require_auth)):
    from datetime import datetime, date
    update_data = schedule.model_dump(exclude_unset=True)

    # Check for overlaps if time is updated
    if update_data.get("startTime") and update_data.get("endTime"):
        all_schedules = await crud.get_schedules(db, date_str=str(update_data.get("date")))
        check_ids = [str(update_data.get("employeeId"))] + [str(x) for x in update_data.get("attendees", []) or []]
        
        if all_schedules:
            for existing in all_schedules:
                if str(existing.get("_id")) == schedule_id or str(existing.get("id")) == schedule_id:
                    continue
                
                existing_emp = str(existing.get("employeeId"))
                existing_attendees = [str(x) for x in existing.get("attendees", []) or []]
                
                overlapping_check_ids = [cid for cid in check_ids if cid == existing_emp or cid in existing_attendees]
                if not overlapping_check_ids:
                    continue
                    
                ex_start = existing.get("startTime")
                ex_end = existing.get("endTime")
                if ex_start and ex_end:
                    if max(update_data["startTime"], ex_start) < min(update_data["endTime"], ex_end):
                        if any(cid != str(_token.get("sub")) for cid in overlapping_check_ids):
                            raise HTTPException(status_code=400, detail="Cannot assign an overlapping schedule to someone else or an attendee.")

    dt_val = update_data.get("date")
    if dt_val is not None:
        if type(dt_val) is date:
            update_data["date"] = datetime.combine(dt_val, datetime.min.time())
        elif isinstance(dt_val, str):
            update_data["date"] = datetime.strptime(dt_val, "%Y-%m-%d")

    updated = await crud.update_schedule(db, schedule_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return updated

@app.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, db=Depends(get_db)):
    success = await crud.delete_schedule(db, schedule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"message": "Schedule deleted successfully"}

@app.post("/schedules/free-slots")
async def get_free_slots(request: dict, db=Depends(get_db)):
    """
    Compute common free time slots for a list of employees on a given date.
    Body: { "employeeIds": ["id1", "id2"], "date": "YYYY-MM-DD", "durationMins": 30 }
    Returns: { "freeSlots": [{ "start": "HH:MM", "end": "HH:MM" }, ...] }
    """
    employee_ids = request.get("employeeIds", [])
    date_str = request.get("date")
    duration_mins = request.get("durationMins", 30)

    if not employee_ids or not date_str:
        return {"freeSlots": []}

    # Collect all busy intervals across all employees
    all_busy = []
    for emp_id in employee_ids:
        schedules = await crud.get_schedules(db, employee_id=emp_id, date_str=date_str)
        for s in schedules:
            start_t = s.get("startTime", "")
            end_t = s.get("endTime", "")
            if start_t and end_t:
                all_busy.append((start_t, end_t))

    # Also check for SMM client meetings that mention these employees
    # (meetings stored in client documents are text-based, so we skip those)

    # Sort and merge overlapping busy intervals
    def time_to_mins(t: str) -> int:
        parts = t.split(":")
        return int(parts[0]) * 60 + int(parts[1])

    def mins_to_time(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    busy_mins = sorted([(time_to_mins(s), time_to_mins(e)) for s, e in all_busy])

    merged = []
    for start, end in busy_mins:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Office hours: get from settings, default to 09:30 to 18:30
    sys_settings = await crud.get_system_settings(db)
    office_start_str = sys_settings.get("officeStartTime", "09:30")
    office_end_str = sys_settings.get("officeEndTime", "18:30")
    
    office_start = time_to_mins(office_start_str)
    office_end = time_to_mins(office_end_str)

    # If the requested date is today, do not allow past time slots
    now = crud.get_now()
    if date_str == now.strftime("%Y-%m-%d"):
        current_mins = now.hour * 60 + now.minute
        if current_mins > office_start:
            office_start = min(current_mins, office_end)

    # Invert merged busy intervals to get free slots within office hours
    free_slots = []
    cursor = office_start

    for busy_start, busy_end in merged:
        # Clamp to office hours
        bs = max(busy_start, office_start)
        be = min(busy_end, office_end)
        if bs > cursor and (bs - cursor) >= duration_mins:
            free_slots.append({"start": mins_to_time(cursor), "end": mins_to_time(bs)})
        if be > cursor:
            cursor = be

    # Remaining time after last busy block
    if cursor < office_end and (office_end - cursor) >= duration_mins:
        free_slots.append({"start": mins_to_time(cursor), "end": mins_to_time(office_end)})

    return {"freeSlots": free_slots}


# --- Appointment Scheduling APIs ---
@app.get("/appointments/config")
@app.get("/api/appointments/config")
async def get_all_appointment_configs(employeeId: Optional[str] = None, db=Depends(get_db)):
    query = {"employeeId": employeeId} if employeeId else {}
    cursor = db.appointment_configs.find(query)
    configs = await cursor.to_list(length=1000)
    return [crud.fix_id(c) for c in configs]

@app.get("/appointments/config/{employee_id}")
@app.get("/api/appointments/config/{employee_id}")
async def get_appointment_config(employee_id: str, db=Depends(get_db)):
    config = await crud.get_appointment_config(db, employee_id)
    if not config:
        return {
            "employeeId": employee_id,
            "duration": 30,
            "availability": {
                "Monday": [{"start": "09:00", "end": "17:00"}],
                "Tuesday": [{"start": "09:00", "end": "17:00"}],
                "Wednesday": [{"start": "09:00", "end": "17:00"}],
                "Thursday": [{"start": "09:00", "end": "17:00"}],
                "Friday": [{"start": "09:00", "end": "17:00"}],
                "Saturday": [],
                "Sunday": []
            },
            "timezone": "Asia/Kolkata",
            "active": True
        }
    return config

@app.post("/appointments/config")
@app.post("/api/appointments/config")
async def save_appointment_config(config: dict, db=Depends(get_db), _token=Depends(auth.require_auth)):
    curr_user_id = str(_token.get("sub"))
    target_emp_id = config.get("employeeId")
    if not target_emp_id:
        raise HTTPException(status_code=400, detail="employeeId is required")
    
    if curr_user_id != str(target_emp_id):
        user_role = _token.get("role")
        if user_role not in ["Admin", "HR"]:
            raise HTTPException(status_code=403, detail="Not authorized to edit this configuration")
            
    return await crud.save_appointment_config(db, config)

@app.delete("/appointments/config/{config_id}")
@app.delete("/api/appointments/config/{config_id}")
async def delete_appointment_config(config_id: str, db=Depends(get_db), _token=Depends(auth.require_auth)):
    curr_user_id = str(_token.get("sub"))
    user_role = _token.get("role")
    
    if ObjectId.is_valid(config_id) and len(str(config_id)) == 24:
        existing = await db.appointment_configs.find_one({"_id": ObjectId(config_id)})
        if existing and str(existing.get("employeeId")) != curr_user_id and user_role not in ["Admin", "HR"]:
            raise HTTPException(status_code=403, detail="Not authorized to delete this configuration")
            
    success = await crud.delete_appointment_config(db, config_id)
    if not success:
        raise HTTPException(status_code=404, detail="Configuration not found or invalid ID")
    return {"status": "success", "deleted": True}

@app.get("/appointments/public/slots")
@app.get("/api/appointments/public/slots")
async def get_public_slots(employeeId: str, date: str, configId: Optional[str] = None, db=Depends(get_db)):
    if not employeeId or not date:
        raise HTTPException(status_code=400, detail="employeeId and date are required")
    slots = await crud.calculate_public_slots(db, employeeId, date, config_id=configId)
    return {"slots": slots}

@app.post("/appointments/public/book")
@app.post("/api/appointments/public/book")
async def public_book_appointment(booking: dict, db=Depends(get_db)):
    employee_id = booking.get("employeeId")
    date_str = booking.get("date")
    start_time = booking.get("startTime")
    end_time = booking.get("endTime")
    config_id = booking.get("configId") or booking.get("linkId")
    
    if not all([employee_id, date_str, start_time, end_time]):
        raise HTTPException(status_code=400, detail="Missing required booking details")
        
    all_schedules = await crud.get_schedules(db, date_str=date_str)
    if all_schedules:
        for existing in all_schedules:
            if str(existing.get("employeeId")) != str(employee_id) and str(employee_id) not in [str(x) for x in existing.get("attendees", []) or []]:
                continue
            ex_start = existing.get("startTime")
            ex_end = existing.get("endTime")
            if ex_start and ex_end:
                if max(start_time, ex_start) < min(end_time, ex_end):
                    raise HTTPException(status_code=400, detail="Requested slot is no longer available.")

    from bson import ObjectId
    q = {"_id": ObjectId(employee_id)} if ObjectId.is_valid(employee_id) else {"_id": employee_id}
    emp = await db.employees.find_one(q)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    client_name = booking.get("attendeeName", "Client")
    client_email = booking.get("attendeeEmail", "")
    reason = booking.get("description", "")

    config = None
    if config_id and ObjectId.is_valid(config_id) and len(str(config_id)) == 24:
        config = await db.appointment_configs.find_one({"_id": ObjectId(config_id)})
    if not config and ObjectId.is_valid(employee_id) and len(str(employee_id)) == 24:
        config = await db.appointment_configs.find_one({"_id": ObjectId(employee_id)})
    if not config:
        config = await db.appointment_configs.find_one({"employeeId": employee_id})

    co_host_ids = []
    real_emp_id = employee_id
    if config:
        if config.get("employeeIds"):
            co_host_ids = [str(x) for x in config.get("employeeIds")]
        if config.get("employeeId"):
            real_emp_id = str(config.get("employeeId"))
            if real_emp_id != str(employee_id):
                real_q = {"_id": ObjectId(real_emp_id)} if ObjectId.is_valid(real_emp_id) else {"_id": real_emp_id}
                real_emp = await db.employees.find_one(real_q)
                if real_emp:
                    emp = real_emp
                    employee_id = real_emp_id
    
    schedule_data = {
        "title": booking.get("title", f"Appointment with {client_name}"),
        "description": f"Client Name: {client_name}\nEmail: {client_email}\nNotes: {reason}",
        "employeeId": employee_id,
        "employeeName": emp.get("name", "Unknown"),
        "startTime": start_time,
        "endTime": end_time,
        "type": "appointment",
        "attendees": co_host_ids,
        "createdBy": "public"
    }
    
    from datetime import datetime
    schedule_data["date"] = datetime.strptime(date_str, "%Y-%m-%d")
    
    created = await crud.create_schedule(db, schedule_data)
    return created


# --- Google Calendar Integration API ---
from fastapi.responses import RedirectResponse

@app.get("/auth/google/url")
async def get_google_auth_url(employeeId: str, desktop: bool = False):
    """Generates the Google OAuth URL to link a user's account."""
    try:
        state_str = f"{employeeId}:desktop" if desktop else employeeId
        url = google_auth.get_authorization_url(state=state_str)
        return RedirectResponse(url=url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/google/callback")
@app.get("/api/google/callback")
async def google_auth_callback(code: str, state: str, db=Depends(get_db)):
    """Handles the OAuth callback, exchanges code for tokens, and saves them."""
    try:
        # State parameter carries the employeeId and optional desktop flag
        parts = state.split(":")
        employee_id = parts[0]
        is_desktop = len(parts) > 1 and parts[1] == "desktop"
        
        tokens = google_auth.fetch_tokens(code, state)
        
        from bson import ObjectId
        query = {"employeeId": employee_id}
        if ObjectId.is_valid(employee_id):
            query = {"$or": [{"employeeId": employee_id}, {"_id": ObjectId(employee_id)}]}
            
        # Save tokens to the employee document
        result = await db.employees.update_one(
            query,
            {"$set": {"googleCalendarTokens": tokens}}
        )
        
        if result.modified_count == 0:
            print(f"Warning: Failed to update Google Calendar Tokens for user {employee_id}. User not found.")
            
        if is_desktop:
            from fastapi.responses import HTMLResponse
            html_content = """
            <html>
            <head>
              <title>Google Calendar Connected</title>
              <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background-color: #f3f4f6; }
                .card { background: white; padding: 40px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); text-align: center; max-width: 400px; }
                h1 { color: #0d9488; margin-top: 0; font-size: 24px; }
                p { color: #4b5563; font-size: 15px; line-height: 1.5; margin: 10px 0; }
                .success-icon { font-size: 54px; margin-bottom: 15px; }
              </style>
            </head>
            <body>
              <div class="card">
                <div class="success-icon">✅</div>
                <h1>Google Calendar Connected!</h1>
                <p>Your Google Calendar has been successfully linked to your HRMS account.</p>
                <p>You can now close this browser tab and return to the HRMS Desktop Application.</p>
              </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=200)
            
        # Redirect the user back to the frontend schedule page
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3535")
        return RedirectResponse(url=f"{frontend_url}/schedule?google_linked=true")
    except Exception as e:
        print(f"Google OAuth Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to link Google Calendar.")

@app.post("/auth/google/disconnect")
async def disconnect_google_calendar(employeeId: str, db=Depends(get_db)):
    try:
        from bson import ObjectId
        query = {"employeeId": employeeId}
        if ObjectId.is_valid(employeeId):
            query = {"$or": [{"employeeId": employeeId}, {"_id": ObjectId(employeeId)}]}
            
        emp = await db.employees.find_one(query)
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found.")
            
        if emp.get("googleCalendarTokens"):
            # They have connected. Let's delete events.
            try:
                import asyncio
                import google_calendar
                creds = await crud._get_creds_and_persist(db, emp)
                if creds:
                    # Find all schedules with a googleEventId
                    cursor = db.schedules.find({
                        "employeeId": employeeId,
                        "googleEventId": {"$exists": True, "$ne": None}
                    })
                    schedules = await cursor.to_list(length=10000)
                    
                    for sched in schedules:
                        gid = sched.get("googleEventId")
                        if sched.get("type") == "Google Event":
                            # Event originated from Google, so just delete the local copy
                            await db.schedules.delete_one({"_id": sched["_id"]})
                        else:
                            # It's an HRMS event pushed to Google, delete from Google
                            try:
                                await asyncio.to_thread(google_calendar.delete_event, creds, gid)
                            except Exception as e:
                                print(f"Error deleting event {gid} from Google Calendar: {e}")
                            # Remove the googleEventId from the local HRMS schedule
                            await db.schedules.update_one(
                                {"_id": sched["_id"]},
                                {"$unset": {"googleEventId": ""}}
                            )
            except Exception as e:
                print(f"Error cleaning up Google Calendar events during disconnect: {e}")
            
        result = await db.employees.update_one(
            {"_id": emp["_id"]},
            {"$unset": {"googleCalendarTokens": ""}}
        )
        return {"message": "Successfully disconnected Google Calendar."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhooks/google-calendar")
async def google_calendar_webhook(request: Request, db=Depends(get_db)):
    """
    Receives push notifications from Google Calendar when an event changes.
    The headers contain 'X-Goog-Resource-ID' and 'X-Goog-Channel-ID'.
    When a notification arrives, we look up the employee whose calendar changed
    and perform a sync for the current week to update local schedules.
    """
    channel_id = request.headers.get('X-Goog-Channel-ID')
    resource_state = request.headers.get('X-Goog-Resource-State', '')
    
    # Google sends a 'sync' message when the watch is first created — ignore it
    if resource_state == 'sync':
        return {"status": "ok"}
    
    if not channel_id:
        return {"status": "ok"}
    
    try:
        # channel_id is expected to be the employeeId (set during watch registration)
        employee_id = channel_id
        
        from datetime import datetime, timedelta
        now = crud.get_now()
        # Sync a 2-week window around today
        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = (now + timedelta(days=7)).strftime("%Y-%m-%d")
        
        await crud.sync_google_events(db, employee_id, start_date, end_date)
        print(f"[Webhook] Synced Google Calendar for employee {employee_id} (state: {resource_state})")
    except Exception as e:
        print(f"[Webhook] Error processing Google Calendar webhook: {e}")
    
    return {"status": "ok"}

@app.post("/schedules/sync")
async def manual_sync_schedules(request: dict, db=Depends(get_db)):
    """
    Manually trigger a Google Calendar sync for a specific employee.
    Body: { "employeeId": "...", "dateFrom": "YYYY-MM-DD", "dateTo": "YYYY-MM-DD" }
    """
    employee_id = request.get("employeeId")
    if not employee_id:
        raise HTTPException(status_code=400, detail="employeeId is required")
    
    from datetime import datetime, timedelta
    now = crud.get_now()
    date_from = request.get("dateFrom", (now - timedelta(days=7)).strftime("%Y-%m-%d"))
    date_to = request.get("dateTo", (now + timedelta(days=7)).strftime("%Y-%m-%d"))
    
    await crud.sync_google_events(db, employee_id, date_from, date_to)
    return {"message": "Sync completed", "employeeId": employee_id, "dateFrom": date_from, "dateTo": date_to}

# --- User Activity Input Tracking API ---
@app.post("/activity/track/{employee_id}", response_model=schemas.UserInputStats)
async def track_activity(employee_id: str, input_data: schemas.UserInputStatsCreate, db=Depends(get_db)):
    return await crud.track_user_activity(db, employee_id, input_data.clicks, input_data.keystrokes)

@app.get("/activity/stats", response_model=List[schemas.UserInputStats])
async def get_activity_stats(employeeId: Optional[str] = None, db=Depends(get_db)):
    return await crud.get_user_activity_stats(db, employee_id=employeeId)


@app.post("/activity/session-active/{employee_id}")
async def set_active_session(employee_id: str, db=Depends(get_db)):
    import input_tracker
    await input_tracker.set_active_user(employee_id)
    
    # Update activeEmployee in registered_pcs for current hostname
    import socket
    import re
    from bson import ObjectId
    user_doc = await db.employees.find_one(
        {"_id": ObjectId(employee_id)} if len(employee_id) == 24 else {"_id": employee_id}
    )
    if user_doc:
        await db.registered_pcs.update_one(
            {"hostname": {"$regex": f"^{re.escape(socket.gethostname())}$", "$options": "i"}},
            {"$set": {"activeEmployee": user_doc.get("name", "Unknown")}}
        )
    return {"message": "Session tracking started"}

@app.post("/activity/session-inactive")
async def clear_active_session(db=Depends(get_db)):
    import input_tracker
    input_tracker.clear_active_user()
    
    # Clear activeEmployee in registered_pcs for current hostname
    import socket
    import re
    await db.registered_pcs.update_one(
        {"hostname": {"$regex": f"^{re.escape(socket.gethostname())}$", "$options": "i"}},
        {"$set": {"activeEmployee": ""}}
    )
    return {"message": "Session tracking stopped"}

@app.get("/activity/last-active")
async def get_last_active(employee_id: Optional[str] = None, db=Depends(get_db)):
    if employee_id:
        from datetime import datetime
        import pytz
        from database import db as mongo_db
        IST = pytz.timezone('Asia/Kolkata')
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        
        stat = await mongo_db.user_input_stats.find_one({"employeeId": employee_id, "date": today_str})
        if stat and "lastActive" in stat:
            last_active_dt = stat["lastActive"]
            if last_active_dt:
                # Convert naive datetime in UTC or localized datetime to timestamp
                if last_active_dt.tzinfo is None:
                    # Naive datetime in MongoDB is typically stored as UTC.
                    # But if the app stored it as local naive or UTC naive, let's treat it as UTC first,
                    # or localized. Let's make it aware.
                    last_active_dt = last_active_dt.replace(tzinfo=pytz.UTC).astimezone(IST)
                else:
                    last_active_dt = last_active_dt.astimezone(IST)
                return {"last_active": last_active_dt.timestamp()}
                
    import input_tracker
    return {"last_active": input_tracker.get_last_global_activity_time()}

# --- PC Device Restrictions & Broadcasts APIs ---
import socket
import platform

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@app.get("/system/info")
async def get_system_info():
    return {
        "hostname": socket.gethostname(),
        "ipAddress": get_local_ip(),
        "os": platform.system(),
        "osVersion": platform.release()
    }

@app.get("/restrictions/pcs", response_model=List[schemas.RegisteredPC])
async def read_registered_pcs(
    db=Depends(get_db),
    _token=Depends(auth.require_auth)  # Must be logged in to see PC list
):
    return await crud.get_registered_pcs(db)

@app.put("/restrictions/pcs/{hostname}", response_model=schemas.RegisteredPC)
async def update_pcs_restrictions(
    hostname: str,
    pc_update: schemas.RegisteredPCUpdate,
    db=Depends(get_db),
    _admin=Depends(auth.require_admin)  # Admin ONLY — employees cannot modify restrictions
):
    updated = await crud.update_pc_restrictions(
        db,
        hostname,
        block_chrome=pc_update.blockChrome,
        block_youtube=pc_update.blockYoutube,
        block_apps=pc_update.blockApps,
        block_urls=pc_update.blockUrls
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Registered PC not found")
    return updated

@app.post("/system/broadcast")
async def system_broadcast(
    payload: dict,
    _admin=Depends(auth.require_admin)  # Admin ONLY — employees cannot send broadcasts
):
    title = payload.get("title", "System Broadcast")
    message = payload.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    await ws_manager.broadcast_all("system_alert", {"title": title, "message": message})
    return {"message": "Announcement broadcasted successfully"}



@app.get("/security/alerts")
async def get_security_alerts(
    resolved: bool = None,
    db=Depends(get_db),
    _token=Depends(auth.require_auth)  # Any logged-in user (admin sees panel)
):
    """Return all security tamper alerts from MongoDB, newest first."""
    query = {}
    if resolved is not None:
        query["resolved"] = resolved
    cursor = db.security_alerts.find(query).sort("timestamp", -1).limit(200)
    alerts = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        alerts.append(doc)
    return alerts

@app.put("/security/alerts/{alert_id}/resolve")
async def resolve_security_alert(
    alert_id: str,
    db=Depends(get_db),
    _admin=Depends(auth.require_admin)  # Admin only
):
    """Mark a security alert as resolved."""
    from bson import ObjectId as ObjId
    try:
        result = await db.security_alerts.update_one(
            {"_id": ObjId(alert_id)},
            {"$set": {"resolved": True, "resolvedAt": datetime.utcnow()}}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"message": "Alert marked as resolved"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Dynamic Feedback Forms

@app.post("/forms", response_model=schemas.FeedbackForm)
async def create_feedback_form(form: schemas.FeedbackFormCreate, createdBy: Optional[str] = None, db=Depends(get_db)):
    return await crud.create_feedback_form(db, form, createdBy=createdBy or "Unknown")

@app.get("/forms/all/forms", response_model=List[schemas.FeedbackForm])
async def read_all_forms(db=Depends(get_db)):
    return await crud.get_all_feedback_forms(db)

@app.get("/forms/all/responses", response_model=List[schemas.FeedbackResponse])
async def read_all_responses(db=Depends(get_db)):
    return await crud.get_all_feedback_responses(db)

@app.get("/forms/{form_id}", response_model=schemas.FeedbackForm)
async def get_feedback_form(form_id: str, db=Depends(get_db)):
    form = await crud.get_feedback_form(db, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    return form

@app.get("/forms/client/{client_id}", response_model=List[schemas.FeedbackForm])
async def get_client_feedback_forms(client_id: str, db=Depends(get_db)):
    return await crud.get_client_feedback_forms(db, client_id)

@app.put("/forms/{form_id}", response_model=schemas.FeedbackForm)
async def update_feedback_form(form_id: str, form: schemas.FeedbackFormCreate, db=Depends(get_db)):
    updated_form = await crud.update_feedback_form(db, form_id, form)
    if not updated_form:
        raise HTTPException(status_code=404, detail="Form not found")
    return updated_form

@app.delete("/forms/{form_id}")
async def delete_feedback_form(form_id: str, db=Depends(get_db)):
    deleted = await crud.delete_feedback_form(db, form_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Form not found")
    return {"message": "Form deleted successfully"}

@app.post("/forms/{form_id}/responses", response_model=schemas.FeedbackResponse)
async def submit_feedback_response(form_id: str, response: schemas.FeedbackResponseCreate, db=Depends(get_db)):
    if response.formId != form_id:
        raise HTTPException(status_code=400, detail="Form ID mismatch")
    return await crud.create_feedback_response(db, response)

@app.get("/forms/{form_id}/responses", response_model=List[schemas.FeedbackResponse])
async def get_form_responses(form_id: str, db=Depends(get_db)):
    return await crud.get_form_responses(db, form_id)

@app.get("/forms/client/{client_id}/responses", response_model=List[schemas.FeedbackResponse])
async def get_client_form_responses(client_id: str, db=Depends(get_db)):
    return await crud.get_client_form_responses(db, client_id)
# --- Desktop Auto-Update Endpoints ---

@app.get("/desktop/download/{filename}")
async def download_desktop_file(filename: str):
    """Serve desktop installer .exe with proper Content-Disposition download headers."""
    from fastapi.responses import FileResponse
    import re
    if not re.match(r'^HRMS_Setup_[\w.\-]+\.exe$', filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = os.path.join(UPLOAD_DIR, "desktop", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=file_path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/desktop/version")
async def get_desktop_version(db=Depends(get_db)):
    """Retrieve the latest desktop app version, download URL, and changelog."""
    release = await db.desktop_releases.find_one(sort=[("created_at", -1)])
    if not release:
        return {
            "version": "1.0.0",
            "downloadUrl": "",
            "changelog": []
        }
    return {
        "version": release.get("version"),
        "downloadUrl": release.get("downloadUrl"),
        "changelog": release.get("changelog", [])
    }

@app.post("/desktop/release")
async def upload_desktop_release(
    version: str = Form(...),
    changelog: str = Form(...),  # JSON string or comma-separated
    file: UploadFile = File(...),
    token_payload: dict = Depends(auth.require_admin),
    db=Depends(get_db)
):
    """Admin-only endpoint to upload a new compiled desktop installer .exe and log its version."""
    import shutil
    import json
    import traceback
    
    try:
        # Create directory uploads/desktop if it doesn't exist
        desktop_dir = os.path.join(UPLOAD_DIR, "desktop")
        if not os.path.exists(desktop_dir):
            os.makedirs(desktop_dir)
            
        # Standardize filename to prevent path traversal issues
        safe_version = "".join([c for c in version if c.isalnum() or c in ".-_"])
        filename = f"HRMS_Setup_{safe_version}.exe"
        file_path = os.path.join(desktop_dir, filename)
        
        # Save the file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Generate download URL (relative path)
        download_url = f"/api/desktop/download/{filename}"
        
        # Parse changelog
        changelog_list = []
        try:
            changelog_list = json.loads(changelog)
            if not isinstance(changelog_list, list):
                changelog_list = [str(changelog_list)]
        except Exception:
            # Fallback to newline separation
            changelog_list = [line.strip() for line in changelog.split("\n") if line.strip()]
            if not changelog_list:
                changelog_list = [line.strip() for line in changelog.split(",") if line.strip()]
                
        # Insert new release into DB
        from datetime import datetime
        new_release = {
            "version": version,
            "downloadUrl": download_url,
            "changelog": changelog_list,
            "created_at": datetime.utcnow().isoformat()
        }
        result = await db.desktop_releases.insert_one(new_release)
        inserted_id = result.inserted_id
        
        return {
            "message": "Release uploaded successfully",
            "release": {**new_release, "_id": str(inserted_id)}
        }
    except Exception as e:
        err_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
        print("[Desktop Release Error]", err_msg, flush=True)




# --- Content Calendar API ---
@app.get("/content-calendar/all")
async def get_all_content_calendar_entries(db=Depends(get_db)):
    try:
        return await crud.get_all_content_calendar_entries(db)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"error": str(e), "trace": traceback.format_exc()}

@app.get("/content-calendar/entry/{entry_id}")
async def get_single_content_calendar_entry(entry_id: str, db=Depends(get_db)):
    try:
        entry = await crud.get_content_calendar_entry(db, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        return entry
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/content-calendar")
async def get_content_calendar_entries(clientId: str, projectId: Optional[str] = None, monthYear: Optional[str] = None, includeLegacy: bool = False, db=Depends(get_db)):
    try:
        return await crud.get_content_calendar_entries(db, client_id=clientId, project_id=projectId, month_year=monthYear, include_legacy=includeLegacy)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"error": str(e), "trace": traceback.format_exc()}

@app.post("/content-calendar", response_model=schemas.ContentCalendarEntry)
async def create_content_calendar_entry(entry: schemas.ContentCalendarEntryCreate, db=Depends(get_db)):
    return await crud.create_content_calendar_entry(db, entry.model_dump())

@app.put("/content-calendar/{entry_id}", response_model=schemas.ContentCalendarEntry)
async def update_content_calendar_entry(entry_id: str, entry: schemas.ContentCalendarEntryUpdate, db=Depends(get_db)):
    updated = await crud.update_content_calendar_entry(db, entry_id, entry.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Entry not found")
    return updated

@app.delete("/content-calendar/{entry_id}")
async def delete_content_calendar_entry(entry_id: str, db=Depends(get_db)):
    success = await crud.delete_content_calendar_entry(db, entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"message": "Entry deleted successfully"}

@app.get("/content-calendar-settings", response_model=schemas.ContentCalendarSettingsBase)
async def get_content_calendar_settings(clientId: str, monthYear: str, projectId: Optional[str] = None, db=Depends(get_db)):
    settings = await crud.get_content_calendar_settings(db, clientId, monthYear, projectId)
    if settings:
        return settings
    # Return empty if not found
    return {
        "clientId": clientId,
        "monthYear": monthYear
    }

@app.get("/content-calendar-settings/all", response_model=List[schemas.ContentCalendarSettingsBase])
async def get_all_content_calendar_settings(monthYear: str, db=Depends(get_db)):
    return await crud.get_all_content_calendar_settings(db, monthYear)

@app.post("/content-calendar-settings", response_model=schemas.ContentCalendarSettings)
async def upsert_content_calendar_settings(settings: schemas.ContentCalendarSettingsBase, db=Depends(get_db)):
    return await crud.upsert_content_calendar_settings(
        db, settings.clientId, settings.monthYear, settings.projectId, settings.model_dump()
    )

# Dynamic Feedback Forms

@app.post("/forms", response_model=schemas.FeedbackForm)
async def create_feedback_form(form: schemas.FeedbackFormCreate, createdBy: Optional[str] = None, db=Depends(get_db)):
    return await crud.create_feedback_form(db, form, createdBy=createdBy or "Unknown")

@app.get("/forms/all/forms", response_model=List[schemas.FeedbackForm])
async def read_all_forms(db=Depends(get_db)):
    return await crud.get_all_feedback_forms(db)

@app.get("/forms/all/responses", response_model=List[schemas.FeedbackResponse])
async def read_all_responses(db=Depends(get_db)):
    return await crud.get_all_feedback_responses(db)

@app.get("/forms/{form_id}", response_model=schemas.FeedbackForm)
async def get_feedback_form(form_id: str, db=Depends(get_db)):
    form = await crud.get_feedback_form(db, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    return form

@app.get("/forms/client/{client_id}", response_model=List[schemas.FeedbackForm])
async def get_client_feedback_forms(client_id: str, db=Depends(get_db)):
    return await crud.get_client_feedback_forms(db, client_id)

@app.put("/forms/{form_id}", response_model=schemas.FeedbackForm)
async def update_feedback_form(form_id: str, form: schemas.FeedbackFormCreate, db=Depends(get_db)):
    updated_form = await crud.update_feedback_form(db, form_id, form)
    if not updated_form:
        raise HTTPException(status_code=404, detail="Form not found")
    return updated_form

@app.delete("/forms/{form_id}")
async def delete_feedback_form(form_id: str, db=Depends(get_db)):
    deleted = await crud.delete_feedback_form(db, form_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Form not found")
    return {"message": "Form deleted successfully"}

@app.post("/forms/{form_id}/responses", response_model=schemas.FeedbackResponse)
async def submit_feedback_response(form_id: str, response: schemas.FeedbackResponseCreate, db=Depends(get_db)):
    if response.formId != form_id:
        raise HTTPException(status_code=400, detail="Form ID mismatch")
    return await crud.create_feedback_response(db, response)

@app.get("/forms/{form_id}/responses", response_model=List[schemas.FeedbackResponse])
async def get_form_responses(form_id: str, db=Depends(get_db)):
    return await crud.get_form_responses(db, form_id)

@app.get("/forms/client/{client_id}/responses", response_model=List[schemas.FeedbackResponse])
async def get_client_form_responses(client_id: str, db=Depends(get_db)):
    return await crud.get_client_form_responses(db, client_id)
# --- Desktop Auto-Update Endpoints ---
@app.get("/desktop/version")
async def get_desktop_version(db=Depends(get_db)):
    """Retrieve the latest desktop app version, download URL, and changelog."""
    release = await db.desktop_releases.find_one(sort=[("created_at", -1)])
    if not release:
        return {
            "version": "1.0.0",
            "downloadUrl": "",
            "changelog": []
        }
    return {
        "version": release.get("version"),
        "downloadUrl": release.get("downloadUrl"),
        "changelog": release.get("changelog", [])
    }

@app.post("/desktop/release")
async def upload_desktop_release(
    version: str = Form(...),
    changelog: str = Form(...),  # JSON string or comma-separated
    file: UploadFile = File(...),
    token_payload: dict = Depends(auth.require_admin),
    db=Depends(get_db)
):
    """Admin-only endpoint to upload a new compiled desktop installer .exe and log its version."""
    import shutil
    import json
    import traceback
    
    try:
        # Create directory uploads/desktop if it doesn't exist
        desktop_dir = os.path.join(UPLOAD_DIR, "desktop")
        if not os.path.exists(desktop_dir):
            os.makedirs(desktop_dir)
            
        # Standardize filename to prevent path traversal issues
        safe_version = "".join([c for c in version if c.isalnum() or c in ".-_"])
        filename = f"HRMS_Setup_{safe_version}.exe"
        file_path = os.path.join(desktop_dir, filename)
        
        # Save the file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Generate download URL (relative path)
        download_url = f"/api/desktop/download/{filename}"
        
        # Parse changelog
        changelog_list = []
        try:
            changelog_list = json.loads(changelog)
            if not isinstance(changelog_list, list):
                changelog_list = [str(changelog_list)]
        except Exception:
            # Fallback to newline separation
            changelog_list = [line.strip() for line in changelog.split("\n") if line.strip()]
            if not changelog_list:
                changelog_list = [line.strip() for line in changelog.split(",") if line.strip()]
                
        # Insert new release into DB
        from datetime import datetime
        new_release = {
            "version": version,
            "downloadUrl": download_url,
            "changelog": changelog_list,
            "created_at": datetime.utcnow().isoformat()
        }
        result = await db.desktop_releases.insert_one(new_release)
        inserted_id = result.inserted_id
        
        return {
            "message": "Release uploaded successfully",
            "release": {**new_release, "_id": str(inserted_id)}
        }
    except Exception as e:
        err_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
        print("[Desktop Release Error]", err_msg, flush=True)
        raise HTTPException(status_code=500, detail=err_msg)

# --- Other Work API ---
@app.get("/other-work/all", response_model=List[schemas.OtherWork])
async def get_all_other_work(db=Depends(get_db)):
    try:
        return await crud.get_all_other_work(db)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return []

@app.post("/other-work", response_model=schemas.OtherWork)
async def create_other_work(entry: schemas.OtherWorkCreate, db=Depends(get_db)):
    return await crud.create_other_work(db, entry.model_dump())

@app.put("/other-work/{entry_id}", response_model=schemas.OtherWork)
async def update_other_work(entry_id: str, entry: schemas.OtherWorkUpdate, db=Depends(get_db)):
    updated = await crud.update_other_work(db, entry_id, entry.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Entry not found")
    return updated

@app.delete("/other-work/{entry_id}")
async def delete_other_work(entry_id: str, db=Depends(get_db)):
    success = await crud.delete_other_work(db, entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"message": "Entry deleted successfully"}

# --- Work Transfer Request API ---
@app.get("/work-transfer-requests", response_model=List[schemas.WorkTransferRequest])
async def get_all_transfer_requests(task_id: Optional[str] = None, task_type: Optional[str] = None, taskType: Optional[str] = None, db=Depends(get_db)):
    actual_task_type = taskType or task_type
    return await crud.get_all_transfer_requests(db, task_id, actual_task_type)

@app.post("/work-transfer-requests", response_model=schemas.WorkTransferRequest)
async def create_transfer_request(request: schemas.WorkTransferRequestCreate, db=Depends(get_db)):
    try:
        return await crud.create_transfer_request(db, request.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/work-transfer-requests/incoming/{employee_id}", response_model=List[schemas.WorkTransferRequest])
async def get_incoming_transfer_requests(employee_id: str, task_type: Optional[str] = None, taskType: Optional[str] = None, db=Depends(get_db)):
    actual_task_type = taskType or task_type
    return await crud.get_incoming_transfer_requests(db, employee_id, actual_task_type)

@app.get("/work-transfer-requests/outgoing/{employee_id}", response_model=List[schemas.WorkTransferRequest])
async def get_outgoing_transfer_requests(employee_id: str, task_type: Optional[str] = None, taskType: Optional[str] = None, db=Depends(get_db)):
    actual_task_type = taskType or task_type
    return await crud.get_outgoing_transfer_requests(db, employee_id, actual_task_type)

@app.put("/work-transfer-requests/{request_id}/respond", response_model=schemas.WorkTransferRequest)
async def respond_to_transfer_request(request_id: str, payload: dict, db=Depends(get_db)):
    status = payload.get("status")
    if status not in ["Accepted", "Rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    updated = await crud.respond_to_transfer_request(db, request_id, status)
    if not updated:
        raise HTTPException(status_code=404, detail="Request not found")
    return updated

# --- Gallery Endpoints ---
@app.get("/gallery", response_model=List[schemas.Gallery])
async def read_galleries(skip: int = 0, limit: int = 100, db=Depends(get_db)):
    return await crud.get_galleries(db, skip, limit)

@app.post("/gallery", response_model=schemas.Gallery)
async def create_gallery_entry(gallery: schemas.GalleryCreate, db=Depends(get_db)):
    return await crud.create_gallery(db, gallery)

@app.put("/gallery/{gallery_id}", response_model=schemas.Gallery)
async def update_gallery_entry(gallery_id: str, payload: dict, db=Depends(get_db)):
    if "date" in payload and payload["date"] == "":
        payload["date"] = None
    updated = await crud.update_gallery(db, gallery_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return updated

@app.delete("/gallery/{gallery_id}")
async def delete_gallery_entry(gallery_id: str, db=Depends(get_db)):
    success = await crud.delete_gallery(db, gallery_id)
    if not success:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return {"status": "success"}

# --- Company Finance Endpoints ---
@app.get("/company-finance/transactions")
@redis_manager.cached_api(namespace="hrms:finance", ttl=180)
async def get_finance_transactions_endpoint(
    request: Request,
    paymentMethod: Optional[str] = None,
    payment_method: Optional[str] = None,
    type: Optional[str] = None,
    search: Optional[str] = None,
    db=Depends(get_db)
):
    actual_pm = paymentMethod or payment_method
    txs = await crud.get_finance_transactions(db, payment_method=actual_pm, type_filter=type, search=search)
    return {"transactions": txs}

@app.post("/company-finance/transactions", response_model=schemas.FinanceTransaction)
async def create_finance_transaction_endpoint(tx: schemas.FinanceTransactionCreate, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    res = await crud.create_finance_transaction(db, tx.model_dump(), current_user)
    await redis_manager.invalidate_namespace("hrms:finance")
    return res

@app.put("/company-finance/transactions/{tx_id}", response_model=schemas.FinanceTransaction)
async def update_finance_transaction_endpoint(tx_id: str, tx_update: schemas.FinanceTransactionUpdate, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    updated = await crud.update_finance_transaction(db, tx_id, tx_update.model_dump(exclude_unset=True), current_user)
    if not updated:
        raise HTTPException(status_code=404, detail="Transaction not found")
    await redis_manager.invalidate_namespace("hrms:finance")
    return updated

@app.delete("/company-finance/transactions/{tx_id}")
async def delete_finance_transaction_endpoint(tx_id: str, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    success = await crud.delete_finance_transaction(db, tx_id, current_user)
    if not success:
        raise HTTPException(status_code=404, detail="Transaction not found")
    await redis_manager.invalidate_namespace("hrms:finance")
    return {"message": "Transaction deleted successfully"}

@app.get("/company-finance/balances", response_model=schemas.FinanceBalance)
@redis_manager.cached_api(namespace="hrms:finance", ttl=180)
async def get_finance_balances_endpoint(request: Request, db=Depends(get_db)):
    return await crud.get_finance_balances(db)

@app.put("/company-finance/balances", response_model=schemas.FinanceBalance)
async def update_finance_balances_endpoint(payload: dict, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    res = await crud.update_finance_balances(db, payload, current_user)
    await redis_manager.invalidate_namespace("hrms:finance")
    return res

@app.get("/company-finance/monthly-plans/{month}")
@redis_manager.cached_api(namespace="hrms:finance", ttl=180)
async def get_monthly_plan_endpoint(request: Request, month: str, db=Depends(get_db)):
    return await crud.get_monthly_plan(db, month)

@app.post("/company-finance/monthly-plans/{month}")
async def save_monthly_plan_endpoint(month: str, payload: dict, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    res = await crud.save_monthly_plan(db, month, payload.get("values", {}), current_user)
    await redis_manager.invalidate_namespace("hrms:finance")
    return res

@app.get("/company-finance/plans", response_model=dict)
@redis_manager.cached_api(namespace="hrms:finance", ttl=180)
async def get_finance_plans_endpoint(request: Request, db=Depends(get_db)):
    plans = await crud.get_finance_plans(db)
    return {"plans": plans}

@app.post("/company-finance/plans", response_model=schemas.FinancePlan)
async def create_finance_plan_endpoint(plan: schemas.FinancePlanCreate, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    res = await crud.create_finance_plan(db, plan.model_dump(), current_user)
    await redis_manager.invalidate_namespace("hrms:finance")
    return res

@app.put("/company-finance/plans/{plan_id}", response_model=schemas.FinancePlan)
async def update_finance_plan_endpoint(plan_id: str, plan_update: schemas.FinancePlanUpdate, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    updated = await crud.update_finance_plan(db, plan_id, plan_update.model_dump(exclude_unset=True), current_user)
    if not updated:
        raise HTTPException(status_code=404, detail="Plan not found")
    await redis_manager.invalidate_namespace("hrms:finance")
    return updated

@app.delete("/company-finance/plans/{plan_id}")
async def delete_finance_plan_endpoint(plan_id: str, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    success = await crud.delete_finance_plan(db, plan_id, current_user)
    if not success:
        raise HTTPException(status_code=404, detail="Plan not found")
    await redis_manager.invalidate_namespace("hrms:finance")
    return {"message": "Plan deleted successfully"}

@app.get("/company-finance/summary", response_model=schemas.FinanceSummary)
async def get_finance_summary_endpoint(db=Depends(get_db)):
    return await crud.get_finance_summary(db)

@app.get("/company-finance/logs")
async def get_finance_logs_endpoint(
    performedBy: Optional[str] = None,
    action: Optional[str] = None,
    search: Optional[str] = None,
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db=Depends(get_db),
    current_user=Depends(auth.get_current_user_token)
):
    logs, total = await crud.get_finance_activity_logs(
        db, performedBy=performedBy, action=action, search=search,
        startDate=startDate, endDate=endDate, limit=limit, skip=skip
    )
    return {"logs": logs, "total": total}

# --- Row Definitions Endpoints ---
@app.get("/company-finance/row-definitions/{month}")
async def get_row_definitions_endpoint(month: str, db=Depends(get_db)):
    return await crud.get_row_definitions(db, month)

@app.post("/company-finance/row-definitions/{month}")
async def save_row_definitions_endpoint(month: str, payload: schemas.RowDefinitionsConfigUpdate, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    rows_data = [row.model_dump() for row in payload.rows]
    return await crud.save_row_definitions(db, month, rows_data, current_user)

# --- Summary Overrides Endpoints ---
@app.get("/company-finance/actual-overrides/{month}")
async def get_summary_actual_overrides_endpoint(month: str, db=Depends(get_db)):
    return await crud.get_summary_actual_overrides(db, month)

@app.post("/company-finance/actual-overrides/{month}")
async def save_summary_actual_overrides_endpoint(month: str, payload: schemas.SummaryOverridesUpdate, db=Depends(get_db), current_user=Depends(auth.get_current_user_token)):
    return await crud.save_summary_actual_overrides(db, month, payload.values, current_user)

# --- Task Preset Endpoints ---
@app.get("/task-presets")
async def read_task_presets(skip: int = 0, limit: int = 100, db=Depends(get_db)):
    return await crud.get_task_presets(db, skip, limit)

@app.post("/task-presets", response_model=schemas.TaskPreset)
async def create_task_preset_entry(preset: schemas.TaskPresetCreate, db=Depends(get_db)):
    return await crud.create_task_preset(db, preset)

@app.put("/task-presets/{preset_id}", response_model=schemas.TaskPreset)
async def update_task_preset_entry(preset_id: str, payload: dict, db=Depends(get_db)):
    updated = await crud.update_task_preset(db, preset_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Preset not found")
    return updated

    if not success:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"status": "success"}

@app.post("/task-presets/{preset_id}/assign")
async def assign_task_preset(preset_id: str, payload: dict, db=Depends(get_db)):
    from bson import ObjectId
    import datetime
    
    assignee_ids = payload.get("assignedToIds", [])
    if not assignee_ids and payload.get("assignedToId"):
        assignee_ids = [payload.get("assignedToId")]
        
    performer_id = payload.get("performedBy", "Unknown")
    user_name = payload.get("userName", "Unknown")
    
    if not assignee_ids:
        raise HTTPException(status_code=400, detail="assignee_ids is required")
        
    presets = await crud.get_task_presets(db, 0, 1000)
    target_preset = next((p for p in presets if str(p.get("_id")) == preset_id or str(p.get("id")) == preset_id), None)
    
    if not target_preset:
        raise HTTPException(status_code=404, detail="Preset not found")
        
    tasks = target_preset.get("tasks", [])
    created_tasks = []
    
    for assignee_id in assignee_ids:
        emp = await db.employees.find_one({"_id": ObjectId(assignee_id)})
        assignee_name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip() if emp else "Unknown"
        
        for pt in tasks:
            new_task = schemas.WMTaskCreate(
                title=pt.get("title"),
                description=pt.get("description", ""),
                projectId=pt.get("projectId"),
                projectName=pt.get("projectName"),
                department=pt.get("department", "development"),
                assignedToId=assignee_id,
                assignedToName=assignee_name,
                status="todo",
                priority="medium",
                estimatedHours=0,
                createdBy=performer_id,
                performedBy=performer_id,
                userName=user_name,
                postingDate=datetime.datetime.now().strftime("%Y-%m-%d"),
                dueDate=datetime.datetime.now().strftime("%Y-%m-%d")
            )
            created = await crud.create_wm_task(db, new_task)
            created_tasks.append(created)

    existing_assigned = target_preset.get("assignedToIds", [])
    new_assigned = list(set(existing_assigned + assignee_ids))
    
    try:
        preset_obj_id = ObjectId(preset_id) if len(preset_id) == 24 else preset_id
    except:
        preset_obj_id = preset_id
        
    await db.task_presets.update_one(
        {"_id": preset_obj_id},
        {"$set": {"assignedToIds": new_assigned}}
    )
            
    return {"message": "Success", "tasks_created": len(created_tasks), "tasks": created_tasks}


# --- Research API ---
@app.post("/research", response_model=schemas.ResearchResponse)
async def create_research(entry: schemas.ResearchCreate, db=Depends(get_db)):
    return await crud.create_research(db, entry.model_dump())

@app.get("/research", response_model=List[schemas.ResearchResponse])
async def get_research(user_id: str = Header(...), role: str = Header(...), db=Depends(get_db)):
    is_admin = role.lower() == "admin"
    return await crud.get_research(db, user_id, is_admin)

@app.put("/research/{entry_id}", response_model=schemas.ResearchResponse)
async def update_research(entry_id: str, entry: schemas.ResearchUpdate, db=Depends(get_db)):
    updated = await crud.update_research(db, entry_id, entry.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Entry not found")
    return updated

@app.delete("/research/{entry_id}")
async def delete_research(entry_id: str, db=Depends(get_db)):
    success = await crud.delete_research(db, entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"message": "Entry deleted successfully"}

# --- Client Transactions API ---
@app.get("/client-transactions", response_model=List[schemas.ClientTransaction])
async def get_client_transactions_endpoint(db=Depends(get_db)):
    return await crud.get_client_transactions(db)

@app.post("/client-transactions", response_model=schemas.ClientTransaction)
async def create_client_transaction_endpoint(tx: schemas.ClientTransactionCreate, db=Depends(get_db)):
    return await crud.create_client_transaction(db, tx.model_dump())

@app.put("/client-transactions/{tx_id}", response_model=schemas.ClientTransaction)
async def update_client_transaction_endpoint(tx_id: str, tx_update: schemas.ClientTransactionUpdate, db=Depends(get_db)):
    updated = await crud.update_client_transaction(db, tx_id, tx_update.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return updated

@app.delete("/client-transactions/{tx_id}")
async def delete_client_transaction_endpoint(tx_id: str, db=Depends(get_db)):
    success = await crud.delete_client_transaction(db, tx_id)
    if not success:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"message": "Transaction deleted successfully"}

# --- Training / Courses API ---
@app.post("/courses", response_model=schemas.CourseResponse)
async def create_course(course: schemas.CourseCreate, db=Depends(get_db)):
    res = await crud.create_course(db, course)
    await redis_manager.invalidate_namespace("hrms:training")
    return res

@app.get("/courses", response_model=List[schemas.CourseResponse])
@redis_manager.cached_api(namespace="hrms:training", ttl=180)
async def get_courses(request: Request, db=Depends(get_db)):
    return await crud.get_courses(db)

@app.get("/courses/{course_id}")
async def get_course_details(course_id: str, db=Depends(get_db)):
    course = await crud.get_course(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    
    modules = await crud.get_course_modules(db, course_id)
    for module in modules:
        lectures = await crud.get_course_lectures(db, str(module["id"]))
        module["lectures"] = lectures
        
    course["modules"] = modules
    return course

@app.put("/courses/{course_id}", response_model=schemas.CourseResponse)
async def update_course(course_id: str, course: schemas.CourseUpdate, db=Depends(get_db)):
    updated = await crud.update_course(db, course_id, course)
    if not updated:
        raise HTTPException(status_code=404, detail="Course not found")
    await redis_manager.invalidate_namespace("hrms:training")
    return updated

@app.delete("/courses/{course_id}")
async def delete_course(course_id: str, db=Depends(get_db)):
    success = await crud.delete_course(db, course_id)
    if not success:
        raise HTTPException(status_code=404, detail="Course not found")
    await redis_manager.invalidate_namespace("hrms:training")
    return {"message": "Course deleted successfully"}

@app.post("/course-modules", response_model=schemas.CourseModuleResponse)
async def create_course_module(module: schemas.CourseModuleCreate, db=Depends(get_db)):
    return await crud.create_course_module(db, module)

@app.put("/course-modules/{module_id}", response_model=schemas.CourseModuleResponse)
async def update_course_module(module_id: str, module: schemas.CourseModuleUpdate, db=Depends(get_db)):
    updated = await crud.update_course_module(db, module_id, module)
    if not updated:
        raise HTTPException(status_code=404, detail="Module not found")
    return updated

@app.delete("/course-modules/{module_id}")
async def delete_course_module(module_id: str, db=Depends(get_db)):
    success = await crud.delete_course_module(db, module_id)
    if not success:
        raise HTTPException(status_code=404, detail="Module not found")
    return {"message": "Module deleted successfully"}

@app.post("/course-lectures", response_model=schemas.CourseLectureResponse)
async def create_course_lecture(lecture: schemas.CourseLectureCreate, db=Depends(get_db)):
    return await crud.create_course_lecture(db, lecture)

@app.put("/course-lectures/{lecture_id}", response_model=schemas.CourseLectureResponse)
async def update_course_lecture(lecture_id: str, lecture: schemas.CourseLectureUpdate, db=Depends(get_db)):
    updated = await crud.update_course_lecture(db, lecture_id, lecture)
    if not updated:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return updated

@app.delete("/course-lectures/{lecture_id}")
async def delete_course_lecture(lecture_id: str, db=Depends(get_db)):
    success = await crud.delete_course_lecture(db, lecture_id)
    if not success:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return {"message": "Lecture deleted successfully"}
@app.put("/courses/{course_id}/assign")
async def assign_course_employees(course_id: str, payload: dict, db=Depends(get_db), token_payload: dict = Depends(auth.require_admin)):
    employee_ids = payload.get("employee_ids", [])
    updated = await crud.update_course(db, course_id, schemas.CourseUpdate(assigned_employee_ids=employee_ids))
    if not updated:
        raise HTTPException(status_code=404, detail="Course not found")
    return {"message": "Assigned successfully", "course": updated}

@app.post("/courses/{course_id}/progress/{lecture_id}", response_model=schemas.LectureProgressResponse)
async def update_lecture_progress(
    course_id: str, 
    lecture_id: str, 
    update_data: schemas.LectureProgressUpdate, 
    db=Depends(get_db), 
    token_payload: dict = Depends(auth.require_auth)
):
    employee_id = token_payload.get("sub")
    
    # We need to know module_id, let's fetch lecture to find it
    lecture = await crud.get_course_lecture(db, lecture_id) if hasattr(crud, "get_course_lecture") else None
    
    # Fallback to fetching directly if helper not present
    if not lecture:
        from bson import ObjectId
        lecture = await db.course_lectures.find_one({"_id": ObjectId(lecture_id) if len(lecture_id)==24 else lecture_id})
        if not lecture:
            raise HTTPException(status_code=404, detail="Lecture not found")
            
    module_id = str(lecture.get("module_id", ""))
    
    progress = schemas.LectureProgressBase(
        employee_id=employee_id,
        course_id=course_id,
        module_id=module_id,
        lecture_id=lecture_id,
        watched_seconds=update_data.watched_seconds,
        total_seconds=update_data.total_seconds,
        is_completed=update_data.watched_seconds >= update_data.total_seconds * 0.9 if update_data.total_seconds > 0 else False
    )
    
    return await crud.update_lecture_progress(db, progress)

@app.get("/courses/{course_id}/progress/my", response_model=List[schemas.LectureProgressResponse])
async def get_my_course_progress(course_id: str, db=Depends(get_db), token_payload: dict = Depends(auth.require_auth)):
    employee_id = token_payload.get("sub")
    return await crud.get_course_progress(db, course_id, employee_id)

@app.get("/courses/{course_id}/progress/all", response_model=List[schemas.LectureProgressResponse])
async def get_all_course_progress(course_id: str, db=Depends(get_db), token_payload: dict = Depends(auth.require_admin)):
    return await crud.get_all_course_progress(db, course_id)





@app.put("/user-permissions/bulk-module")
async def update_bulk_module_permissions(request: schemas.ModuleBulkUpdateRequest, db=Depends(get_db), ):
    return await crud.bulk_update_module_permissions(db, request, performed_by="System", user_name="System User")


# ==========================================
# STV VOTING SYSTEM MODULE ENDPOINTS
# ==========================================

async def _is_admin(token_payload: dict) -> bool:
    if not token_payload:
        return False
    role = str(token_payload.get("role", "")).lower().strip()
    admin_roles = {"admin", "super admin", "superadmin", "administrator", "founder"}
    if role in admin_roles or role == "admin":
        return True
    user_id = str(token_payload.get("sub", "")).strip()
    if user_id:
        try:
            from bson import ObjectId
            filter_q = {"_id": ObjectId(user_id)} if (len(user_id) == 24 and ObjectId.is_valid(user_id)) else {"_id": user_id}
            u_doc = await database.db.employees.find_one(filter_q)
            if not u_doc:
                u_doc = await database.db.employees.find_one({"id": user_id})
            if u_doc:
                u_role = str(u_doc.get("role") or "").lower().strip()
                if u_role in admin_roles or u_role == "admin" or u_doc.get("name") == "Admin Admin":
                    return True
        except Exception as e:
            print(f"Error in _is_admin: {e}", flush=True)
    return False


@app.post("/elections", response_model=schemas.ElectionOut)
async def create_election_endpoint(
    data: schemas.ElectionCreate,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin(token_payload):
        raise HTTPException(status_code=403, detail="Only Administrators can create elections.")
    user_id = token_payload.get("sub")
    return await crud.create_election(data, user_id)


@app.get("/elections", response_model=List[schemas.ElectionOut])
async def list_elections_endpoint(
    month: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    token_payload: dict = Depends(auth.require_auth)
):
    return await crud.get_elections(month=month, year=year)


@app.get("/elections/{election_id}", response_model=schemas.ElectionOut)
async def get_election_endpoint(
    election_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    election = await crud.get_election_by_id(election_id)
    if not election:
        raise HTTPException(status_code=404, detail="Election not found.")
    return election


@app.delete("/elections/{election_id}")
async def delete_election_endpoint(
    election_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin(token_payload):
        raise HTTPException(status_code=403, detail="Only Administrators can delete elections.")
    deleted = await crud.soft_delete_election(election_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Election not found or already deleted.")
    return {"message": "Election deleted successfully."}


@app.post("/elections/{election_id}/ballots")
async def submit_ballot_endpoint(
    election_id: str,
    data: schemas.BallotSubmit,
    token_payload: dict = Depends(auth.require_auth)
):
    voter_id = token_payload.get("sub")
    try:
        ballot = await crud.submit_ballot(election_id, voter_id, data.preferences)
        return {"message": "Vote submitted successfully.", "ballot": ballot}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/elections/{election_id}/my-ballot")
async def get_my_ballot_endpoint(
    election_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    voter_id = token_payload.get("sub")
    ballot = await crud.get_voter_ballot(election_id, voter_id)
    return ballot or {"isSubmitted": False, "preferences": []}


@app.post("/elections/{election_id}/run")
async def run_election_stv_endpoint(
    election_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin(token_payload):
        raise HTTPException(status_code=403, detail="Only Administrators can run STV calculation.")
    try:
        result = await crud.run_stv_round_calculation(election_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/elections/{election_id}/rounds")
async def get_election_rounds_endpoint(
    election_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin(token_payload):
        raise HTTPException(status_code=403, detail="Only Administrators can view round calculation details.")
    rounds = await crud.get_election_rounds_history(election_id)
    return rounds


@app.get("/elections/{election_id}/result")
async def get_election_result_endpoint(
    election_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    election = await crud.get_election_by_id(election_id)
    if not election:
        raise HTTPException(status_code=404, detail="Election not found.")
    return election


@app.get("/elections/{election_id}/voter-ballots")
async def get_voter_ballots_admin_endpoint(
    election_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin(token_payload):
        raise HTTPException(status_code=403, detail="Only Administrators can view individual voter ballots.")
    ballots = await crud.get_admin_voter_ballots(election_id)
    return ballots


async def _is_admin(token_payload: dict) -> bool:
    if not token_payload:
        return False
    admin_roles = {"admin", "super admin", "superadmin", "administrator", "founder"}
    role = str(token_payload.get("role", "")).lower().strip()
    if role in admin_roles:
        return True
    user_id = token_payload.get("sub")
    if user_id:
        from bson import ObjectId
        from database import db
        user = await db.employees.find_one({"_id": ObjectId(user_id) if len(user_id) == 24 else user_id})
        if user and str(user.get("role", "")).lower().strip() in admin_roles:
            return True
    return False

async def _is_admin_or_hr(token_payload: dict) -> bool:
    if not token_payload:
        return False
    allowed_roles = {"admin", "super admin", "superadmin", "administrator", "founder", "hr", "human resources", "hr manager"}
    role = str(token_payload.get("role", "")).lower().strip()
    if role in allowed_roles:
        return True
    user_id = token_payload.get("sub")
    if user_id:
        from bson import ObjectId
        from database import db
        user = await db.employees.find_one({"_id": ObjectId(user_id) if len(user_id) == 24 else user_id})
        if not user:
            user = await db.employees.find_one({"id": user_id})
        if user:
            u_role = str(user.get("role", "")).lower().strip()
            u_dept = str(user.get("department", "")).lower().strip()
            if u_role in allowed_roles or u_dept in ["hr", "human resources", "hr department"]:
                return True
        # Also check user_permissions
        perms = await db.user_permissions.find_one({"employeeId": str(user_id)})
        if perms:
            for p in perms.get("permissions", []):
                if p.get("moduleName") in ["employee-of-the-month", "eom", "eow", "weekly-meetings", "employee-of-the-week"] and (p.get("canEdit") is True or p.get("canCreate") is True):
                    return True
    return False

# --- EMPLOYEE OF THE MONTH (EOM) ENDPOINTS ---

import eom_service

@app.get("/eom/master-criteria")
async def get_eom_master_criteria_endpoint():
    return await eom_service.get_or_init_master_criteria()

@app.post("/eom/master-criteria")
async def save_eom_master_criteria_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can manage EOM master template.")
    data = await request.json()
    criteria_list = data.get("criteria", [])
    return await eom_service.save_master_criteria(criteria_list)

@app.get("/eom/month-history")
async def get_eom_month_history_endpoint():
    return await eom_service.get_eom_month_history()

@app.get("/eom/criteria")
async def get_eom_criteria_endpoint(month_year: str = Query(...)):
    try:
        return await eom_service.get_or_init_criteria(month_year)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/eom/criteria")
async def save_eom_criteria_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can manage EOM criteria.")
    data = await request.json()
    month_year = data.get("month_year")
    criteria_list = data.get("criteria", [])
    if not month_year:
        raise HTTPException(status_code=400, detail="month_year is required")
    try:
        return await eom_service.save_criteria(month_year, criteria_list)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/eom/clone-criteria")
async def clone_eom_criteria_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can clone criteria.")
    data = await request.json()
    from_month = data.get("from_month_year")
    to_month = data.get("to_month_year")
    if not from_month or not to_month:
        raise HTTPException(status_code=400, detail="from_month_year and to_month_year are required")
    return await eom_service.clone_criteria(from_month, to_month)

@app.post("/eom/scores")
async def save_eom_score_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    data = await request.json()
    month_year = data.get("month_year")
    criteria_id = data.get("criteriaId")
    employee_id = data.get("employeeId")
    score = data.get("score")
    raw_quantity = data.get("rawQuantity")
    calculated_rank = data.get("calculatedRank")
    
    if not month_year or not criteria_id or not employee_id or score is None:
        raise HTTPException(status_code=400, detail="Missing required score fields")

    actor_id, actor_name = await get_actor_from_request(request, database.db)
    return await eom_service.save_score(
        month_year=month_year,
        criteria_id=criteria_id,
        employee_id=employee_id,
        score=score,
        raw_quantity=raw_quantity,
        calculated_rank=calculated_rank,
        scored_by=actor_name,
        evaluator_id=actor_id
    )

@app.post("/eom/bulk-scores")
async def save_eom_bulk_scores_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    data = await request.json()
    month_year = data.get("month_year")
    criteria_id = data.get("criteriaId")
    category_type = data.get("categoryType", "+ve")
    max_score = float(data.get("maxScore", 10.0))
    entries = data.get("entries", [])
    
    if not month_year or not criteria_id:
        raise HTTPException(status_code=400, detail="Missing month_year or criteriaId")

    actor_id, actor_name = await get_actor_from_request(request, database.db)
    return await eom_service.bulk_save_hybrid_scores(
        month_year=month_year,
        criteria_id=criteria_id,
        category_type=category_type,
        max_score=max_score,
        entries=entries,
        scored_by=actor_name
    )

@app.post("/eom/save-all-matrix")
async def save_eom_all_matrix_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    data = await request.json()
    month_year = data.get("month_year")
    scores_list = data.get("scores", [])
    if not month_year or not isinstance(scores_list, list):
        raise HTTPException(status_code=400, detail="month_year and scores list are required")

    actor_id, actor_name = await get_actor_from_request(request, database.db)
    res = await eom_service.bulk_save_all_matrix(
        month_year=month_year,
        scores_list=scores_list,
        scored_by=actor_name,
        evaluator_id=actor_id
    )
    await redis_manager.invalidate_namespace("hrms:eom")
    return res

@app.get("/eom/reveal-order")
async def get_eom_reveal_order_endpoint(month_year: str = Query(...)):
    return await eom_service.get_eom_reveal_order(month_year)

@app.get("/eom/scores")
async def get_eom_scores_endpoint(
    month_year: str = Query(...),
    criteriaId: Optional[str] = Query(None),
    scoredBy: Optional[str] = Query(None)
):
    return await eom_service.get_scores(month_year, criteriaId, scored_by=scoredBy)

@app.get("/eom/leaderboard")
@redis_manager.cached_api(namespace="hrms:eom", ttl=180)
async def get_eom_leaderboard_endpoint(request: Request, month_year: str = Query(...)):
    return await eom_service.calculate_eom_leaderboard(month_year)

@app.get("/eom/month-config")
async def get_eom_month_config_endpoint(month_year: str = Query(...)):
    return await eom_service.get_month_config(month_year)

@app.get("/eom/attendance-stats")
@redis_manager.cached_api(namespace="hrms:eom", ttl=180)
async def get_eom_attendance_stats_endpoint(request: Request, month_year: str = Query(...), maxScore: float = Query(15.0)):
    return await eom_service.get_eom_attendance_stats(month_year, maxScore)

@app.get("/eom/discipline-stats")
@redis_manager.cached_api(namespace="hrms:eom", ttl=180)
async def get_eom_discipline_stats_endpoint(request: Request, month_year: str = Query(...), maxScore: float = Query(10.0)):
    return await eom_service.get_eom_discipline_stats(month_year, maxScore)

@app.get("/eom/work-completion-stats")
@redis_manager.cached_api(namespace="hrms:eom", ttl=180)
async def get_eom_work_completion_stats_endpoint(request: Request, month_year: str = Query(...), maxScore: float = Query(10.0)):
    return await eom_service.get_eom_work_completion_stats(month_year, maxScore)

@app.get("/eom/work-dedication-stats")
@redis_manager.cached_api(namespace="hrms:eom", ttl=180)
async def get_eom_work_dedication_stats_endpoint(request: Request, month_year: str = Query(...), maxScore: float = Query(10.0)):
    return await eom_service.get_eom_work_dedication_stats(month_year, maxScore)

@app.get("/eom/vote-stats")
@redis_manager.cached_api(namespace="hrms:eom", ttl=180)
async def get_eom_vote_stats_endpoint(request: Request, month_year: str = Query(...), maxScore: float = Query(10.0)):
    return await eom_service.get_eom_vote_stats(month_year, maxScore)

@app.post("/eom/reveal-schedule")
async def save_eom_reveal_schedule_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can schedule reveal time.")
    data = await request.json()
    month_year = data.get("month_year")
    reveal_date_time = data.get("revealDateTime")
    if not month_year:
        raise HTTPException(status_code=400, detail="month_year is required")
    return await eom_service.save_month_config(month_year, reveal_date_time=reveal_date_time)

@app.post("/eom/month-config")
async def save_eom_month_config_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can configure participating employees.")
    data = await request.json()
    month_year = data.get("month_year")
    selected_employee_ids = data.get("selectedEmployeeIds", [])
    if not month_year:
        raise HTTPException(status_code=400, detail="month_year is required")
    return await eom_service.save_month_config(month_year, selected_employee_ids)


# --- EMPLOYEE OF THE WEEK (EOW) ENDPOINTS ---

@app.get("/weekly-meetings/master-topics")
async def get_weekly_master_topics_endpoint():
    return await eom_service.get_weekly_master_topics()

@app.post("/weekly-meetings/master-topics")
async def save_weekly_master_topics_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can configure master topics.")
    data = await request.json()
    topics = data.get("topics", [])
    return await eom_service.save_weekly_master_topics(topics)

@app.get("/weekly-meetings")
async def get_weekly_meetings_endpoint():
    return await eom_service.get_weekly_meetings()

@app.post("/weekly-meetings")
async def create_weekly_meeting_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can create weekly meetings.")
    data = await request.json()
    meeting_date = data.get("meetingDate")
    participant_ids = data.get("participantEmployeeIds", [])
    copy_from_id = data.get("copyFromMeetingId")
    if not meeting_date:
        raise HTTPException(status_code=400, detail="meetingDate is required")
    try:
        return await eom_service.create_weekly_meeting(meeting_date, participant_ids, copy_from_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@app.get("/weekly-meetings/{meeting_id}")
async def get_weekly_meeting_detail_endpoint(meeting_id: str):
    detail = await eom_service.get_weekly_meeting_detail(meeting_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return detail

@app.delete("/weekly-meetings/{meeting_id}")
async def delete_weekly_meeting_endpoint(
    meeting_id: str,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can delete weekly meetings.")
    return {"success": await eom_service.delete_weekly_meeting(meeting_id)}

@app.put("/weekly-meetings/{meeting_id}/participants")
async def update_weekly_participants_endpoint(
    meeting_id: str,
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can update meeting participants.")
    data = await request.json()
    participant_ids = data.get("participantEmployeeIds", [])
    return {"success": await eom_service.update_weekly_participants(meeting_id, participant_ids)}

@app.put("/weekly-meetings/{meeting_id}/topics")
async def save_weekly_topics_endpoint(
    meeting_id: str,
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can edit topics.")
    data = await request.json()
    topics = data.get("topics", [])
    return await eom_service.save_weekly_topics(meeting_id, topics)

@app.post("/weekly-meetings/{meeting_id}/entries")
async def save_weekly_entries_endpoint(
    meeting_id: str,
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    data = await request.json()
    entries = data.get("entries", [])
    return await eom_service.save_weekly_entries(meeting_id, entries)

@app.post("/weekly-meetings/declare-result")
async def declare_weekly_team_result_endpoint(
    request: Request,
    token_payload: dict = Depends(auth.require_auth)
):
    if not await _is_admin_or_hr(token_payload):
        raise HTTPException(status_code=403, detail="Only Admin or HR can declare results.")
    data = await request.json()
    week_ids = data.get("weekMeetingIds", [])
    month_year = data.get("monthYear", "")
    try:
        return await eom_service.declare_weekly_team_result(week_ids, month_year)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@app.get("/weekly-meetings/declared-results/{month_year}")
async def get_team_declared_result_endpoint(month_year: str):
    res = await eom_service.get_team_declared_result(month_year)
    if not res:
        raise HTTPException(status_code=404, detail=f"No declared team result found for {month_year}")
    return res


if __name__ == "__main__":


    port = int(os.environ.get("BACKEND_PORT", os.environ.get("PORT", 8000)))
    print(f"Starting HRMS Backend on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, reload=False)

@app.get("/documents-page-data")
async def get_documents_page_data(userId: Optional[str] = None, role: Optional[str] = None, db=Depends(get_db)):
    """Clubbed endpoint for the Documents page. Replaces 4 separate calls."""
    is_admin = role and role.lower() in ["admin", "super admin", "hr"]
    
    results = await asyncio.gather(
        crud.get_document_requests(db, employee_id=None if is_admin else userId),
        crud.get_document_types(db),
        crud.get_payroll(db, skip=0, limit=10000),
        crud.get_document_templates(db, skip=0, limit=10000)
    )
    
    return {
        "requests": results[0],
        "types": results[1],
        "payroll": results[2],
        "templates": results[3]
    }

@app.get("/modules-page-data")
async def get_modules_page_data(db=Depends(get_db)):
    """Clubbed endpoint for the Modules page. Replaces 3 separate calls."""
    results = await asyncio.gather(
        crud.get_projects(db, skip=0, limit=10000),
        crud.get_employees(db, skip=0, limit=10000),
        crud.get_wm_tasks(db, skip=0, limit=10000)
    )
    
    return {
        "projects": results[0],
        "employees": [
            {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
             "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
             "role": e.get("role"), "email": e.get("email"), "photoUrl": e.get("photoUrl")}
            for e in results[1]
        ],
        "tasks": results[2]
    }

@app.get("/settings-page-data")
async def get_settings_page_data(db=Depends(get_db)):
    """Clubbed endpoint for the Settings page. Replaces 2 separate calls."""
    results = await asyncio.gather(
        crud.get_system_settings(db),
        crud.get_employees(db, skip=0, limit=10000)
    )
    
    return {
        "systemSettings": results[0],
        "employees": [
            {"id": e.get("id"), "firstName": e.get("firstName"), "lastName": e.get("lastName"),
             "name": e.get("name"), "department": e.get("department"), "designation": e.get("designation"),
             "role": e.get("role"), "email": e.get("email"), "photoUrl": e.get("photoUrl"),
             "signatureUrl": e.get("signatureUrl")}
            for e in results[1]
        ]
    }
