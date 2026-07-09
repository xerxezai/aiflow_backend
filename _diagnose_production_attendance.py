"""
Diagnose Production Attendance Issue
=====================================
Check why attendance records work locally but not in production.

This script tests the production API endpoint directly and diagnoses configuration issues.
"""

import os
import sys
import django
import requests
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.conf import settings

PRODUCTION_URL = "https://www.radai.ae"
LOCAL_URL = "http://localhost:8000"

def check_environment_config():
    """Check critical environment variables"""
    print("=" * 80)
    print("ENVIRONMENT CONFIGURATION CHECK")
    print("=" * 80)
    
    critical_vars = {
        'TIMESHEET_DATA_SOURCE': os.getenv('TIMESHEET_DATA_SOURCE'),
        'TIMESHEET_HOST': os.getenv('TIMESHEET_HOST'),
        'TIMESHEET_DATABASE': os.getenv('TIMESHEET_DATABASE'),
        'TIMESHEET_CACHE_ENABLED': os.getenv('TIMESHEET_CACHE_ENABLED'),
        'DATABASE_URL': os.getenv('DATABASE_URL', 'NOT SET')[:50] + '...',
    }
    
    print("\n📋 Current Local Environment:")
    for key, value in critical_vars.items():
        print(f"  {key}: {value}")
    
    # Check data source configuration
    data_source = os.getenv('TIMESHEET_DATA_SOURCE', 'sqlserver')
    print(f"\n⚙️  Current Data Source: {data_source}")
    
    if data_source == 'sqlserver':
        print("  ⚠️  WARNING: sqlserver mode requires direct LAN access to 192.168.99.52")
        print("  ⚠️  This WILL NOT WORK on Railway (no route to office network)")
        print("  ✅ Recommendation: Set TIMESHEET_DATA_SOURCE=mirror in Railway")
    elif data_source == 'mirror':
        print("  ✅ mirror mode - works everywhere (requires sync agent)")
    
    return critical_vars

def check_production_api(test_user_email=None):
    """Test production API endpoint"""
    print("\n" + "=" * 80)
    print("PRODUCTION API CHECK")
    print("=" * 80)
    
    # Use a known working user from verification script
    if not test_user_email:
        test_user_email = "tanzeem.agra@rejlers.ae"  # We just fixed this user
    
    endpoint = f"{PRODUCTION_URL}/api/v1/timesheet/my-attendance/monthly/"
    
    print(f"\n🔍 Testing: {endpoint}")
    print(f"📧 Test User: {test_user_email}")
    
    try:
        # First, try without authentication to see if endpoint is accessible
        response = requests.get(endpoint, timeout=10)
        print(f"\n📡 Response Status: {response.status_code}")
        print(f"📦 Response Size: {len(response.content)} bytes")
        
        if response.status_code == 401:
            print("✅ Endpoint is accessible (401 = auth required, as expected)")
            print("⚠️  Need to test with valid JWT token - user must login at production")
        elif response.status_code == 200:
            print("✅ Endpoint returned 200 OK")
            try:
                data = response.json()
                print(f"📊 Response Data: {data}")
            except:
                print(f"📄 Response Text: {response.text[:500]}")
        else:
            print(f"⚠️  Unexpected status code: {response.status_code}")
            print(f"📄 Response: {response.text[:500]}")
            
    except requests.exceptions.ConnectionError as e:
        print(f"❌ CONNECTION ERROR: {e}")
        print("⚠️  Production server may be down or unreachable")
    except requests.exceptions.Timeout:
        print("❌ TIMEOUT: Server took too long to respond")
    except Exception as e:
        print(f"❌ ERROR: {e}")

def check_local_mirror_data():
    """Check if mirror data exists in local PostgreSQL"""
    print("\n" + "=" * 80)
    print("LOCAL MIRROR DATA CHECK")
    print("=" * 80)
    
    try:
        from apps.timesheet.models import TimesheetEvent, TimesheetUser
        
        event_count = TimesheetEvent.objects.count()
        user_count = TimesheetUser.objects.count()
        
        print(f"\n📊 TimesheetEvent records: {event_count:,}")
        print(f"👥 TimesheetUser records: {user_count:,}")
        
        if event_count == 0:
            print("\n⚠️  WARNING: No mirror data in local PostgreSQL!")
            print("  This is expected if you're using TIMESHEET_DATA_SOURCE=sqlserver")
            print("  For production (Railway), you need:")
            print("    1. Set TIMESHEET_DATA_SOURCE=mirror in Railway env vars")
            print("    2. Run sync agent: python scripts/timesheet_mirror_sync.py --daemon")
        else:
            # Show latest event
            latest = TimesheetEvent.objects.order_by('-event_datetime').first()
            if latest:
                print(f"\n📅 Latest Event:")
                print(f"  Employee: {latest.employee_code} - {latest.employee_name}")
                print(f"  Time: {latest.event_datetime}")
                print(f"  Type: {latest.event_type}")
                
    except Exception as e:
        print(f"❌ Error checking mirror data: {e}")

