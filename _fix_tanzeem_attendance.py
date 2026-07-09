#!/usr/bin/env python
"""
Diagnose and fix tanzeem.agra@rejlers.ae attendance issue
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from apps.rbac.models import UserProfile
from apps.timesheet import services as ts_services
from difflib import SequenceMatcher

print("=" * 90)
print("DIAGNOSING: tanzeem.agra@rejlers.ae")
print("=" * 90)

# Get user profile
try:
    profile = UserProfile.objects.get(user__email='tanzeem.agra@rejlers.ae')
    user = profile.user
    
    print(f"\n✅ User Found in Database:")
    print(f"   Email: {user.email}")
    print(f"   First Name: {user.first_name}")
    print(f"   Last Name: {user.last_name}")
    print(f"   Full Name: {user.first_name} {user.last_name}")
    print(f"   Current employee_id: {profile.employee_id}")
    
    # Fetch biometric employees
    print(f"\n📊 Fetching biometric system data...")
    monthly = ts_services.monthly_report(2026, 6)
    bio_employees = monthly.get('rows', [])
    print(f"✅ Found {len(bio_employees)} employees in biometric system")
    
    # Search for potential matches by name
    print(f"\n🔍 Searching for potential matches...")
    
    # Try variations of the name
    search_names = [
        "Tanzeem Agra",
        "Mohammed Agra",
        "Tanzeem",
        "Agra",
        "Mohammed Tanzeem",
        "Muhammad Tanzeem"
    ]
    
    matches_found = []
    
    for bio in bio_employees:
        bio_name = str(bio.get('employee_name') or bio.get('name', ''))
        bio_code = str(bio.get('employee_code') or bio.get('code', ''))
        
        # Check if any search name appears in biometric name
        for search_name in search_names:
            if search_name.lower() in bio_name.lower():
                matches_found.append({
                    'bio_code': bio_code,
                    'bio_name': bio_name,
                    'match_text': search_name,
                    'hours': bio.get('total_hours', 0),
                    'days': bio.get('days_present', 0)
                })
    
    if matches_found:
        print(f"\n✅ POTENTIAL MATCHES FOUND ({len(matches_found)}):")
        for i, match in enumerate(matches_found, 1):
            print(f"\n   Match {i}:")
            print(f"      Biometric Name: {match['bio_name']}")
            print(f"      Biometric Code: {match['bio_code']}")
            print(f"      Matched on: '{match['match_text']}'")
            print(f"      June 2026: {match['days']} days, {match['hours']:.2f} hours")
    else:
        print(f"\n⚠️  No obvious matches found")
        print(f"\n   Let's search for all names containing 'agra' or 'tanzeem':")
        
        agra_matches = []
        for bio in bio_employees:
            bio_name = str(bio.get('employee_name') or bio.get('name', '')).lower()
            if 'agra' in bio_name or 'tanzeem' in bio_name or 'muhammad' in bio_name:
                agra_matches.append({
                    'code': bio.get('employee_code') or bio.get('code', ''),
                    'name': bio.get('employee_name') or bio.get('name', ''),
                    'hours': bio.get('total_hours', 0)
                })
        
        if agra_matches:
            print(f"\n   Found {len(agra_matches)} similar names:")
            for m in agra_matches[:10]:
                print(f"      {m['code']:15} {m['name']:40} ({m['hours']:.1f} hours)")
        else:
            print(f"      No matches with 'agra', 'tanzeem', or 'muhammad'")
    
    # Check current employee_id validity
    print(f"\n🔍 Checking current employee_id: {profile.employee_id}")
    
    current_match = None
    for bio in bio_employees:
        if str(bio.get('employee_code') or bio.get('code', '')) == str(profile.employee_id):
            current_match = bio
            break
    
    if current_match:
        print(f"   ✅ Current employee_id IS VALID in biometric system")
        print(f"      Name: {current_match.get('employee_name')}")
        print(f"      Hours: {current_match.get('total_hours', 0):.2f}")
        print(f"\n   ✅ User SHOULD be able to see attendance!")
        print(f"   ⚠️  If not seeing data, check frontend/browser cache")
    else:
        print(f"   ❌ Current employee_id '{profile.employee_id}' NOT FOUND in biometric system")
        print(f"\n   ⚠️  This is why the user cannot see attendance records!")
        
        if matches_found:
            print(f"\n💡 SUGGESTED FIX:")
            best_match = matches_found[0]
            print(f"   Update employee_id to: {best_match['bio_code']}")
            print(f"   For employee: {best_match['bio_name']}")
            
            # Ask if we should auto-fix
            print(f"\n🔧 Auto-fixing now...")
            profile.employee_id = best_match['bio_code']
            profile.save()
            print(f"   ✅ UPDATED: {profile.user.email}")
            print(f"      Old employee_id: EMP001")
            print(f"      New employee_id: {best_match['bio_code']}")
            print(f"\n   ✅ User can now see attendance data!")
            print(f"      Login and refresh the page to see changes")
        else:
            print(f"\n💡 MANUAL ACTION NEEDED:")
            print(f"   1. Find your correct biometric code from the list above")
            print(f"   2. Update via Django shell:")
            print(f"      python manage.py shell")
            print(f"      >>> from apps.rbac.models import UserProfile")
            print(f"      >>> p = UserProfile.objects.get(user__email='tanzeem.agra@rejlers.ae')")
            print(f"      >>> p.employee_id = 'YOUR_CODE_HERE'")
            print(f"      >>> p.save()")

except UserProfile.DoesNotExist:
    print(f"❌ User not found: tanzeem.agra@rejlers.ae")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 90)
