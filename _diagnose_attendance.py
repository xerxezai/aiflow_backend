#!/usr/bin/env python
"""Diagnose attendance data fetching for self-service endpoint"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from apps.rbac.models import UserProfile
from apps.timesheet import services as ts_services
from apps.timesheet import config as ts_config

print("=" * 70)
print("ATTENDANCE DATA DIAGNOSTIC")
print("=" * 70)

# Check timesheet configuration
print(f"\n1. TIMESHEET CONFIGURATION:")
print(f"   Configured: {ts_config.is_configured()}")
print(f"   Data Source: {ts_config.DATA_SOURCE}")
print(f"   Host: {ts_config.SQLSERVER.get('host')}")
print(f"   Port: {ts_config.SQLSERVER.get('port')}")
print(f"   Database: {ts_config.SQLSERVER.get('database')}")

# Get first user with employee_id
profile = UserProfile.objects.filter(employee_id__isnull=False, is_deleted=False).first()

if not profile:
    print("\n❌ ERROR: No user with employee_id found in database!")
    print("   Please assign employee_id to at least one user.")
    sys.exit(1)

print(f"\n2. TEST USER PROFILE:")
print(f"   Email: {profile.user.email}")
print(f"   Name: {profile.user.first_name} {profile.user.last_name}")
print(f"   Employee ID: {profile.employee_id}")

# Try to fetch monthly data
print(f"\n3. FETCHING MONTHLY DATA (June 2026):")
try:
    monthly = ts_services.monthly_report(2026, 6)
    rows = monthly.get('rows', [])
    print(f"   ✅ Query successful")
    print(f"   Total rows returned: {len(rows)}")
    print(f"   Working days in month: {monthly.get('working_days_in_month', 'N/A')}")
    
    if rows:
        print(f"\n4. SAMPLE DATA (first 3 rows):")
        for i, row in enumerate(rows[:3], 1):
            emp_code = row.get('employee_code') or row.get('code', 'N/A')
            emp_name = row.get('employee_name') or row.get('name', 'N/A')
            hours = row.get('total_hours', 'N/A')
            print(f"   Row {i}: Code={emp_code}, Name={emp_name}, Hours={hours}")
        
        print(f"\n5. LOOKING FOR TEST USER'S DATA:")
        code_to_find = str(profile.employee_id).lower()
        print(f"   Searching for employee_code: {code_to_find}")
        
        match = None
        for row in rows:
            row_code = str(row.get('employee_code') or row.get('code', '')).lower()
            if row_code == code_to_find:
                match = row
                break
        
        if match:
            print(f"   ✅ FOUND user's record:")
            print(f"      Employee Code: {match.get('employee_code') or match.get('code')}")
            print(f"      Name: {match.get('employee_name') or match.get('name')}")
            print(f"      Total Hours: {match.get('total_hours', 0)}")
            print(f"      Days Present: {match.get('days_present', 0)}")
            print(f"      Overtime: {match.get('total_overtime', 0)}")
        else:
            print(f"   ❌ NOT FOUND - No matching record for code '{code_to_find}'")
            print(f"   Available codes in SQL Server:")
            for row in rows[:10]:
                code = row.get('employee_code') or row.get('code', 'N/A')
                name = row.get('employee_name') or row.get('name', 'N/A')
                print(f"      - {code} ({name})")
    else:
        print(f"   ⚠️  No attendance data found in SQL Server for June 2026")
        
except Exception as e:
    print(f"   ❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
