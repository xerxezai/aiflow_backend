#!/usr/bin/env python
"""
END-TO-END ATTENDANCE FEATURE TEST
Simulates API calls as a logged-in user would make
"""
import os
import sys
import django
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from django.test import RequestFactory
from apps.rbac.models import UserProfile
from apps.timesheet import views

print("=" * 90)
print("END-TO-END ATTENDANCE FEATURE TEST")
print("=" * 90)

# Test with verified users
test_users = [
    'gaurav.kumar@rejlers.ae',
    'jarmo.suominen@rejlers.ae',
    'aarekh.mehrotra@rejlers.ae',
    'nitesh.nijhawan@rejlers.ae',
    'rajasekhar.pasumarthi@rejlers.ae'
]

factory = RequestFactory()
success_count = 0
fail_count = 0

for email in test_users:
    print(f"\n{'=' * 90}")
    print(f"Testing User: {email}")
    print('=' * 90)
    
    try:
        # Get user profile
        profile = UserProfile.objects.get(user__email=email)
        user = profile.user
        
        print(f"✅ User Found")
        print(f"   Name: {user.first_name} {user.last_name}")
        print(f"   employee_id: {profile.employee_id}")
        
        # Simulate GET /api/v1/timesheet/my-attendance/monthly/
        request = factory.get('/api/v1/timesheet/my-attendance/monthly/', {'year': 2026, 'month': 6})
        request.user = user
        
        # Call the view
        response = views.my_monthly_attendance(request)
        
        if response.status_code == 200:
            data = json.loads(response.content)
            
            print(f"\n📊 MONTHLY ATTENDANCE (June 2026):")
            print(f"   API Response Code: {response.status_code}")
            print(f"   Configured: {data.get('configured')}")
            print(f"   Employee Code: {data.get('employee_code')}")
            
            attendance = data.get('data', {})
            if attendance:
                print(f"\n   ✅ DATA RETRIEVED:")
                print(f"      Total Hours: {attendance.get('total_hours', 0):.2f}")
                print(f"      Days Present: {attendance.get('days_present', 0)}")
                print(f"      Total Overtime: {attendance.get('total_overtime', 0):.2f}")
                print(f"      Working Days: {attendance.get('working_days_in_month', 0)}")
                
                # Show daily breakdown sample
                daily_hours = attendance.get('daily_hours', {})
                if daily_hours:
                    print(f"\n   📅 DAILY BREAKDOWN (first 5 days):")
                    for day, hours in list(daily_hours.items())[:5]:
                        print(f"      {day}: {hours:.2f} hours")
                
                success_count += 1
                print(f"\n   ✅ TEST PASSED - User can see their attendance")
            else:
                print(f"   ⚠️  No attendance data found for this user")
                fail_count += 1
        else:
            print(f"   ❌ API Error: {response.status_code}")
            fail_count += 1
            
    except UserProfile.DoesNotExist:
        print(f"   ❌ User profile not found")
        fail_count += 1
    except Exception as e:
        print(f"   ❌ Test Error: {e}")
        import traceback
        traceback.print_exc()
        fail_count += 1

# Summary
print(f"\n{'=' * 90}")
print("TEST SUMMARY")
print('=' * 90)
print(f"✅ Passed: {success_count}/{len(test_users)}")
print(f"❌ Failed: {fail_count}/{len(test_users)}")

if success_count == len(test_users):
    print("\n🎉 ALL TESTS PASSED - FEATURE FULLY FUNCTIONAL")
    print("\n✅ Users can now:")
    print("   1. Login at http://localhost:5173")
    print("   2. Go to HR → Employee Self-Service")
    print("   3. Click 'Attendance' tab")
    print("   4. View their attendance records")
elif success_count > 0:
    print(f"\n⚠️  PARTIAL SUCCESS - {success_count} users working")
    print("   Review failed users above")
else:
    print("\n❌ ALL TESTS FAILED - REVIEW CONFIGURATION")

print('=' * 90)
