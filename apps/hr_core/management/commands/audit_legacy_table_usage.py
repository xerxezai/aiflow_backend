"""
Django management command to audit legacy table usage

Scans codebase for references to old tables and suggests replacements.

Usage:
    python manage.py audit_legacy_table_usage --table user_profiles
    python manage.py audit_legacy_table_usage --table finance_employee_salary_info
    python manage.py audit_legacy_table_usage --table onboarding_record
    python manage.py audit_legacy_table_usage --all
    python manage.py audit_legacy_table_usage --all --fix
"""

import os
import re
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Audit codebase for legacy table usage and suggest replacements'

    def add_arguments(self, parser):
        parser.add_argument(
            '--table',
            type=str,
            choices=['user_profiles', 'finance_employee_salary_info', 'onboarding_record'],
            help='Specific table to audit',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Audit all legacy tables',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Generate migration suggestions (does not modify files)',
        )
        parser.add_argument(
            '--export',
            type=str,
            help='Export results to file (e.g., audit_report.md)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('  LEGACY TABLE USAGE AUDIT'))
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))

        tables_to_audit = []
        if options['all']:
            tables_to_audit = ['user_profiles', 'finance_employee_salary_info', 'onboarding_record']
        elif options['table']:
            tables_to_audit = [options['table']]
        else:
            self.stdout.write(self.style.ERROR('❌ Error: Specify --table <name> or --all'))
            return

        # Scan results storage
        all_results = []

        for table in tables_to_audit:
            self.stdout.write(self.style.WARNING(f'\n📊 Auditing: {table}'))
            self.stdout.write('-' * 80)
            
            results = self.audit_table(table)
            all_results.append({
                'table': table,
                'results': results
            })

            self.print_results(table, results, options.get('fix', False))

        # Export to file if requested
        if options.get('export'):
            self.export_results(all_results, options['export'])

        # Summary
        total_files = sum(len(r['results']['files']) for r in all_results)
        total_references = sum(r['results']['total_matches'] for r in all_results)
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS(f'  SUMMARY'))
        self.stdout.write(self.style.SUCCESS('='*80))
        self.stdout.write(f'\n  Tables Audited: {len(tables_to_audit)}')
        self.stdout.write(f'  Files with References: {total_files}')
        self.stdout.write(f'  Total References: {total_references}\n')

        if options.get('fix'):
            self.stdout.write(self.style.WARNING('\n💡 Migration suggestions generated. Review carefully before applying.\n'))

    def audit_table(self, table_name):
        """Scan codebase for references to a specific table"""
        
        # Define search patterns based on table
        patterns = self.get_search_patterns(table_name)
        
        # Directories to scan
        base_dir = settings.BASE_DIR
        scan_dirs = [
            os.path.join(base_dir, 'apps'),
            os.path.join(base_dir, 'config'),
        ]

        results = {
            'total_matches': 0,
            'files': {},
            'patterns': patterns
        }

        for scan_dir in scan_dirs:
            if not os.path.exists(scan_dir):
                continue
                
            for root, dirs, files in os.walk(scan_dir):
                # Skip migrations and __pycache__
                if '__pycache__' in root or 'migrations' in root:
                    continue
                    
                for file in files:
                    if file.endswith('.py'):
                        file_path = os.path.join(root, file)
                        matches = self.scan_file(file_path, patterns)
                        
                        if matches:
                            relative_path = os.path.relpath(file_path, base_dir)
                            results['files'][relative_path] = matches
                            results['total_matches'] += len(matches)

        return results

    def get_search_patterns(self, table_name):
        """Get regex patterns to search for based on table name"""
        
        patterns = {}
        
        if table_name == 'user_profiles':
            patterns = {
                'model_import': re.compile(r'from\s+(?:apps\.)?(?:users|rbac)\.models\s+import\s+.*UserProfile'),
                'class_reference': re.compile(r'\bUserProfile\b(?!_)'),
                'table_name': re.compile(r'[\'"]user_profiles[\'"]'),
                'db_table': re.compile(r'db_table\s*=\s*[\'"]user_profiles[\'"]'),
            }
        elif table_name == 'finance_employee_salary_info':
            patterns = {
                'model_import': re.compile(r'from\s+apps\.finance\.models\s+import\s+.*EmployeeSalaryInfo'),
                'class_reference': re.compile(r'\bEmployeeSalaryInfo\b'),
                'table_name': re.compile(r'[\'"]finance_employee_salary_info[\'"]'),
                'fk_reference': re.compile(r'employee_salary_info'),
            }
        elif table_name == 'onboarding_record':
            patterns = {
                'model_import': re.compile(r'from\s+apps\.onboarding\.models\s+import\s+.*OnboardingRecord'),
                'class_reference': re.compile(r'\bOnboardingRecord\b'),
                'table_name': re.compile(r'[\'"]onboarding_record[\'"]'),
                'fk_reference': re.compile(r'onboarding_record_id'),
            }
        
        return patterns

    def scan_file(self, file_path, patterns):
        """Scan a single file for pattern matches"""
        matches = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            for line_num, line in enumerate(lines, 1):
                for pattern_name, pattern in patterns.items():
                    if pattern.search(line):
                        matches.append({
                            'line_num': line_num,
                            'line': line.strip(),
                            'pattern': pattern_name,
                            'suggestion': self.get_suggestion(pattern_name, line.strip())
                        })
        except Exception as e:
            # Skip files that can't be read
            pass
            
        return matches

    def get_suggestion(self, pattern_name, line):
        """Generate replacement suggestion based on pattern"""
        
        suggestions = {
            'model_import': 'from apps.hr_core.models import EmployeeMaster',
            'class_reference': 'Replace UserProfile with EmployeeMaster',
            'table_name': 'Use hr_employee_master table',
            'db_table': "db_table = 'hr_employee_master'",
            'fk_reference': "Use ForeignKey('hr_core.EmployeeMaster')",
        }
        
        return suggestions.get(pattern_name, 'Review and update to use EmployeeMaster')

    def print_results(self, table_name, results, show_suggestions):
        """Print audit results to console"""
        
        if not results['files']:
            self.stdout.write(self.style.SUCCESS(f'\n  ✅ No references found to {table_name}\n'))
            return

        self.stdout.write(f'\n  Found {results["total_matches"]} references in {len(results["files"])} files:\n')

        for file_path, matches in sorted(results['files'].items()):
            self.stdout.write(f'\n  📄 {file_path}')
            self.stdout.write(f'     {len(matches)} reference(s)')
            
            if show_suggestions:
                for match in matches[:5]:  # Show max 5 matches per file
                    self.stdout.write(f'\n     Line {match["line_num"]}: {match["pattern"]}')
                    self.stdout.write(self.style.WARNING(f'       Current: {match["line"][:70]}'))
                    self.stdout.write(self.style.SUCCESS(f'       Suggest: {match["suggestion"]}'))
                    
                if len(matches) > 5:
                    self.stdout.write(f'\n     ... and {len(matches) - 5} more')

    def export_results(self, all_results, export_path):
        """Export audit results to markdown file"""
        
        base_dir = settings.BASE_DIR
        output_path = os.path.join(base_dir, export_path)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('# Legacy Table Usage Audit Report\n\n')
            f.write(f'**Generated**: {self.get_timestamp()}\n\n')
            f.write('---\n\n')
            
            for result in all_results:
                table = result['table']
                data = result['results']
                
                f.write(f'## {table}\n\n')
                f.write(f'- **Total References**: {data["total_matches"]}\n')
                f.write(f'- **Files Affected**: {len(data["files"])}\n\n')
                
                if data['files']:
                    f.write('### Files with References:\n\n')
                    
                    for file_path, matches in sorted(data['files'].items()):
                        f.write(f'#### `{file_path}`\n\n')
                        f.write(f'**{len(matches)} reference(s)**\n\n')
                        
                        for match in matches[:10]:  # Max 10 per file in report
                            f.write(f'- **Line {match["line_num"]}** ({match["pattern"]})\n')
                            f.write(f'  ```python\n')
                            f.write(f'  {match["line"]}\n')
                            f.write(f'  ```\n')
                            f.write(f'  💡 *Suggestion: {match["suggestion"]}*\n\n')
                        
                        if len(matches) > 10:
                            f.write(f'  ... and {len(matches) - 10} more\n\n')
                
                f.write('---\n\n')
            
            # Summary
            total_files = sum(len(r['results']['files']) for r in all_results)
            total_refs = sum(r['results']['total_matches'] for r in all_results)
            
            f.write('## Summary\n\n')
            f.write(f'- **Tables Audited**: {len(all_results)}\n')
            f.write(f'- **Total Files**: {total_files}\n')
            f.write(f'- **Total References**: {total_refs}\n\n')
            f.write('---\n\n')
            f.write('**Next Steps**:\n')
            f.write('1. Review each reference carefully\n')
            f.write('2. Update imports to use `apps.hr_core.models.EmployeeMaster`\n')
            f.write('3. Replace model references with `EmployeeMaster`\n')
            f.write('4. Update ForeignKey fields to point to `hr_core.EmployeeMaster`\n')
            f.write('5. Test thoroughly after each change\n')
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ Report exported to: {output_path}\n'))

    def get_timestamp(self):
        """Get current timestamp"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
