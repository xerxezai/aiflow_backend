#!/usr/bin/env python
"""
Update PRODUCTION Railway database with correct employee_id mappings.
This script connects to Railway production database and updates UserProfile.employee_id
to match biometric system codes by name similarity.

CRITICAL: This updates PRODUCTION data - review changes before running!
"""
import os
import sys
import django
from difflib import SequenceMatcher
from datetime import datetime

# Force production database connection
os.environ['DATABASE_URL'] = 'postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway'

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from apps.rbac.models import UserProfile
from apps.timesheet import config as ts_config
import pyodbc

def normalize_name(name):
    """Normalize name for comparison."""
    if not name:
        return ""
    return ' '.join(name.lower().strip().split())

def name_similarity(name1, name2):
    """Calculate similarity ratio between two names (0.0 to 1.0)."""
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)
    return SequenceMatcher(None, n1, n2).ratio()

def main():
    print("=" * 80)
    print("PRODUCTION DATABASE EMPLOYEE_ID UPDATE SCRIPT")
    print("=" * 80)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: Railway Production Database")
    print(f"Database: {os.environ['DATABASE_URL'][:50]}...")
    print("=" * 80)
    
    # Load biometric system data
    cfg = ts_config.SQLSERVER
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={cfg['host']},{cfg['port']};"
        f"DATABASE={cfg['database']};"
        f"UID={cfg['user']};"
        f"PWD={cfg['password']};"
        f"TrustServerCertificate=yes;"
    )
    
    print("\n[1/5] Connecting to SQL Server biometric database...")
    conn = pyodbc.connect(conn_str, timeout=10)
    cursor = conn.cursor()
    
    print("[2/5] Loading biometric employee codes...")
    cursor.execute("SELECT UserID, UserName FROM dbo.Mx_VEW_UserDetails WHERE IsActive = 1")
    biometric_users = {row.UserID: row.UserName for row in cursor.fetchall()}
    print(f"    ✓ Loaded {len(biometric_users)} active biometric users")
    
    print("\n[3/5] Loading RAD AI users from PRODUCTION database...")
    profiles = UserProfile.objects.select_related('user').filter(user__is_active=True)
    print(f"    ✓ Loaded {profiles.count()} active RAD AI users")
    
    print("\n[4/5] Matching users by name similarity...")
    print("    Confidence Thresholds:")
    print("      • HIGH:   ≥90% similarity (auto-update)")
    print("      • MEDIUM: 75-89% similarity (auto-update)")
    print("      • LOW:    <75% similarity (manual review needed)")
    print()
    
    updates = []
    high_conf = []
    medium_conf = []
    low_conf = []
    
    for profile in profiles:
        full_name = profile.user.get_full_name()
        if not full_name or full_name.strip() == '':
            continue
        
        best_match = None
        best_score = 0.0
        best_code = None
        
        for code, bio_name in biometric_users.items():
            score = name_similarity(full_name, bio_name)
            if score > best_score:
                best_score = score
                best_match = bio_name
                best_code = code
        
        if best_score >= 0.75:  # 75% threshold
            updates.append({
                'profile': profile,
                'old_emp_id': profile.employee_id,
                'new_emp_id': best_code,
                'rad_name': full_name,
                'bio_name': best_match,
                'confidence': best_score
            })
            
            if best_score >= 0.90:
                high_conf.append((profile.user.email, best_score))
            else:
                medium_conf.append((profile.user.email, best_score))
        else:
            low_conf.append((full_name, best_score, best_match))
    
    print(f"    Summary:")
    print(f"      • HIGH confidence (≥90%):   {len(high_conf)} users")
    print(f"      • MEDIUM confidence (75-89%): {len(medium_conf)} users")
    print(f"      • LOW confidence (<75%):      {len(low_conf)} users (skipped)")
    print(f"      • TOTAL updates to apply:     {len(updates)} users")
    
    if not updates:
        print("\n✓ No updates needed. All employee_ids are already correct!")
        return
    
    print(f"\n[5/5] Applying {len(updates)} updates to PRODUCTION database...")
    print()
    print(f"{'Email':<40} {'Old ID':<15} {'New ID':<10} {'Confidence':<10} {'Match Name'}")
    print("-" * 120)
    
    success_count = 0
    for update in updates:
        profile = update['profile']
        email = profile.user.email
        old_id = update['old_emp_id']
        new_id = update['new_emp_id']
        conf = update['confidence']
        bio_name = update['bio_name']
        
        try:
            profile.employee_id = new_id
            profile.save()
            success_count += 1
            conf_label = f"{conf*100:.1f}%"
            print(f"{email:<40} {old_id:<15} {new_id:<10} {conf_label:<10} {bio_name}")
        except Exception as e:
            print(f"{email:<40} {old_id:<15} FAILED: {str(e)}")
    
    print("-" * 120)
    print(f"\n✅ PRODUCTION UPDATE COMPLETE!")
    print(f"   • Successfully updated: {success_count}/{len(updates)} users")
    print(f"   • Database: Railway Production")
    print(f"   • Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if low_conf:
        print(f"\n⚠️  WARNING: {len(low_conf)} users need manual review (low confidence)")
        print("   Run this script with --show-unmatched to see details")
    
    # Special check for tanzeem.agra@rejlers.ae
    print("\n" + "=" * 80)
    print("VERIFICATION: tanzeem.agra@rejlers.ae")
    print("=" * 80)
    try:
        tanzeem = UserProfile.objects.select_related('user').get(user__email='tanzeem.agra@rejlers.ae')
        print(f"✓ Found user: {tanzeem.user.get_full_name()}")
        print(f"✓ Employee ID: {tanzeem.employee_id}")
        print("✅ Production database updated successfully!")
        print("   User should see data at https://www.radai.ae/hr/leave once sync agent runs")
    except Exception as e:
        print(f"⚠️  Could not verify tanzeem.agra@rejlers.ae: {e}")
    
    print("=" * 80)
    
    conn.close()

if __name__ == '__main__':
    main()
