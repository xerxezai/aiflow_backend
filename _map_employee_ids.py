#!/usr/bin/env python
"""Helper script to view and update employee_id mappings"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from apps.rbac.models import UserProfile
from apps.timesheet import services as ts_services

print("=" * 80)
print("EMPLOYEE ID MAPPING TOOL")
print("=" * 80)

# Fetch all employees from SQL Server
print("\n📊 Fetching employees from biometric system...")
try:
    monthly = ts_services.monthly_report(2026, 6)
    rows = monthly.get('rows', [])
    print(f"✅ Found {len(rows)} employees in biometric system\n")
    
    # Display available employees
    print("Available Biometric Employees (showing first 30):")
    print("-" * 80)
    print(f"{'Code':<15} {'Name':<40} {'Hours':<10}")
    print("-" * 80)
    
    for i, row in enumerate(rows[:30], 1):
        code = str(row.get('employee_code') or row.get('code', 'N/A'))
        name = str(row.get('employee_name') or row.get('name', 'N/A'))
        hours = row.get('total_hours', 0)
        print(f"{code:<15} {name:<40} {hours:<10.2f}")
    
    if len(rows) > 30:
        print(f"\n... and {len(rows) - 30} more employees")
    
    print("\n" + "-" * 80)
    print("\n📋 Current RAD AI User Profiles (with employee_id):")
    print("-" * 80)
    print(f"{'Email':<35} {'Name':<30} {'Employee ID':<15}")
    print("-" * 80)
    
    profiles = UserProfile.objects.select_related('user').filter(is_deleted=False).all()
    unmatched = []
    
    for profile in profiles:
        email = profile.user.email
        name = f"{profile.user.first_name} {profile.user.last_name}"
        emp_id = profile.employee_id or "NOT SET"
        
        print(f"{email:<35} {name:<30} {emp_id:<15}")
        
        # Check if employee_id exists in biometric system
        if profile.employee_id:
            code_lower = str(profile.employee_id).lower()
            match = any(
                str(r.get('employee_code', '')).lower() == code_lower or 
                str(r.get('code', '')).lower() == code_lower
                for r in rows
            )
            if not match:
                unmatched.append((profile, email, name, profile.employee_id))
    
    if unmatched:
        print("\n⚠️  UNMATCHED USERS (employee_id not found in biometric system):")
        print("-" * 80)
        for profile, email, name, emp_id in unmatched:
            print(f"   {email} → '{emp_id}' not found")
        
        print("\n💡 TO FIX:")
        print("   1. Find the correct biometric code from the list above")
        print("   2. Update using Django admin or shell:")
        print("      python manage.py shell")
        print("      >>> from apps.rbac.models import UserProfile")
        print("      >>> p = UserProfile.objects.get(user__email='user@example.com')")
        print("      >>> p.employee_id = '22393'  # Use the correct numeric code")
        print("      >>> p.save()")
    else:
        print("\n✅ All users have valid employee_id mappings!")
    
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
