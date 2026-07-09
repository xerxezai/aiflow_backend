"""
Smart Data Synchronization Management Command
Syncs users, profiles, roles, and RBAC data between local and production databases

Usage:
    # Import users from production to local
    python manage.py sync_data --source production --target local --entity users

    # Export users from local to production
    python manage.py sync_data --source local --target production --entity users

    # Sync all data (users, roles, organizations)
    python manage.py sync_data --source production --target local --entity all

    # Dry run (no changes)
    python manage.py sync_data --source production --target local --entity users --dry-run
"""
import os
import sys
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.contrib.auth import get_user_model
from django.apps import apps
from django.conf import settings
import json
from datetime import datetime, date
from decimal import Decimal


# ===================================================================
# SOFT-CODED CONFIGURATION
# ===================================================================
SYNC_CONFIG = {
    # Entities to sync in dependency order (respect foreign keys)
    'sync_order': [
        'organizations',
        'modules',
        'permissions',
        'roles',
        'role_modules',
        'role_permissions',
        'users',
        'user_profiles',
        'rbac_profiles',
    ],
    
    # Model mappings (app.Model)
    'models': {
        'organizations': 'rbac.Organization',
        'modules': 'rbac.Module',
        'permissions': 'rbac.Permission',
        'roles': 'rbac.Role',
        'role_modules': 'rbac.RoleModule',
        'role_permissions': 'rbac.RolePermission',
        'users': 'users.User',
        'user_profiles': 'users.UserProfile',
        'rbac_profiles': 'rbac.UserProfile',
    },
    
    # Fields to exclude from sync (auto-generated or sensitive)
    'exclude_fields': {
        'users': ['last_login', 'date_joined', 'password'],  # Keep existing passwords
        'user_profiles': [],
        'rbac_profiles': [],
        'organizations': [],
        'modules': [],
        'permissions': [],
        'roles': [],
        'role_modules': [],
        'role_permissions': [],
    },
    
    # Conflict resolution strategy
    'conflict_strategy': {
        'users': 'update',  # update existing users
        'user_profiles': 'update',
        'rbac_profiles': 'update',
        'organizations': 'skip',  # don't update existing orgs
        'modules': 'skip',
        'permissions': 'skip',
        'roles': 'skip',
        'role_modules': 'skip',
        'role_permissions': 'skip',
    },
    
    # Database connection settings
    'databases': {
        'local': {
            'ENGINE': 'django.db.backends.postgresql',
            'HOST': os.getenv('DB_HOST', 'postgres_local'),
            'PORT': os.getenv('DB_PORT', '5432'),
            'NAME': os.getenv('DB_NAME', 'aiflow_dev'),
            'USER': os.getenv('DB_USER', 'aiflow_user'),
            'PASSWORD': os.getenv('DB_PASSWORD', 'aiflow_local_pass_123'),
        },
        'production': {
            # Parse from DATABASE_URL or use individual settings
            'url': os.getenv('RAILWAY_DATABASE_URL', 
                           'postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway'),
        }
    }
}


