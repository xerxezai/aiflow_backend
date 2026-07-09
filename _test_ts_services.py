"""Test all 3 timesheet query builders after the email fix."""
import os, sys
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from apps.timesheet import services

print("\n=== live_status ===")
try:
    r = services.live_status()
    print(f"variant : {r['variant']}")
    print(f"summary : {r['summary']}")
    print(f"rows    : {len(r['rows'])} users currently tracked today")
    for row in r['rows'][:5]:
        print(f"  {row.get('employee_code'):<8} {(row.get('name') or '')[:25]:<25} {row.get('department') or ''}  punch={row.get('punch_time')} type={row.get('punch_type')}")
    print("OK live_status passed")
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"FAIL: {e}")

print("\n=== daily_report ===")
try:
    r = services.daily_report()
    print(f"date    : {r['date']}")
    print(f"rows    : {len(r['rows'])}")
    for row in r['rows'][:3]:
        print(f"  {row.get('employee_code'):<8} {(row.get('name') or '')[:20]:<20} first_in={row.get('first_in')} last_out={row.get('last_out')} hrs={row.get('hours_worked')}")
    print("OK daily_report passed")
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"FAIL: {e}")

print("\n=== monthly_report ===")
try:
    r = services.monthly_report()
    print(f"year/month: {r['year']}/{r['month']}")
    print(f"rows      : {len(r['rows'])}")
    for row in r['rows'][:3]:
        print(f"  {row.get('employee_code'):<8} {(row.get('name') or '')[:20]:<20} days_present={row.get('days_present')} total_hours={row.get('total_hours')}")
    print("OK monthly_report passed")
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"FAIL: {e}")
