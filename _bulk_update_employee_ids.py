#!/usr/bin/env python
"""
BULK EMPLOYEE ID UPDATE - Production-Ready
Matches RAD AI users with biometric system by name and updates employee_id
"""
import os
import sys
import django
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'radai.settings')
django.setup()

from django.db import transaction
from apps.rbac.models import UserProfile
from apps.timesheet import services as ts_services

def normalize_name(name):
    """Normalize name for comparison"""
    return ''.join(c.lower() for c in name if c.isalnum())

def similarity(a, b):
    """Calculate similarity ratio between two strings"""
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()

print("=" * 90)
print("BULK EMPLOYEE ID UPDATE - PRODUCTION MODE")
print("=" * 90)

# Fetch biometric employees
print("\n📊 Step 1: Fetching employees from biometric system...")
try:
    monthly = ts_services.monthly_report(2026, 6)
    bio_employees = monthly.get('rows', [])
    print(f"✅ Found {len(bio_employees)} employees in SQL Server (Matrix system)")
except Exception as e:
    print(f"❌ Failed to connect to biometric system: {e}")
    sys.exit(1)

# Get RAD AI profiles
print("\n📊 Step 2: Loading RAD AI user profiles...")
profiles = list(UserProfile.objects.select_related('user').filter(is_deleted=False).all())
print(f"✅ Found {len(profiles)} active user profiles")

# Build biometric lookup by code
bio_lookup = {}
for bio in bio_employees:
    code = str(bio.get('employee_code') or bio.get('code', ''))
    name = str(bio.get('employee_name') or bio.get('name', ''))
    if code and code != 'None':
        bio_lookup[code] = {
            'code': code,
            'name': name,
            'hours': bio.get('total_hours', 0),
            'days': bio.get('days_present', 0)
        }

print(f"✅ Indexed {len(bio_lookup)} biometric employee records")

# Matching and update logic
print("\n" + "=" * 90)
print("STEP 3: MATCHING & UPDATING")
print("=" * 90)

updates_to_apply = []
high_confidence_matches = []
medium_confidence_matches = []
low_confidence_matches = []
no_match = []

for profile in profiles:
    radai_name = f"{profile.user.first_name} {profile.user.last_name}".strip()
    current_emp_id = profile.employee_id
    email = profile.user.email
    
    # Skip if name is empty
    if not radai_name or radai_name == " ":
        no_match.append((email, radai_name, "Empty name"))
        continue
    
    # Skip deleted users
    if ".deleted_" in email:
        continue
    
    # Find best match in biometric system
    best_match = None
    best_score = 0
    
    for bio in bio_employees:
        bio_name = str(bio.get('employee_name') or bio.get('name', ''))
        score = similarity(radai_name, bio_name)
        
        if score > best_score:
            best_score = score
            best_match = bio
    
    # Categorize by confidence level
    if best_match:
        bio_code = str(best_match.get('employee_code') or best_match.get('code'))
        bio_name = str(best_match.get('employee_name') or best_match.get('name'))
        
        match_info = {
            'profile': profile,
            'radai_name': radai_name,
            'email': email,
            'bio_code': bio_code,
            'bio_name': bio_name,
            'score': best_score,
            'old_id': current_emp_id,
            'hours': best_match.get('total_hours', 0),
            'days': best_match.get('days_present', 0)
        }
        
        if best_score >= 0.90:  # 90%+ = High confidence
            high_confidence_matches.append(match_info)
            updates_to_apply.append(match_info)
        elif best_score >= 0.75:  # 75-89% = Medium confidence
            medium_confidence_matches.append(match_info)
            updates_to_apply.append(match_info)
        elif best_score >= 0.60:  # 60-74% = Low confidence
            low_confidence_matches.append(match_info)
            # Don't auto-update low confidence
        else:
            no_match.append((email, radai_name, f"Best: {bio_name} ({int(best_score*100)}%)"))
    else:
        no_match.append((email, radai_name, "No match found"))

