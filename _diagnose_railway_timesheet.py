"""
Railway Production Timesheet Diagnostic
========================================
Directly check Railway database to diagnose "No punch events" issue.
Run this script to verify mirror table data and configuration.

Usage:
    python _diagnose_railway_timesheet.py
"""
import os
import sys
import django
from datetime import datetime, timedelta

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.utils import timezone
from apps.timesheet.models import TimesheetEvent, BiometricUserMaster
from apps.timesheet import config as ts_config

print("=" * 80)
print("🔍 RAILWAY PRODUCTION TIMESHEET DIAGNOSTIC")
print("=" * 80)

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 1: Environment Configuration
# ══════════════════════════════════════════════════════════════════════════════
print("\n📋 STEP 1: Environment Configuration")
print("-" * 80)

data_source = ts_config.DATA_SOURCE
lookback_hours = ts_config.RULES.get('live_lookback_hours', 20)
tz_offset = ts_config.INGEST_TZ_OFFSET_HOURS

print(f"TIMESHEET_DATA_SOURCE:        {data_source}")
print(f"TIMESHEET_LIVE_LOOKBACK_HOURS: {lookback_hours}")
print(f"TIMESHEET_INGEST_TZ_OFFSET:    {tz_offset}")

if data_source != 'mirror':
    print("\n❌ CRITICAL ERROR: TIMESHEET_DATA_SOURCE is NOT 'mirror'")
    print("   Railway CANNOT access office SQL Server (192.168.99.52)")
    print("\n🔧 FIX: Set this in Railway environment variables:")
    print("   TIMESHEET_DATA_SOURCE=mirror")
    print("\n   Then redeploy the backend service.")
    sys.exit(1)
else:
    print("\n✅ Data source correctly set to 'mirror'")

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 2: TimesheetEvent Table Stats
# ══════════════════════════════════════════════════════════════════════════════
print("\n📊 STEP 2: TimesheetEvent Table Statistics")
print("-" * 80)

try:
    total_events = TimesheetEvent.objects.count()
    print(f"Total events in database: {total_events:,}")
    
    if total_events == 0:
        print("\n❌ CRITICAL ERROR: TimesheetEvent table is EMPTY")
        print("   The office sync agent has NOT pushed any data to Railway")
        print("\n🔧 FIX: On office server, start the sync agent:")
        print("   cd C:\\path\\to\\aiflow\\backend")
        print("   python scripts\\timesheet_mirror_sync.py --daemon")
        print("\n   Verify TIMESHEET_MIRROR_API_KEY matches on both sides")
        sys.exit(1)
    
    # Get date range of events
    earliest = TimesheetEvent.objects.order_by('event_time').values_list('event_time', flat=True).first()
    latest = TimesheetEvent.objects.order_by('-event_time').values_list('event_time', flat=True).first()
    
    print(f"Earliest event: {earliest}")
    print(f"Latest event:   {latest}")
    print(f"Data span:      {(latest - earliest).days} days")
    
    # Check events by type
    in_count = TimesheetEvent.objects.filter(event_type='IN').count()
    out_count = TimesheetEvent.objects.filter(event_type='OUT').count()
    print(f"\nEvent breakdown:")
    print(f"  IN punches:  {in_count:,}")
    print(f"  OUT punches: {out_count:,}")
    
    print("\n✅ Mirror table has data")
    
except Exception as e:
    print(f"\n❌ ERROR checking TimesheetEvent table: {e}")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 3: Rolling Time Window Query (Same as live_status)
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n🕐 STEP 3: Rolling Time Window Query (lookback: {lookback_hours}h)")
print("-" * 80)

