#!/usr/bin/env python
"""Quick verification that tanzeem.agra fix worked"""
import os, sys, django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from apps.rbac.models import UserProfile
from apps.timesheet import services as ts_services

p = UserProfile.objects.get(user__email='tanzeem.agra@rejlers.ae')
print(f"✅ User: {p.user.email}")
print(f"   employee_id: {p.employee_id}")

monthly = ts_services.monthly_report(2026, 6)
match = [r for r in monthly.get('rows', []) if str(r.get('employee_code')) == str(p.employee_id)]

if match:
    print(f"\n✅ ATTENDANCE DATA FOUND!")
    print(f"   Total Hours: {match[0].get('total_hours', 0):.2f}")
    print(f"   Days Present: {match[0].get('days_present', 0)}")
    print(f"   Employee Name: {match[0].get('employee_name', 'N/A')}")
    print(f"\n🎉 FIX SUCCESSFUL - User can now see attendance!")
else:
    print(f"\n❌ No data found for employee_id: {p.employee_id}")
