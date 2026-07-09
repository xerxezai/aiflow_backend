#!/usr/bin/env python
"""
Diagnostic: Test the soft-coded rolling window for Live timesheet view.
Run this to verify TIMESHEET_LIVE_LOOKBACK_HOURS is working correctly.

Usage:
    cd backend
    python _verify_live_window.py
"""
import os
import sys
import django
from datetime import datetime, timedelta

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from apps.timesheet import config as ts_config
from apps.timesheet.sqlserver import connect

def test_rolling_window():
    print("=" * 80)
    print("LIVE TIMESHEET ROLLING WINDOW DIAGNOSTIC")
    print("=" * 80)
    
    # Read config
    lookback_hours = int(ts_config.RULES.get('live_lookback_hours', 20))
    table = ts_config.SCHEMA['table']
    cols = ts_config.SCHEMA['columns']
    
    print(f"\n✓ Configuration loaded:")
    print(f"  • Table: {table}")
    print(f"  • Lookback Hours: {lookback_hours}")
    print(f"  • Punch Time Column: {cols['punch_time']}")
    print(f"  • Punch Type Column: {cols['punch_type']}")
    print(f"  • IN Value: {cols['in_value']}")
    print(f"  • OUT Value: {cols['out_value']}")
    
    # Calculate window
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    print(f"\n✓ Time Window:")
    print(f"  • Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  • Cutoff (UTC): {cutoff.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  • Window: Last {lookback_hours} hours")
    
    # Test query
    sql = f"""
    SELECT COUNT(*) AS total_events,
           MIN({cols['punch_time']}) AS earliest_event,
           MAX({cols['punch_time']}) AS latest_event,
           COUNT(DISTINCT {cols['employee_code']}) AS unique_employees
    FROM {table}
    WHERE {cols['punch_time']} >= DATEADD(HOUR, %s, GETDATE())
    """
    
    print(f"\n✓ Testing SQL Server query...")
    print(f"  Query: WHERE {cols['punch_time']} >= DATEADD(HOUR, -{lookback_hours}, GETDATE())")
    
    try:
        with connect() as cur:
            cur.execute(sql, (-lookback_hours,))
            result = cur.fetchone()
            
            print(f"\n✅ SUCCESS - Query executed successfully!")
            print(f"  • Total Events: {result[0]:,}")
            print(f"  • Unique Employees: {result[3]:,}")
            if result[1]:
                print(f"  • Earliest Event: {result[1]}")
            if result[2]:
                print(f"  • Latest Event: {result[2]}")
            
            if result[0] == 0:
                print(f"\n⚠️  WARNING: No events found in the last {lookback_hours} hours")
                print(f"  Possible causes:")
                print(f"  • No employees have punched in/out recently")
                print(f"  • Timezone mismatch (SQL Server vs application server)")
                print(f"  • Lookback window too short")
                print(f"\n  Suggestions:")
                print(f"  • Increase TIMESHEET_LIVE_LOOKBACK_HOURS (current: {lookback_hours})")
                print(f"  • Try setting to 30-48 hours to cover timezone differences")
                
            # Test latest 10 events regardless of window
            print(f"\n✓ Checking latest 10 events (any time)...")
            sql_recent = f"""
            SELECT TOP 10
                   {cols['employee_code']} AS emp_code,
                   {cols['employee_name']} AS emp_name,
                   {cols['punch_time']} AS punch_time,
                   {cols['punch_type']} AS punch_type,
                   DATEDIFF(HOUR, {cols['punch_time']}, GETDATE()) AS hours_ago
            FROM {table}
            ORDER BY {cols['punch_time']} DESC
            """
            cur.execute(sql_recent)
            recent_rows = cur.fetchall()
            
            if recent_rows:
                print(f"\n  Latest 10 events:")
                print(f"  {'Code':<10} {'Name':<30} {'Punch Time':<20} {'Type':<8} {'Hours Ago'}")
                print(f"  {'-'*10} {'-'*30} {'-'*20} {'-'*8} {'-'*10}")
                for row in recent_rows:
                    code = str(row[0])[:10] if row[0] else 'N/A'
                    name = str(row[1])[:30] if row[1] else 'N/A'
                    time = str(row[2])[:19] if row[2] else 'N/A'
                    ptype = str(row[3])[:8] if row[3] else 'N/A'
                    hours = int(row[4]) if row[4] else 0
                    print(f"  {code:<10} {name:<30} {time:<20} {ptype:<8} {hours}")
                    
                oldest_hours = int(recent_rows[-1][4]) if recent_rows[-1][4] else 0
                if oldest_hours > lookback_hours:
                    print(f"\n  ⚠️  The 10th latest event is {oldest_hours}h old, outside the {lookback_hours}h window")
                    print(f"     Consider increasing TIMESHEET_LIVE_LOOKBACK_HOURS to {oldest_hours + 4}")
            else:
                print(f"\n  ❌ No events found in the table at all!")
                
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print(f"\n  Check your database connection settings:")
        print(f"  • TIMESHEET_HOST")
        print(f"  • TIMESHEET_DATABASE")
        print(f"  • TIMESHEET_USER/PASSWORD")
        sys.exit(1)
    
    print("\n" + "=" * 80)
    print("Diagnostic complete.")
    print("=" * 80)

if __name__ == '__main__':
    test_rolling_window()