def generate_railway_env_config():
    """Generate recommended Railway environment variable configuration"""
    print("\n" + "=" * 80)
    print("RECOMMENDED RAILWAY ENVIRONMENT VARIABLES")
    print("=" * 80)
    
    print("""
For production to work, you need to set these in Railway dashboard:
(https://railway.app/project/<your-project>/variables)

CRITICAL - Data Source Configuration:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TIMESHEET_DATA_SOURCE=mirror
  ⚠️  Railway CANNOT access office SQL Server (192.168.99.52) 
  ⚠️  Must use 'mirror' mode to read from PostgreSQL
  
TIMESHEET_MIRROR_API_KEY=1b1KZj9N8jXaHaV8rbHcBdcGrTpvPlAaVr9Y7Pb2IMDpR7a_XH8FTcSSbxlWozhj
  ⚠️  This key must match the one in your office sync agent
  ⚠️  Used to authenticate POST /api/v1/timesheet/mirror/ingest/

Optional - Performance Optimization:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TIMESHEET_CACHE_ENABLED=true
TIMESHEET_CACHE_LIVE_TTL=15
TIMESHEET_CACHE_DAILY_TTL=300
TIMESHEET_CACHE_MONTHLY_TTL=3600
TIMESHEET_CACHE_BACKGROUND_REFRESH=true

After setting these variables:
1. Railway will automatically redeploy
2. Verify deployment completes successfully
3. Check Railway logs for any errors
4. Test production endpoint again
    """)

def check_sync_agent_status():
    """Check if sync agent is configured"""
    print("\n" + "=" * 80)
    print("SYNC AGENT STATUS CHECK")
    print("=" * 80)
    
    sync_script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'timesheet_mirror_sync.py')
    
    if os.path.exists(sync_script):
        print(f"✅ Sync agent script found: {sync_script}")
        print("\nTo start sync agent (from office computer with SQL Server access):")
        print("  python scripts/timesheet_mirror_sync.py --daemon")
        print("\nSync agent configuration:")
        print(f"  Interval: {os.getenv('RADAI_SYNC_INTERVAL_MINUTES', 5)} minutes")
        print(f"  Batch size: {os.getenv('RADAI_SYNC_BATCH_SIZE', 1000)} events")
        print(f"  Lookback: {os.getenv('RADAI_SYNC_LOOKBACK_DAYS', 2)} days")
    else:
        print(f"⚠️  Sync agent script not found at: {sync_script}")

def main():
    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║              PRODUCTION ATTENDANCE DIAGNOSTIC TOOL                         ║
║                                                                            ║
║  Purpose: Diagnose why attendance works locally but not in production     ║
║  Date: 2026-06-26                                                         ║
╚════════════════════════════════════════════════════════════════════════════╝
    """)
    
    # Run all diagnostic checks
    check_environment_config()
    check_local_mirror_data()
    check_sync_agent_status()
    check_production_api()
    generate_railway_env_config()
    
    print("\n" + "=" * 80)
    print("DIAGNOSIS SUMMARY")
    print("=" * 80)
    
    data_source = os.getenv('TIMESHEET_DATA_SOURCE', 'sqlserver')
    
    if data_source == 'sqlserver':
        print("""
⚠️  ROOT CAUSE IDENTIFIED:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your local environment uses TIMESHEET_DATA_SOURCE=sqlserver
This works locally because your computer can access 192.168.99.52 (office LAN)

Railway CANNOT access 192.168.99.52 because:
  • Railway servers are in the cloud
  • Office SQL Server is on internal network only
  • No VPN or tunnel configured

SOLUTION:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Update Railway environment variables:
   TIMESHEET_DATA_SOURCE=mirror
   TIMESHEET_MIRROR_API_KEY=1b1KZj9N8jXaHaV8rbHcBdcGrTpvPlAaVr9Y7Pb2IMDpR7a_XH8FTcSSbxlWozhj

2. Start sync agent (from office computer):
   python scripts/timesheet_mirror_sync.py --daemon
   
   This will continuously sync data from SQL Server → Railway PostgreSQL

3. Verify deployment:
   - Check Railway logs for "mirror mode enabled"
   - Test production endpoint: https://www.radai.ae/api/v1/timesheet/my-attendance/monthly/

4. Monitor sync agent:
   - Should sync every 5 minutes (RADAI_SYNC_INTERVAL_MINUTES)
   - Check Railway logs for POST /api/v1/timesheet/mirror/ingest/ entries
        """)
    else:
        print("""
✅ Configuration looks correct for production (mirror mode)

Next steps:
1. Verify sync agent is running (check office computer)
2. Check Railway environment variables match local .env.local
3. Review Railway deployment logs for errors
4. Test production endpoint with valid JWT token
        """)
    
    print("\n" + "=" * 80)
    print("END OF DIAGNOSTIC")
    print("=" * 80)

if __name__ == '__main__':
    main()
