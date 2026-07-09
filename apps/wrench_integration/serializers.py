"""
Wrench Integration – Serializers
"""
from rest_framework import serializers
from .models import WrenchConfig, WrenchSyncLog
from .crypto import encrypt_value


class WrenchConfigReadSerializer(serializers.ModelSerializer):
    """Read-safe serializer – never exposes the decrypted password or session token."""
    created_by_email = serializers.SerializerMethodField()

    class Meta:
        model = WrenchConfig
        fields = [
            'id', 'base_url', 'svc_url',
            'server_id', 'login_name',
            'is_password_encrypted', 'otp',
            'language', 'time_zone_id', 'workstation_name',
            'organization_name',
            'is_active', 'connection_verified', 'last_verified_at',
            'created_by_email', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_created_by_email(self, obj):
        return obj.created_by.email if obj.created_by else None


class WrenchConfigWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer – accepts plain-text password, encrypts before saving.
    The password field is write-only so it never appears in responses.
    """
    password = serializers.CharField(
        write_only=True, required=True, min_length=1,
        help_text='Wrench account password – encrypted before storage',
    )

    class Meta:
        model = WrenchConfig
        fields = [
            'base_url', 'svc_url',
            'server_id', 'login_name', 'password',
            'is_password_encrypted', 'otp',
            'language', 'time_zone_id', 'workstation_name',
            'organization_name', 'is_active',
        ]

    def validate_base_url(self, value: str) -> str:
        if not value.startswith('https://'):
            raise serializers.ValidationError(
                'Base URL must use HTTPS to protect credentials in transit.'
            )
        return value.rstrip('/')

    def validate_svc_url(self, value: str) -> str:
        if value and not value.startswith('https://'):
            raise serializers.ValidationError(
                'Service URL must use HTTPS to protect credentials in transit.'
            )
        if not value:
            return value
        # Soft-coded normalisation: admins commonly paste the AtomSVC.svc /
        # OData metadata URL (e.g. ".../SVC/AtomSVC.svc/") instead of the JSON
        # SVC base. Normalise here so /DocumentSearch/SearchObject resolves
        # correctly without changing any search-core logic.
        from .service import _normalise_svc_url  # local import to avoid cycle
        return _normalise_svc_url(value)

    def create(self, validated_data):
        password_plain = validated_data.pop('password')
        validated_data['encrypted_password'] = encrypt_value(password_plain)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        password_plain = validated_data.pop('password', None)
        if password_plain:
            validated_data['encrypted_password'] = encrypt_value(password_plain)
        return super().update(instance, validated_data)


class WrenchSyncLogSerializer(serializers.ModelSerializer):
    triggered_by_email = serializers.SerializerMethodField()
    duration_seconds = serializers.FloatField(read_only=True)

    class Meta:
        model = WrenchSyncLog
        fields = [
            'id', 'direction', 'entity_type', 'status',
            'records_requested', 'records_synced', 'records_failed',
            'error_message', 'sync_details',
            'triggered_by_email', 'started_at', 'completed_at', 'duration_seconds',
        ]
        read_only_fields = fields

    def get_triggered_by_email(self, obj):
        return obj.triggered_by.email if obj.triggered_by else None


class WrenchS3SyncJobSerializer(serializers.ModelSerializer):
    triggered_by_email = serializers.SerializerMethodField()
    duration_seconds   = serializers.FloatField(read_only=True)

    class Meta:
        from .models import WrenchS3SyncJob
        model = WrenchS3SyncJob
        fields = [
            'id', 'mode', 'entity_type', 'status',
            's3_prefix',
            'records_exported', 'records_failed', 'pages_processed', 'last_page_exported',
            'error_message', 'job_details', 'celery_task_id',
            'triggered_by_email', 'started_at', 'completed_at', 'updated_at', 'duration_seconds',
        ]
        read_only_fields = fields

    def get_triggered_by_email(self, obj):
        return obj.triggered_by.email if obj.triggered_by else None

