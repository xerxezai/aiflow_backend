# Generated migration — adds contract_value, currency and scope_type to Project.
# These fields support the Project Dashboard tab in the Project Management module.
# All three fields are nullable / have defaults so existing rows are unaffected.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        # Awarded contract value — separate from the internal budget estimate.
        migrations.AddField(
            model_name='project',
            name='contract_value',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Awarded contract value (commercial, separate from internal budget estimate).',
                max_digits=14,
                null=True,
            ),
        ),
        # ISO 4217 currency code for the contract value.
        # Default 'AED' is backward-compatible for existing projects in UAE offices.
        migrations.AddField(
            model_name='project',
            name='currency',
            field=models.CharField(
                choices=[
                    ('AED', 'AED \u2014 UAE Dirham'),
                    ('USD', 'USD \u2014 US Dollar'),
                    ('EUR', 'EUR \u2014 Euro'),
                    ('GBP', 'GBP \u2014 British Pound'),
                    ('SAR', 'SAR \u2014 Saudi Riyal'),
                    ('QAR', 'QAR \u2014 Qatari Riyal'),
                    ('KWD', 'KWD \u2014 Kuwaiti Dinar'),
                    ('BHD', 'BHD \u2014 Bahraini Dinar'),
                    ('OMR', 'OMR \u2014 Omani Rial'),
                ],
                default='AED',
                help_text='ISO 4217 currency code for the contract value.',
                max_length=8,
            ),
        ),
        # Engineering scope / engagement type (FEED, Detailed Engineering, EPC, …).
        migrations.AddField(
            model_name='project',
            name='scope_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('conceptual',           'Conceptual Study'),
                    ('pre_feed',             'Pre-FEED'),
                    ('feed',                 'FEED'),
                    ('basic_engineering',    'Basic Engineering'),
                    ('detailed_engineering', 'Detailed Engineering'),
                    ('epcm',                 'EPCM'),
                    ('epc',                  'EPC (Lump Sum)'),
                    ('pmc',                  'PMC (Project Management Consultancy)'),
                    ('owner_engineer',       "Owner's Engineer"),
                    ('procurement',          'Procurement Only'),
                    ('construction',         'Construction Management'),
                    ('commissioning',        'Commissioning & Start-Up'),
                    ('feasibility',          'Feasibility Study'),
                    ('other',                'Other / Mixed Scope'),
                ],
                default='',
                help_text='Engineering scope / engagement type (FEED, Detailed Engineering, EPC, \u2026).',
                max_length=30,
            ),
        ),
    ]
