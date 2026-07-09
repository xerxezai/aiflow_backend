"""
Seed AIPricingConfig with current vendor pricing so the AI Champion cost
dashboard works out of the box.

All values are SOFT-CODED here and overridable via Django admin (rows are only
created if no active row exists for the (provider, model_name) pair).

Source: vendor public pricing as of 2026-Q1. Update via Django admin or a
follow-up data migration when vendor pricing changes.
"""
from decimal import Decimal
from django.db import migrations
from django.utils import timezone


# (provider, model_name, input_per_1k, output_per_1k)
SEED_PRICING = [
    ('openai',    'gpt-4o',                    Decimal('0.0025'),  Decimal('0.0100')),
    ('openai',    'gpt-4o-mini',               Decimal('0.00015'), Decimal('0.00060')),
    ('openai',    'gpt-4-turbo',               Decimal('0.0100'),  Decimal('0.0300')),
    ('openai',    'gpt-4',                     Decimal('0.0300'),  Decimal('0.0600')),
    ('openai',    'gpt-4-vision-preview',      Decimal('0.0100'),  Decimal('0.0300')),
    ('openai',    'gpt-3.5-turbo',             Decimal('0.0005'),  Decimal('0.0015')),
    ('openai',    'text-embedding-3-large',    Decimal('0.00013'), Decimal('0')),
    ('openai',    'text-embedding-3-small',    Decimal('0.00002'), Decimal('0')),
    ('anthropic', 'claude-3-5-sonnet',         Decimal('0.0030'),  Decimal('0.0150')),
    ('anthropic', 'claude-3-opus',             Decimal('0.0150'),  Decimal('0.0750')),
    ('anthropic', 'claude-3-haiku',            Decimal('0.00025'), Decimal('0.00125')),
    ('google',    'gemini-1.5-pro',            Decimal('0.0035'),  Decimal('0.0105')),
    ('google',    'gemini-1.5-flash',          Decimal('0.00035'), Decimal('0.00105')),
    ('google',    'gemini-2.0-flash',          Decimal('0.00010'), Decimal('0.00040')),
]


def seed_pricing(apps, schema_editor):
    AIPricingConfig = apps.get_model('rbac', 'AIPricingConfig')
    now = timezone.now()
    for provider, model_name, in_cost, out_cost in SEED_PRICING:
        exists = AIPricingConfig.objects.filter(
            provider=provider, model_name=model_name, is_active=True
        ).exists()
        if exists:
            continue
        AIPricingConfig.objects.create(
            provider=provider,
            model_name=model_name,
            input_cost_per_1k=in_cost,
            output_cost_per_1k=out_cost,
            currency='USD',
            is_active=True,
            effective_from=now,
            notes='Seeded by migration 0014_seed_ai_pricing — edit in Django admin to override.',
        )


def unseed_pricing(apps, schema_editor):
    AIPricingConfig = apps.get_model('rbac', 'AIPricingConfig')
    AIPricingConfig.objects.filter(
        notes__startswith='Seeded by migration 0014_seed_ai_pricing'
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0013_ai_champion'),
    ]

    operations = [
        migrations.RunPython(seed_pricing, unseed_pricing),
    ]
