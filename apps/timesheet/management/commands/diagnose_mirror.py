"""
Django management command to diagnose timesheet mirror mode issues.
Checks TimesheetEvent table, rolling window, and configuration.

Usage:
    python manage.py diagnose_mirror
    
On Railway:
    railway run python manage.py diagnose_mirror
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.timesheet.models import TimesheetEvent, BiometricUserMaster
from apps.timesheet import config as ts_config


class Command(BaseCommand):
    help = 'Diagnose timesheet mirror mode configuration and data'

    def handle(self, *args, **options):
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("🔍 TIMESHEET MIRROR MODE DIAGNOSTIC"))
        self.stdout.write("=" * 80)
        
        # ══════════════════════════════════════════════════════════════════
        # CHECK 1: Configuration
        # ══════════════════════════════════════════════════════════════════
        self.stdout.write("\n📋 STEP 1: Configuration")
        self.stdout.write("-" * 80)
        
        data_source = ts_config.DATA_SOURCE
        lookback_hours = ts_config.RULES.get('live_lookback_hours', 20)
        tz_offset = ts_config.INGEST_TZ_OFFSET_HOURS
        
        self.stdout.write(f"TIMESHEET_DATA_SOURCE:         {data_source}")
        self.stdout.write(f"TIMESHEET_LIVE_LOOKBACK_HOURS: {lookback_hours}")
        self.stdout.write(f"TIMESHEET_INGEST_TZ_OFFSET:    {tz_offset}")
        
        if data_source != 'mirror':
            self.stdout.write(self.style.ERROR(
                "\n❌ CRITICAL: TIMESHEET_DATA_SOURCE is NOT 'mirror'"
            ))
            self.stdout.write(self.style.WARNING(
                "   Railway cannot access office SQL Server (192.168.99.52)"
            ))
            self.stdout.write("\n🔧 FIX: Set TIMESHEET_DATA_SOURCE=mirror in Railway")
            return
        else:
            self.stdout.write(self.style.SUCCESS("\n✅ Data source: mirror"))
        
        # ══════════════════════════════════════════════════════════════════
        # CHECK 2: TimesheetEvent Table
        # ══════════════════════════════════════════════════════════════════
        self.stdout.write("\n📊 STEP 2: TimesheetEvent Table")
        self.stdout.write("-" * 80)
        
        try:
            total_events = TimesheetEvent.objects.count()
            self.stdout.write(f"Total events: {total_events:,}")
            
            if total_events == 0:
                self.stdout.write(self.style.ERROR(
                    "\n❌ CRITICAL: TimesheetEvent table is EMPTY"
                ))
                self.stdout.write(self.style.WARNING(
                    "   Sync agent has NOT pushed any data"
                ))
                self.stdout.write("\n🔧 FIX: Start sync agent on office server")
                return
            
            # Date range
            earliest = TimesheetEvent.objects.order_by('event_time').first()
            latest = TimesheetEvent.objects.order_by('-event_time').first()
            
            self.stdout.write(f"Earliest: {earliest.event_time}")
            self.stdout.write(f"Latest:   {latest.event_time}")
            self.stdout.write(f"Span:     {(latest.event_time - earliest.event_time).days} days")
            
            # Event types
            in_count = TimesheetEvent.objects.filter(event_type='IN').count()
            out_count = TimesheetEvent.objects.filter(event_type='OUT').count()
            self.stdout.write(f"IN:  {in_count:,}")
            self.stdout.write(f"OUT: {out_count:,}")
            
            self.stdout.write(self.style.SUCCESS("\n✅ Table has data"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n❌ Error: {e}"))
            return
        
        # ══════════════════════════════════════════════════════════════════
        # CHECK 3: Rolling Window
        # ══════════════════════════════════════════════════════════════════
        self.stdout.write(f"\n🕐 STEP 3: Rolling Window ({lookback_hours}h)")
        self.stdout.write("-" * 80)
        
        try:
            now = timezone.now()
            cutoff = now - timedelta(hours=lookback_hours)
            
            self.stdout.write(f"Now:    {now}")
            self.stdout.write(f"Cutoff: {cutoff}")
            
            windowed = TimesheetEvent.objects.filter(event_time__gte=cutoff)
            windowed_count = windowed.count()
            
            self.stdout.write(f"\nEvents in window: {windowed_count:,}")
            
            if windowed_count == 0:
                self.stdout.write(self.style.ERROR(
                    "\n❌ PROBLEM: 0 events in rolling window!"
                ))
                
                # Show latest events
                latest_5 = TimesheetEvent.objects.order_by('-event_time')[:5]
                self.stdout.write("\nLatest 5 events:")
                for ev in latest_5:
                    self.stdout.write(f"  {ev.employee_code} - {ev.event_type} - {ev.event_time}")
                
                hours_diff = (now - latest.event_time).total_seconds() / 3600
                self.stdout.write(f"\nHours since latest event: {hours_diff:.1f}h")
                
                if hours_diff > lookback_hours:
                    self.stdout.write(self.style.WARNING(
                        "\n🔧 FIX: Events are too old OR sync agent not running"
                    ))
                    self.stdout.write("   1. Run sync agent to push fresh data")
                    self.stdout.write(f"   2. OR increase: TIMESHEET_LIVE_LOOKBACK_HOURS=48")
                else:
                    self.stdout.write(self.style.WARNING(
                        f"\n🔧 FIX: Timezone offset wrong (current: {tz_offset})"
                    ))
                    self.stdout.write("   For UAE (UTC+4): TIMESHEET_INGEST_TZ_OFFSET=4")
                    self.stdout.write("   Then re-run sync agent with --full flag")
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"\n✅ Found {windowed_count} events in window"
                ))
                
                # Sample
                sample = windowed.order_by('-event_time')[:5]
                self.stdout.write("\nSample events:")
                for ev in sample:
                    self.stdout.write(
                        f"  {ev.employee_code} - {ev.employee_name} - "
                        f"{ev.event_type} - {ev.event_time}"
                    )
                
                unique_employees = windowed.values('employee_code').distinct().count()
                in_windowed = windowed.filter(event_type='IN').count()
                out_windowed = windowed.filter(event_type='OUT').count()
                
                self.stdout.write(f"\nUnique employees: {unique_employees}")
                self.stdout.write(f"IN:  {in_windowed}")
                self.stdout.write(f"OUT: {out_windowed}")
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n❌ Error: {e}"))
            import traceback
            traceback.print_exc()
            return
        
        # ══════════════════════════════════════════════════════════════════
        # SUMMARY
        # ══════════════════════════════════════════════════════════════════
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("🎯 DIAGNOSIS COMPLETE"))
        self.stdout.write("=" * 80)
        
        if windowed_count > 0:
            self.stdout.write(self.style.SUCCESS(
                f"\n✅ ✅ ✅ SYSTEM HEALTHY ✅ ✅ ✅"
            ))
            self.stdout.write(f"\nTotal: {total_events:,} | Window: {windowed_count:,} | Employees: {unique_employees}")
            self.stdout.write("\nBackend should return data correctly.")
            self.stdout.write("If frontend still shows error, check:")
            self.stdout.write("  1. Browser console for API errors")
            self.stdout.write("  2. Network tab: /api/v1/timesheet/live/")
            self.stdout.write("  3. Railway logs for [timesheet.live] messages")
        else:
            self.stdout.write(self.style.ERROR(
                f"\n❌ ❌ ❌ ISSUE FOUND ❌ ❌ ❌"
            ))
            self.stdout.write(f"\nDatabase: {total_events:,} events | Window: 0 events")
            self.stdout.write("\n🔧 Required fixes:")
            self.stdout.write("  1. TIMESHEET_INGEST_TZ_OFFSET=4 (UAE)")
            self.stdout.write("  2. OR TIMESHEET_LIVE_LOOKBACK_HOURS=48")
            self.stdout.write("  3. Redeploy Railway backend")
            self.stdout.write("  4. Re-run sync agent: --full flag")
        
        self.stdout.write("\n" + "=" * 80)
