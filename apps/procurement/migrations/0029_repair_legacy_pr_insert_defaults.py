"""Make legacy PR columns safe for inserts from the current model state.

Some deployed databases retain columns from an older requisition workflow even
though those fields no longer exist in Django's model state. PostgreSQL still
requires values for the NOT NULL columns, so every ORM insert fails unless the
database supplies a default. This migration is intentionally idempotent and
only adds defaults to legacy columns that are physically present.
"""

from django.db import migrations


LEGACY_DEFAULTS = {
    'escalation_notes': "''",
    'escalation_resolution_notes': "''",
    'escalation_resolved': 'FALSE',
    'rejection_escalated_to_moe': 'FALSE',
    'rejection_escalated_to_mop': 'FALSE',
    'current_approval_level': '0',
    'po_completion_verified': 'FALSE',
    'po_manually_entered': "''",
}


def set_legacy_defaults(apps, schema_editor):
    Requisition = apps.get_model('procurement', 'PurchaseRequisition')
    table_name = Requisition._meta.db_table
    quoted_table = schema_editor.quote_name(table_name)

    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(
            cursor, table_name
        )
        existing_columns = {column.name for column in description}
        for column_name, default_sql in LEGACY_DEFAULTS.items():
            if column_name not in existing_columns:
                continue
            quoted_column = schema_editor.quote_name(column_name)
            cursor.execute(
                f'ALTER TABLE {quoted_table} ALTER COLUMN {quoted_column} SET DEFAULT {default_sql}'
            )


def unset_legacy_defaults(apps, schema_editor):
    Requisition = apps.get_model('procurement', 'PurchaseRequisition')
    table_name = Requisition._meta.db_table
    quoted_table = schema_editor.quote_name(table_name)

    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(
            cursor, table_name
        )
        existing_columns = {column.name for column in description}
        for column_name in LEGACY_DEFAULTS:
            if column_name not in existing_columns:
                continue
            quoted_column = schema_editor.quote_name(column_name)
            cursor.execute(
                f'ALTER TABLE {quoted_table} ALTER COLUMN {quoted_column} DROP DEFAULT'
            )


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0028_repair_receipt_legacy_constraints'),
    ]

    operations = [
        migrations.RunPython(set_legacy_defaults, unset_legacy_defaults),
    ]
