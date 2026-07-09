"""
Django management command to set Lira's employee_id in production
Usage: python manage.py set_lira_employee_id
"""
from django.core.management.base import BaseCommand
from apps.rbac.models import UserProfile


class Command(BaseCommand):
    help = 'Set employee_id for lira.viaga@rejlers.ae'

    def handle(self, *args, **options):
        email = 'lira.viaga@rejlers.ae'
        employee_id = '21573'  # Airene Lira Viaga from biometric system
        
        try:
            profile = UserProfile.objects.get(user__email=email)
            
            self.stdout.write(f'Found user: {email}')
            self.stdout.write(f'Current employee_id: {profile.employee_id}')
            
            profile.employee_id = employee_id
            profile.save()
            
            self.stdout.write(self.style.SUCCESS(
                f'✅ Successfully updated employee_id to: {employee_id}'
            ))
            
        except UserProfile.DoesNotExist:
            self.stdout.write(self.style.ERROR(
                f'❌ User not found: {email}'
            ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f'❌ Error: {str(e)}'
            ))
