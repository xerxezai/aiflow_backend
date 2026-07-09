"""
User-Timesheet Auto-Synchronization Signals

Automatically keeps UserProfile ↔ BiometricUserMaster in sync when:
  - New user is created → create matching BiometricUserMaster
  - User profile is updated → sync changes to BiometricUserMaster
  - BiometricUserMaster is updated → sync changes to UserProfile

Soft-coded configuration: AUTO_SYNC_CONFIG

Created: 2025-02-11 (Smart User-Timesheet Sync)
"""

import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile
from apps.timesheet.models import BiometricUserMaster

User = get_user_model()
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════
AUTO_SYNC_CONFIG = {
    # Enable/disable auto-sync
    'enabled': True,
    
    # Auto-create BiometricUserMaster when UserProfile is created?
    'auto_create_biometric': True,
    
    # Auto-create UserProfile when BiometricUserMaster is created? (risky - be careful)
    'auto_create_userprofile': False,
    
    # Which direction(s) to sync?
    'sync_directions': {
        'user_to_biometric': True,   # UserProfile changes → BiometricUserMaster
        'biometric_to_user': True,   # BiometricUserMaster changes → UserProfile
    },
    
    # Which fields to sync?
    'sync_fields': {
        'email': True,           # Sync office_email ↔ user.email
        'name': True,            # Sync full_name ↔ user first/last name
        'department': True,      # Sync department
        'designation': True,     # Sync designation ↔ job_title
    },
    
    # Conflict resolution: which system is the source of truth?
    'source_of_truth': {
        'email': 'userprofile',       # UserProfile controls email
        'name': 'biometric',          # BiometricUserMaster controls name (from HR)
        'department': 'biometric',    # BiometricUserMaster controls department (from HR)
        'designation': 'biometric',   # BiometricUserMaster controls designation (from HR)
    },
    
    # Default values for new BiometricUserMaster records
    'defaults': {
        'designation': 'Staff',
        'department': 'General',
    },
    
    # How to generate employee_id if missing
    'employee_id_strategy': 'email_prefix',  # 'email_prefix' | 'sequential' | 'uuid'
}


# ═════════════════════════════════════════════════════════════════════════════
# SIGNAL HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

@receiver(post_save, sender=UserProfile)
def sync_userprofile_to_biometric(sender, instance, created, **kwargs):
    """
    When UserProfile is created/updated, sync to BiometricUserMaster.
    
    Flow:
        1. UserProfile saved
        2. Auto-assign employee_id if missing (using strategy)
        3. Get or create matching BiometricUserMaster
        4. Sync fields based on source_of_truth rules
    """
    if not AUTO_SYNC_CONFIG['enabled']:
        return
    
    if not AUTO_SYNC_CONFIG['sync_directions']['user_to_biometric']:
        return
    
    try:
        user = instance.user
        
        # Step 1: Ensure employee_id exists
        if not instance.employee_id:
            instance.employee_id = _generate_employee_id(user, instance)
            instance.save(update_fields=['employee_id'])
        
        employee_id = instance.employee_id
        
        # Step 2: Get or create BiometricUserMaster
        bio, bio_created = BiometricUserMaster.objects.get_or_create(
            employee_code=employee_id,
            defaults={
                'full_name': f'{user.first_name} {user.last_name}'.strip() or user.username,
                'office_email': user.email,
                'designation': instance.job_title or AUTO_SYNC_CONFIG['defaults']['designation'],
                'department': instance.department or AUTO_SYNC_CONFIG['defaults']['department'],
            }
        )
        
        if bio_created:
            logger.info(f"[AUTO-SYNC] Created BiometricUserMaster: {employee_id} for {user.email}")
            return
        
        # Step 3: Sync fields (UserProfile → BiometricUserMaster)
        updated = False
        updates = {}
        
        # Sync email (if UserProfile is source of truth)
        if AUTO_SYNC_CONFIG['sync_fields']['email'] and \
           AUTO_SYNC_CONFIG['source_of_truth']['email'] == 'userprofile':
            if bio.office_email != user.email:
                updates['office_email'] = user.email
                updated = True
        
        # Sync name (if UserProfile is source of truth)
        if AUTO_SYNC_CONFIG['sync_fields']['name'] and \
           AUTO_SYNC_CONFIG['source_of_truth']['name'] == 'userprofile':
            full_name = f'{user.first_name} {user.last_name}'.strip()
            if full_name and bio.full_name != full_name:
                updates['full_name'] = full_name
                updated = True
        
        # Sync department (if UserProfile is source of truth)
        if AUTO_SYNC_CONFIG['sync_fields']['department'] and \
           AUTO_SYNC_CONFIG['source_of_truth']['department'] == 'userprofile':
            if instance.department and bio.department != instance.department:
                updates['department'] = instance.department
                updated = True
        
        # Sync designation (if UserProfile is source of truth)
        if AUTO_SYNC_CONFIG['sync_fields']['designation'] and \
           AUTO_SYNC_CONFIG['source_of_truth']['designation'] == 'userprofile':
            if instance.job_title and bio.designation != instance.job_title:
                updates['designation'] = instance.job_title
                updated = True
        
        if updated:
            for field, value in updates.items():
                setattr(bio, field, value)
            bio.save()
            logger.info(f"[AUTO-SYNC] Updated BiometricUserMaster {employee_id}: {list(updates.keys())}")
    
    except Exception as e:
        logger.error(f"[AUTO-SYNC] Error syncing UserProfile {instance.id} → BiometricUserMaster: {e}")