class Command(BaseCommand):
    help = 'Sync data between local and production databases (users, roles, RBAC)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            type=str,
            choices=['local', 'production'],
            required=True,
            help='Source database to read from'
        )
        parser.add_argument(
            '--target',
            type=str,
            choices=['local', 'production'],
            required=True,
            help='Target database to write to'
        )
        parser.add_argument(
            '--entity',
            type=str,
            choices=['all', 'users', 'roles', 'organizations', 'rbac'],
            default='users',
            help='What to sync (default: users)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without making changes'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )

    def handle(self, *args, **options):
        source = options['source']
        target = options['target']
        entity = options['entity']
        dry_run = options['dry_run']
        verbose = options['verbose']

        # Validation
        if source == target:
            raise CommandError('Source and target must be different')

        self.stdout.write(self.style.SUCCESS('\n' + '='*70))
        self.stdout.write(self.style.SUCCESS('RAD AI Data Synchronization Tool'))
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write(f'Source:      {source.upper()}')
        self.stdout.write(f'Target:      {target.upper()}')
        self.stdout.write(f'Entity:      {entity}')
        self.stdout.write(f'Mode:        {"DRY RUN (no changes)" if dry_run else "LIVE (will modify data)"}')
        self.stdout.write('='*70 + '\n')

        # Determine which entities to sync
        entities_to_sync = self._get_entities_to_sync(entity)
        
        if verbose:
            self.stdout.write(f'Will sync: {", ".join(entities_to_sync)}\n')

        # Setup database connections
        self._setup_connections(source, target)

        # Sync each entity in order
        stats = {
            'total': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
        }

        for entity_name in entities_to_sync:
            try:
                entity_stats = self._sync_entity(
                    entity_name, 
                    source, 
                    target, 
                    dry_run, 
                    verbose
                )
                stats['total'] += entity_stats['total']
                stats['created'] += entity_stats['created']
                stats['updated'] += entity_stats['updated']
                stats['skipped'] += entity_stats['skipped']
                stats['errors'] += entity_stats['errors']
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'✗ Error syncing {entity_name}: {e}')
                )
                stats['errors'] += 1
                if verbose:
                    import traceback
                    traceback.print_exc()

        # Summary
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS('Synchronization Complete'))
        self.stdout.write('='*70)
        self.stdout.write(f'Total records:   {stats["total"]}')
        self.stdout.write(self.style.SUCCESS(f'✓ Created:       {stats["created"]}'))
        self.stdout.write(self.style.SUCCESS(f'✓ Updated:       {stats["updated"]}'))
        self.stdout.write(self.style.WARNING(f'- Skipped:       {stats["skipped"]}'))
        if stats['errors'] > 0:
            self.stdout.write(self.style.ERROR(f'✗ Errors:        {stats["errors"]}'))
        self.stdout.write('='*70 + '\n')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '⚠️  This was a DRY RUN - no data was modified\n'
                '   Remove --dry-run to apply changes\n'
            ))

    def _get_entities_to_sync(self, entity):
        """Determine which entities to sync based on user selection"""
        if entity == 'all':
            return SYNC_CONFIG['sync_order']
        elif entity == 'users':
            return ['users', 'user_profiles', 'rbac_profiles']
        elif entity == 'roles':
            return ['roles', 'role_modules', 'role_permissions']
        elif entity == 'organizations':
            return ['organizations']
        elif entity == 'rbac':
            return ['organizations', 'modules', 'permissions', 'roles', 
                    'role_modules', 'role_permissions']
        else:
            return [entity]

    def _setup_connections(self, source, target):
        """Setup database connections if not in Django DATABASES"""
        # Use default connection for local
        if source == 'local' or target == 'local':
            pass  # Already configured in settings

        # Setup production connection if needed
        if source == 'production' or target == 'production':
            if 'production' not in connections.databases:
                prod_url = SYNC_CONFIG['databases']['production']['url']
                # Parse DATABASE_URL format: postgresql://user:pass@host:port/dbname
                import re
                match = re.match(
                    r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)',
                    prod_url
                )
                if match:
                    user, password, host, port, dbname = match.groups()
                    connections.databases['production'] = {
                        'ENGINE': 'django.db.backends.postgresql',
                        'NAME': dbname,
                        'USER': user,
                        'PASSWORD': password,
                        'HOST': host,
                        'PORT': port,
                        'CONN_MAX_AGE': 0,
                        'CONN_HEALTH_CHECKS': False,
                        'AUTOCOMMIT': True,
                        'ATOMIC_REQUESTS': False,
                        'TIME_ZONE': None,
                        'OPTIONS': {},
                    }

    def _sync_entity(self, entity_name, source, target, dry_run, verbose):
        """Sync a specific entity from source to target"""
        model_path = SYNC_CONFIG['models'].get(entity_name)
        if not model_path:
            self.stdout.write(self.style.WARNING(f'- Unknown entity: {entity_name}'))
            return {'total': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': 0}

        app_label, model_name = model_path.split('.')
        Model = apps.get_model(app_label, model_name)

        exclude_fields = SYNC_CONFIG['exclude_fields'].get(entity_name, [])
        strategy = SYNC_CONFIG['conflict_strategy'].get(entity_name, 'skip')

        self.stdout.write(f'\nSyncing {entity_name} ({Model.__name__})...')

        # Read from source
        source_db = 'default' if source == 'local' else 'production'
        target_db = 'default' if target == 'local' else 'production'

        records = list(Model.objects.using(source_db).all())
        
        if not records:
            self.stdout.write(self.style.WARNING(f'  No records found in source'))
            return {'total': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'errors': 0}

        stats = {'total': len(records), 'created': 0, 'updated': 0, 'skipped': 0, 'errors': 0}

        for record in records:
            try:
                result = self._sync_record(
                    Model, 
                    record, 
                    target_db, 
                    exclude_fields, 
                    strategy, 
                    dry_run, 
                    verbose
                )
                stats[result] += 1
            except Exception as e:
                stats['errors'] += 1
                if verbose:
                    self.stdout.write(
                        self.style.ERROR(f'  ✗ Error: {record} - {e}')
                    )

        # Summary for this entity
        self.stdout.write(
            f'  Total: {stats["total"]} | '
            f'Created: {stats["created"]} | '
            f'Updated: {stats["updated"]} | '
            f'Skipped: {stats["skipped"]} | '
            f'Errors: {stats["errors"]}'
        )

        return stats

    def _sync_record(self, Model, source_record, target_db, exclude_fields, strategy, dry_run, verbose):
        """Sync a single record to target database"""
        # Get field data
        data = {}
        for field in Model._meta.get_fields():
            if field.name in exclude_fields:
                continue
            if field.many_to_many or field.one_to_many:
                continue  # Skip relation fields
            if hasattr(source_record, field.name):
                value = getattr(source_record, field.name)
                # Convert non-JSON-serializable types
                if isinstance(value, (datetime, date)):
                    value = value.isoformat() if value else None
                elif isinstance(value, Decimal):
                    value = float(value)
                data[field.name] = value

        # Check if record exists in target
        pk_field = Model._meta.pk.name
        pk_value = data.get(pk_field)

        try:
            existing = Model.objects.using(target_db).get(**{pk_field: pk_value})
            exists = True
        except Model.DoesNotExist:
            existing = None
            exists = False

        # Apply conflict resolution strategy
        if exists:
            if strategy == 'skip':
                if verbose:
                    self.stdout.write(f'  - Skipped: {source_record} (already exists)')
                return 'skipped'
            elif strategy == 'update':
                if not dry_run:
                    for key, value in data.items():
                        if key not in exclude_fields:
                            setattr(existing, key, value)
                    existing.save(using=target_db)
                if verbose:
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Updated: {source_record}'))
                return 'updated'
        else:
            if not dry_run:
                # Create new record
                new_record = Model(**data)
                new_record.save(using=target_db)
            if verbose:
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created: {source_record}'))
            return 'created'

        return 'skipped'