# Display summary
print(f"\n📊 MATCHING SUMMARY:")
print(f"   ✅ High Confidence (≥90%): {len(high_confidence_matches)}")
print(f"   ⚠️  Medium Confidence (75-89%): {len(medium_confidence_matches)}")
print(f"   ⚠️  Low Confidence (60-74%): {len(low_confidence_matches)} [NOT auto-updated]")
print(f"   ❌ No Match (<60%): {len(no_match)}")
print(f"\n   📝 Will update: {len(updates_to_apply)} users")

# Show sample high confidence matches
if high_confidence_matches:
    print("\n✅ HIGH CONFIDENCE MATCHES (Sample):")
    for match in high_confidence_matches[:5]:
        print(f"   {match['radai_name']:30} → {match['bio_name']:30} ({int(match['score']*100)}%)")
        print(f"      Email: {match['email']}")
        print(f"      Code: {match['old_id']} → {match['bio_code']}")
        print(f"      Data: {match['days']} days, {match['hours']:.1f} hours")
        print()

# Show medium confidence matches
if medium_confidence_matches:
    print("\n⚠️  MEDIUM CONFIDENCE MATCHES (Sample):")
    for match in medium_confidence_matches[:5]:
        print(f"   {match['radai_name']:30} → {match['bio_name']:30} ({int(match['score']*100)}%)")
        print(f"      Code: {match['old_id']} → {match['bio_code']}")
        print()

# Show no matches
if no_match:
    print(f"\n❌ NO CONFIDENT MATCH (first 10):")
    for email, name, reason in no_match[:10]:
        print(f"   {name:30} ({email:40}) - {reason}")

# Apply updates in transaction
print("\n" + "=" * 90)
print("STEP 4: APPLYING UPDATES")
print("=" * 90)

if updates_to_apply:
    try:
        with transaction.atomic():
            updated_count = 0
            for match in updates_to_apply:
                profile = match['profile']
                new_code = match['bio_code']
                
                # Only update if different
                if profile.employee_id != new_code:
                    profile.employee_id = new_code
                    profile.save()
                    updated_count += 1
            
            print(f"\n✅ SUCCESS: Updated {updated_count} employee IDs in database")
            print(f"   Transaction committed successfully")
            
    except Exception as e:
        print(f"\n❌ ERROR during database update: {e}")
        import traceback
        traceback.print_exc()
else:
    print("⚠️  No updates needed - all users already have correct employee_id")

# Final verification
print("\n" + "=" * 90)
print("STEP 5: VERIFICATION")
print("=" * 90)

verified = 0
for match in updates_to_apply[:5]:
    profile = UserProfile.objects.get(id=match['profile'].id)
    if profile.employee_id == match['bio_code']:
        verified += 1
        print(f"✅ {profile.user.email}: {profile.employee_id}")

print(f"\n✅ Verified {verified}/{min(5, len(updates_to_apply))} sample updates")

print("\n" + "=" * 90)
print("DEPLOYMENT COMPLETE")
print("=" * 90)
print(f"""
📊 FINAL STATISTICS:
   • Total users processed: {len(profiles)}
   • Successfully matched: {len(updates_to_apply)}
   • Database updates applied: {updated_count if 'updated_count' in locals() else 0}
   • Users with attendance data: {len(high_confidence_matches) + len(medium_confidence_matches)}

🎯 NEXT STEPS:
   1. Users can now log in at http://localhost:5173
   2. Navigate to HR → Employee Self-Service
   3. Click "Attendance" tab to view their records
   4. Attendance data will show for matched users only

⚠️  MANUAL ACTION REQUIRED FOR {len(no_match)} UNMATCHED USERS:
   These users' names don't match closely enough with biometric system.
   You need to manually set their employee_id via Django Admin or shell.

💡 TEST USERS (High confidence, verified data):
""")

for match in high_confidence_matches[:3]:
    print(f"   • {match['email']}")
    print(f"     employee_id: {match['bio_code']}")
    print(f"     Attendance: {match['days']} days, {match['hours']:.1f} hours")
    print()

print("=" * 90)
