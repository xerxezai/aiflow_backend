#!/usr/bin/env python
"""
Quick User Sync Script - Import users from production to local
Safe wrapper around sync_data management command

Usage:
    python sync_users_from_production.py           # Dry run (preview only)
    python sync_users_from_production.py --apply   # Actually sync users
"""
import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.management import call_command

def main():
    dry_run = '--apply' not in sys.argv
    
    print("\n" + "="*70)
    print("RAD AI - Quick User Sync (Production → Local)")
    print("="*70)
    print(f"Mode: {'DRY RUN (preview only)' if dry_run else 'LIVE (will import users)'}")
    print("="*70 + "\n")
    
    # Confirm if not dry run
    if not dry_run:
        confirm = input("⚠️  This will import production users to local database.\n"
                       "   Existing users will be updated. Continue? (yes/no): ")
        if confirm.lower() != 'yes':
            print("\n❌ Cancelled by user\n")
            return
    
    # Run sync command
    args = [
        '--source', 'production',
        '--target', 'local',
        '--entity', 'users',
        '--verbose',
    ]
    
    if dry_run:
        args.append('--dry-run')
    
    try:
        call_command('sync_data', *args)
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        return 1
    
    if dry_run:
        print("\n💡 To actually import users, run:")
        print("   python sync_users_from_production.py --apply\n")
    else:
        print("\n✅ Users synced successfully!")
        print("   Refresh http://localhost:5173/admin/users to see imported users\n")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
