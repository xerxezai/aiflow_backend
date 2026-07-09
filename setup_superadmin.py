from django.contrib.auth import get_user_model
from apps.rbac.models import Organization, Role, UserProfile, UserRole, Module, RoleModule

User = get_user_model()

org = Organization.objects.get(code='REJ_UAE')
super_admin_role = Role.objects.get(code='super_admin')
user = User.objects.get(email='tanzeem.agra@rejlers.ae')

# Ensure Django-level superuser flags are set
if not user.is_superuser or not user.is_staff:
    user.is_superuser = True
    user.is_staff = True
    user.save()
    print('User flags: is_superuser=True, is_staff=True set')
else:
    print('User flags: already is_superuser=True, is_staff=True')

# Create / update UserProfile
profile, created = UserProfile.objects.get_or_create(
    user=user,
    defaults={
        'organization': org,
        'status': 'active',
        'department': 'Engineering',
        'job_title': 'Super Administrator',
        'employee_id': 'EMP001',
    }
)
if not created:
    profile.organization = org
    profile.status = 'active'
    profile.save()
print('UserProfile:', 'created' if created else 'updated', '| org:', profile.organization.name)

# Assign Super Admin role via UserProfile
ur, created = UserRole.objects.get_or_create(user_profile=profile, role=super_admin_role)
print('UserRole:', 'created' if created else 'already exists', '| role:', super_admin_role.name)

# Grant all modules to super_admin role
all_modules = Module.objects.all()
added = 0
for mod in all_modules:
    rm, c = RoleModule.objects.get_or_create(role=super_admin_role, module=mod)
    if c:
        added += 1
total = RoleModule.objects.filter(role=super_admin_role).count()
print('RoleModules:', added, 'new modules added | total:', total)

print()
print('=== FINAL STATUS ===')
print('User:', user.email)
print('is_superuser:', user.is_superuser, '| is_staff:', user.is_staff, '| is_active:', user.is_active)
print('Profile org:', profile.organization.name)
roles = list(UserRole.objects.filter(user_profile=profile).values_list('role__name', flat=True))
print('Roles:', roles)
