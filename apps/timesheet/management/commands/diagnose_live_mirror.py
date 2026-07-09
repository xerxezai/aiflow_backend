"""
Diagnostic: Check TimesheetEvent mirror table status for Live view debugging.

Usage (Railway shell):
    python manage.py diagnose_live_mirror

Usage (local):
    docker exec aiflow_backend_local python manage.py diagnose_live_mirror
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.timesheet.models import TimesheetEvent
from apps.timesheet import config as ts_config
import datetime as dt


class Command(BaseCommand):
    help = 'Diagnose mirror table status for Live timesheet view'

    def handle(self, *args, **options):
        print("=" * 80)
        print("LIVE TIMESHEET MIRROR DIAGNOSTIC")
        print("=" * 80)
        
        # 1. Check configuration
        print("\n✓ Configuration:")
        print(f"  • DATA_SOURCE: {ts_config.DATA_SOURCE}")
        lookback_hours = int(ts_config.RULES.get('live_lookback_hours', 20))
        print(f"  • LIVE_LOOKBACK_HOURS: {lookback_hours}")
        
        # 2. Check total events in table
        total_events = TimesheetEvent.objects.count()
        print(f"\n✓ TimesheetEvent Table:")
        print(f"  • Total Events: {total_events:,}")
        
        if total_events == 0:
            print("\n❌ PROBLEM: TimesheetEvent table is EMPTY!")
            print("   Cause: Sync agent has never run or failed to push data")
            print("\n   SOLUTION:")
            print("   1. Check if sync agent is running:")
            print("      scripts/timesheet_mirror_sync.py --help")
            print("   2. Run manual sync:")
            print("      python scripts/timesheet_mirror_sync.py --hours 48")
            print("   3. Check Railway environment variables:")
            print("      TIMESHEET_MIRROR_API_KEY=<shared-secret>")
            return
        
        # 3. Check latest event
        latest = TimesheetEvent.objects.order_by('-event_time').first()
        print(f"  • Latest Event: {latest.event_time if latest else 'None'}")
        if latest:
            hours_ago = (timezone.now() - latest.event_time).total_seconds() / 3600
            print(f"  • Latest Event Age: {hours_ago:.1f} hours ago")
            
        # 4. Check events in rolling window
        cutoff = timezone.now() - dt.timedelta(hours=lookback_hours)
        events_in_window = TimesheetEvent.objects.filter(event_time__gte=cutoff).count()
        unique_employees = TimesheetEvent.objects.filter(
            event_time__gte=cutoff
        ).values('employee_code').distinct().count()
        
        print(f"\n✓ Rolling Window ({lookback_hours}h):")
        print(f"  • Cutoff Time: {cutoff}")
        print(f"  • Events in Window: {events_in_window:,}")
        print(f"  • Unique Employees: {unique_employees:,}")
        
        if events_in_window == 0:
            print("\n⚠️  WARNING: No events in the rolling window!")
            print(f"   The latest event is {hours_ago:.1f}h old")
            print(f"   Current window only looks back {lookback_hours}h")
            print("\n   SOLUTIONS:")
            print(f"   A. Increase window: TIMESHEET_LIVE_LOOKBACK_HOURS={int(hours_ago) + 4}")
            print("   B. Wait for sync agent to push recent data")
            print("   C. Manually sync recent events:")
            print("      python scripts/timesheet_mirror_sync.py --hours 24")
        else:
            print(f"\n✅ SUCCESS: Found {events_in_window:,} events in window")
            
        # 5. Show sample events in window
        if events_in_window > 0:
            print("\n✓ Sample Events (latest 10 in window):")
            samples = TimesheetEvent.objects.filter(
                event_time__gte=cutoff
            ).order_by('-event_time')[:10]
            
            print(f"  {'Code':<12} {'Name':<30} {'Time':<20} {'Type':<6} {'Dept':<20}")
            print(f"  {'-'*12} {'-'*30} {'-'*20} {'-'*6} {'-'*20}")
            for ev in samples:
                code = (ev.employee_code or '')[:12]
                name = (ev.employee_name or '')[:30]
                time = ev.event_time.strftime('%Y-%m-%d %H:%M')
                etype = ev.event_type
                dept = (ev.department or '')[:20]
                print(f"  {code:<12} {name:<30} {time:<20} {etype:<6} {dept:<20}")
        
        # 6. Check data freshness
        print("\n✓ Data Freshness:")
        today = timezone.now().date()
        today_events = TimesheetEvent.objects.filter(event_time__date=today).count()
        yesterday = today - dt.timedelta(days=1)
        yesterday_events = TimesheetEvent.objects.filter(event_time__date=yesterday).count()
        print(f"  • Today ({today}): {today_events:,} events")
        print(f"  • Yesterday ({yesterday}): {yesterday_events:,} events")
        
        if today_events == 0 and yesterday_events == 0:
            print("\n⚠️  WARNING: No events in last 48 hours!")
            print("   Sync agent may have stopped or failed")
            
        print("\n" + "=" * 80)
        print("Diagnostic complete.")
        print("=" * 80)
