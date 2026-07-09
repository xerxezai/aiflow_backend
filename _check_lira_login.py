import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

email = 'lira.viaga@rejlers.ae'

try:
    user = User.objects.get(email=email)
    print(f"✅ User found: {email}")
    print(f"   Username: {user.username}")
    print(f"   First Name: {user.first_name}")
    print(f"   Last Name: {user.last_name}")
    print(f"   Is Active: {user.is_active}")
    print(f"   Is Staff: {user.is_staff}")
    print(f"   Is Superuser: {user.is_superuser}")
    print(f"   Has Usable Password: {user.has_usable_password()}")
    print(f"   Last Login: {user.last_login}")
    print(f"   Date Joined: {user.date_joined}")
    
    # Check UserProfile
    try:
        profile = UserProfile.objects.get(user=user)
        print(f"\n✅ UserProfile found:")
        print(f"   Employee ID: {profile.employee_id}")
        print(f"   Job Title: {profile.job_title}")
        print(f"   Department: {profile.department}")
        print(f"   Phone: {profile.phone_number}")
    except UserProfile.DoesNotExist:
        print(f"\n❌ No UserProfile found for {email}")
    
    # Check if password needs to be reset
    if not user.has_usable_password():
        print(f"\n⚠️  WARNING: User has no usable password!")
        print(f"   Setting default password: 'Password123'")
        user.set_password('Password123')
        user.save()
        print(f"   ✅ Password set successfully")
    
    # Ensure user is active
    if not user.is_active:
        print(f"\n⚠️  WARNING: User is not active!")
        print(f"   Activating user...")
        user.is_active = True
        user.save()
        print(f"   ✅ User activated")
    
except User.DoesNotExist:
    print(f"❌ User not found: {email}")
    print(f"\nCreating user...")
    user = User.objects.create_user(
        username='lira.viaga',
        email=email,
        password='Password123',
        first_name='Lira',
        last_name='Viaga'
    )
    print(f"✅ User created: {email}")
    print(f"   Password: Password123")
    
    # Create UserProfile
    profile = UserProfile.objects.create(
        user=user,
        employee_id='21573',
        department='Human Resources',
        job_title='HR Officer'
    )
    print(f"✅ UserProfile created with employee_id: 21573")

print(f"\n{'='*60}")
print(f"LOGIN CREDENTIALS:")
print(f"{'='*60}")
print(f"Email: {email}")
print(f"Password: Password123")
print(f"URL: http://localhost:5173/login")
print(f"{'='*60}")
