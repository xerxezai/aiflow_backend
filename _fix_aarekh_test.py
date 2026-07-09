#!/usr/bin/env python
"""Update Aarekh's employee_id for testing"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from apps.rbac.models import UserProfile
from apps.timesheet import services as ts_services

print("=" * 80)
print("UPDATE TEST USER FOR ATTENDANCE VERIFICATION")
print("=" * 80)

# Update Aarekh Mehrotra
try:
    profile = UserProfile.objects.get(user__email='aarekh.mehrotra@rejlers.ae')
    old_id = profile.employee_id
    profile.employee_id = '22393'  # Biometric code for Aarekh Mehrotra
    profile.save()
    
    print(f"\n✅ Updated user: aarekh.mehrotra@rejlers.ae")
    print(f"   Old employee_id: {old_id}")
    print(f"   New employee_id: 22393")
    
    # Test if we can fetch their data
    print(f"\n📊 Testing data fetch...")
    monthly = ts_services.monthly_report(2026, 6)
    rows = monthly.get('rows', [])
    
    # Find Aarekh's data
    aarekh_data = None
    for row in rows:
        code = str(row.get('employee_code') or row.get('code', ''))
        if code == '22393':
            aarekh_data = row
            break
    
    if aarekh_data:
        print(f"✅ FOUND attendance data:")
        print(f"   Employee Code: {aarekh_data.get('employee_code')}")
        print(f"   Name: {aarekh_data.get('employee_name')}")
        print(f"   Total Hours: {aarekh_data.get('total_hours', 0)}")
        print(f"   Days Present: {aarekh_data.get('days_present', 0)}")
        print(f"   Total Overtime: {aarekh_data.get('total_overtime', 0)}")
        print(f"\n✅ Feature is working! Aarekh can now log in and see their attendance.")
    else:
        print(f"⚠️  Employee code 22393 not found in biometric data")
    
except UserProfile.DoesNotExist:
    print(f"❌ User aarekh.mehrotra@rejlers.ae not found in database")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("NEXT STEPS:")
print("1. Login as aarekh.mehrotra@rejlers.ae")
print("2. Go to http://localhost:5173/hr/leave")
print("3. Click on 'Attendance' tab")
print("4. You should see attendance records!")
print("=" * 80)
