#!/usr/bin/env python
"""Auto-map employee IDs by matching names between RAD AI and biometric system"""
import os
import sys
import django
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from apps.rbac.models import UserProfile
from apps.timesheet import services as ts_services

def normalize_name(name):
    """Normalize name for comparison"""
    return ''.join(c.lower() for c in name if c.isalnum())

def similarity(a, b):
    """Calculate similarity ratio between two strings"""
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()

print("=" * 80)
print("AUTO-MAPPING EMPLOYEE IDs")
print("=" * 80)

# Fetch biometric employees
print("\n📊 Fetching employees from biometric system...")
monthly = ts_services.monthly_report(2026, 6)
bio_employees = monthly.get('rows', [])
print(f"✅ Found {len(bio_employees)} employees in biometric system")

# Get RAD AI profiles
profiles = UserProfile.objects.select_related('user').filter(is_deleted=False).all()
print(f"✅ Found {len(profiles)} user profiles in RAD AI\n")

print("=" * 80)
print("MATCHING RESULTS")
print("=" * 80)

matched_count = 0
unmatched_count = 0
updated_count = 0

for profile in profiles:
    radai_name = f"{profile.user.first_name} {profile.user.last_name}".strip()
    current_emp_id = profile.employee_id
    
    # Skip if name is empty
    if not radai_name or radai_name == " ":
        continue
    
    # Find best match in biometric system
    best_match = None
    best_score = 0
    
    for bio_emp in bio_employees:
        bio_name = str(bio_emp.get('employee_name') or bio_emp.get('name', ''))
        score = similarity(radai_name, bio_name)
        
        if score > best_score:
            best_score = score
            best_match = bio_emp
    
    # If match score is above threshold (80%), suggest it
    if best_match and best_score >= 0.70:
        bio_code = str(best_match.get('employee_code') or best_match.get('code'))
        bio_name = str(best_match.get('employee_name') or best_match.get('name'))
        
        print(f"\n✅ MATCH FOUND ({int(best_score*100)}% confidence)")
        print(f"   RAD AI: {radai_name} ({profile.user.email})")
        print(f"   Biometric: {bio_name} (Code: {bio_code})")
        print(f"   Current employee_id: {current_emp_id}")
        print(f"   Suggested employee_id: {bio_code}")
        
        # Auto-update if employee_id doesn't match the biometric code
        if current_emp_id != bio_code:
            old_id = current_emp_id or "(empty)"
            profile.employee_id = bio_code
            profile.save()
            print(f"   ✅ UPDATED: {old_id} → {bio_code}")
            updated_count += 1
        else:
            print(f"   ℹ️  Already correct: {bio_code}")
        
        matched_count += 1
    else:
        print(f"\n⚠️  NO CONFIDENT MATCH")
        print(f"   RAD AI: {radai_name} ({profile.user.email})")
        if best_match:
            bio_name = str(best_match.get('employee_name') or best_match.get('name'))
            bio_code = str(best_match.get('employee_code') or best_match.get('code'))
            print(f"   Best guess: {bio_name} (Code: {bio_code}) - {int(best_score*100)}% match")
        unmatched_count += 1

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"✅ Matched: {matched_count}")
print(f"⚠️  Unmatched: {unmatched_count}")
print(f"✅ Updated: {updated_count}")
print("\n💡 TIP: Users should now see their attendance data at http://localhost:5173/hr/leave")
print("   Refresh the page (Ctrl+Shift+R) to see the changes!")
print("=" * 80)
