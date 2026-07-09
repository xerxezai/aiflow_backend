#!/usr/bin/env python
"""
Diagnostic script: Check for LeaveRequest records with null leave_type.
This can cause 500 errors in the leave-calendar endpoint.

Usage:
    cd backend
    python _check_leave_data.py
"""

import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.payroll.models import LeaveRequest, LeaveType

def check_leave_data():
    print("=" * 80)
    print("Leave Request Data Integrity Check")
    print("=" * 80)
    
    # Count total leave requests
    total_requests = LeaveRequest.objects.count()
    print(f"\nTotal Leave Requests: {total_requests}")
    
    # Check for requests with null leave_type
    null_leave_type = LeaveRequest.objects.filter(leave_type__isnull=True)
    null_count = null_leave_type.count()
    
    if null_count > 0:
        print(f"\n⚠️  WARNING: Found {null_count} LeaveRequest(s) with null leave_type!")
        print("\nRecords with null leave_type:")
        for req in null_leave_type:
            print(f"  - ID: {req.id}")
            print(f"    Employee: {req.employee_name} ({req.employee_code})")
            print(f"    Dates: {req.start_date} → {req.end_date}")
            print(f"    Status: {req.status}")
            print(f"    Created: {req.created_at}")
            print()
        
        print("\n💡 Recommended action:")
        print("   1. Assign a valid leave_type to these records, OR")
        print("   2. Delete these records if they're invalid")
        print("\nExample fix (in Django shell):")
        print("   from apps.payroll.models import LeaveRequest, LeaveType")
        print("   default_type = LeaveType.objects.first()")
        print("   LeaveRequest.objects.filter(leave_type__isnull=True).update(leave_type=default_type)")
    else:
        print("\n✅ All LeaveRequest records have valid leave_type")
    
    # Check available leave types
    leave_types = LeaveType.objects.all()
    print(f"\n\nAvailable Leave Types: {leave_types.count()}")
    if leave_types.count() > 0:
        for lt in leave_types:
            print(f"  - {lt.code}: {lt.name}")
            request_count = lt.requests.count()
            print(f"    Requests using this type: {request_count}")
    else:
        print("⚠️  WARNING: No LeaveType records found!")
        print("   You need to create at least one LeaveType before employees can request leave.")
    
    print("\n" + "=" * 80)

if __name__ == '__main__':
    check_leave_data()