@receiver(post_save, sender=BiometricUserMaster)
def sync_biometric_to_userprofile(sender, instance, created, **kwargs):
    """
    When BiometricUserMaster is created/updated, sync to UserProfile.
    
    Flow:
        1. BiometricUserMaster saved
        2. Find matching UserProfile by employee_id
        3. If not found, try matching by email
        4. Sync fields based on source_of_truth rules
    """
    if not AUTO_SYNC_CONFIG['enabled']:
        return
    
    if not AUTO_SYNC_CONFIG['sync_directions']['biometric_to_user']:
        return
    
    try:
        # Step 1: Find matching UserProfile by employee_id
        profile = UserProfile.objects.filter(
            employee_id=instance.employee_code,
            is_deleted=False
        ).first()
        
        # Step 2: If not found, try matching by email
        if not profile and instance.office_email:
            user = User.objects.filter(email__iexact=instance.office_email).first()
            if user:
                profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        
        if not profile:
            # No matching UserProfile found
            if created and AUTO_SYNC_CONFIG['auto_create_userprofile']:
                # Optionally create UserProfile (risky - usually don't do this)
                logger.warning(f"[AUTO-SYNC] BiometricUserMaster {instance.employee_code} has no matching UserProfile")
            return
        
        # Step 3: Sync fields (BiometricUserMaster → UserProfile)
        updated = False
        updates = {}
        
        # Update employee_id if missing
        if not profile.employee_id:
            updates['employee_id'] = instance.employee_code
            updated = True
        
        # Sync department (if BiometricUserMaster is source of truth)
        if AUTO_SYNC_CONFIG['sync_fields']['department'] and \
           AUTO_SYNC_CONFIG['source_of_truth']['department'] == 'biometric':
            if instance.department and profile.department != instance.department:
                updates['department'] = instance.department
                updated = True
        
        # Sync designation (if BiometricUserMaster is source of truth)
        if AUTO_SYNC_CONFIG['sync_fields']['designation'] and \
           AUTO_SYNC_CONFIG['source_of_truth']['designation'] == 'biometric':
            if instance.designation and profile.job_title != instance.designation:
                updates['job_title'] = instance.designation
                updated = True
        
        if updated:
            for field, value in updates.items():
                setattr(profile, field, value)
            profile.save()
            logger.info(f"[AUTO-SYNC] Updated UserProfile {profile.user.email}: {list(updates.keys())}")
    
    except Exception as e:
        logger.error(f"[AUTO-SYNC] Error syncing BiometricUserMaster {instance.employee_code} → UserProfile: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _generate_employee_id(user, profile):
    """Generate employee_id for a user based on configured strategy."""
    strategy = AUTO_SYNC_CONFIG['employee_id_strategy']
    
    if strategy == 'email_prefix':
        # Use email username as employee_id (e.g., john.doe@company.com → JOHN.DOE)
        return user.email.split('@')[0].upper()
    
    elif strategy == 'sequential':
        # Find highest existing employee_id that's numeric and increment
        existing_ids = UserProfile.objects.filter(
            employee_id__regex=r'^\d+$'
        ).values_list('employee_id', flat=True)
        max_id = max([int(eid) for eid in existing_ids] + [1000])
        return str(max_id + 1)
    
    elif strategy == 'uuid':
        # Use first 8 chars of user UUID
        return str(profile.id)[:8].upper()
    
    else:
        # Fallback to email prefix
        return user.email.split('@')[0].upper()