try:
    now = timezone.now()
    cutoff = now - timedelta(hours=lookback_hours)
    
    print(f"Current server time (UTC): {now}")
    print(f"Cutoff time (UTC):         {cutoff}")
    print(f"Window:                    Last {lookback_hours} hours")
    
    # Query events in rolling window (SAME as mirror_services.live_status)
    windowed_events = TimesheetEvent.objects.filter(event_time__gte=cutoff)
    windowed_count = windowed_events.count()
    
    print(f"\n📈 Events in rolling window: {windowed_count:,}")
    
    if windowed_count == 0:
        print("\n❌ PROBLEM FOUND: Database has events BUT none in rolling window!")
        print("   This is a TIMEZONE MISMATCH issue.")
        print("\n📅 Let's check the timezone of your latest events:")
        
        # Show latest 5 events with timestamps
        latest_5 = TimesheetEvent.objects.order_by('-event_time')[:5]
        print("\n   Latest 5 events in database:")
        for i, ev in enumerate(latest_5, 1):
            print(f"   {i}. {ev.employee_code} - {ev.event_type} - {ev.event_time}")
        
        print(f"\n   Current server time (UTC): {now}")
        print(f"   Cutoff time (UTC):         {cutoff}")
        print(f"   Latest event time:         {latest}")
        print(f"   Hours difference:          {(now - latest).total_seconds() / 3600:.1f}h")
        
        if (now - latest).total_seconds() / 3600 > lookback_hours:
            print("\n🔧 FIX: Events are too old (outside rolling window)")
            print("   EITHER:")
            print("   1. Increase lookback window: TIMESHEET_LIVE_LOOKBACK_HOURS=48")
            print("   2. Run sync agent to push fresh data")
        else:
            print("\n🔧 FIX: Timezone offset is wrong")
            print("   For UAE (UTC+4), set: TIMESHEET_INGEST_TZ_OFFSET=4")
            print("   Current offset: {tz_offset}")
            print("\n   After fixing, re-run sync agent with --full flag to backfill")
    else:
        print(f"\n✅ Found {windowed_count} events in rolling window")
        
        # Show sample events
        sample = windowed_events.order_by('-event_time')[:5]
        print("\n   Sample events:")
        for i, ev in enumerate(sample, 1):
            print(f"   {i}. {ev.employee_code} - {ev.employee_name} - {ev.event_type} - {ev.event_time}")
        
        # Count unique employees
        unique_employees = windowed_events.values('employee_code').distinct().count()
        print(f"\n   Unique employees: {unique_employees}")
        
        # Count by event type
        in_windowed = windowed_events.filter(event_type='IN').count()
        out_windowed = windowed_events.filter(event_type='OUT').count()
        print(f"   IN punches:  {in_windowed}")
        print(f"   OUT punches: {out_windowed}")
        
except Exception as e:
    print(f"\n❌ ERROR checking rolling window: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 4: BiometricUserMaster Table
# ══════════════════════════════════════════════════════════════════════════════
print("\n👥 STEP 4: BiometricUserMaster Table")
print("-" * 80)

try:
    user_master_count = BiometricUserMaster.objects.count()
    print(f"User master records: {user_master_count:,}")
    
    if user_master_count == 0:
        print("⚠️  WARNING: BiometricUserMaster is empty")
        print("   This table enriches attendance rows with email, card numbers, etc.")
        print("   Not critical but recommended for full functionality")
    else:
        print("✅ User master table populated")
        
        # Sample users
        sample = BiometricUserMaster.objects.all()[:3]
        print("\n   Sample users:")
        for u in sample:
            print(f"   - {u.employee_code}: {u.full_name} ({u.office_email or 'no email'})")
            
except Exception as e:
    print(f"\n⚠️  WARNING: Error checking BiometricUserMaster: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# FINAL DIAGNOSIS
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("🎯 FINAL DIAGNOSIS")
print("=" * 80)

if windowed_count > 0:
    print("\n✅ ✅ ✅ SYSTEM IS HEALTHY ✅ ✅ ✅")
    print(f"\n   • Data source: {data_source}")
    print(f"   • Total events: {total_events:,}")
    print(f"   • Events in window: {windowed_count:,}")
    print(f"   • Unique employees: {unique_employees}")
    print("\n   The backend should be returning data correctly.")
    print("   If the frontend still shows 'No punch events', check:")
    print("   1. Browser console for API errors")
    print("   2. Railway logs for [timesheet.live] diagnostic messages")
    print("   3. Network tab to verify /api/v1/timesheet/live/ is being called")
else:
    print("\n❌ ❌ ❌ ISSUE IDENTIFIED ❌ ❌ ❌")
    print(f"\n   Database has {total_events:,} events BUT 0 in rolling window")
    print("   This is a TIMEZONE or LOOKBACK configuration issue")
    print("\n   Next steps:")
    print("   1. Set TIMESHEET_INGEST_TZ_OFFSET=4 in Railway (for UAE UTC+4)")
    print("   2. OR increase TIMESHEET_LIVE_LOOKBACK_HOURS=48")
    print("   3. Redeploy Railway backend")
    print("   4. Re-run sync agent: python scripts\\timesheet_mirror_sync.py --full")
    print("   5. Wait 5 minutes and test again")

print("\n" + "=" * 80)
print("Script completed. Share this output for further diagnosis.")
print("=" * 80)
