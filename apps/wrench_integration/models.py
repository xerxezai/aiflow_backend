"""
Wrench Integration Models
Stores encrypted credentials and sync records for the Wrench Project Platform.
"""
import secrets
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

User = get_user_model()


class WrenchConfig(models.Model):
    """
    Stores the Wrench SmartProject platform connection configuration.
    Auth flow: POST /api/AccessControl/Login → receive TOKEN → supply TOKEN on every request.
    The session token rolls – each API response returns a refreshed token.
    Credentials are Fernet-encrypted at rest.
    """
    # --- Connection ---
    base_url = models.URLField(
        max_length=500,
        help_text='Wrench WebAPI Server URL, e.g. https://your-org.wrenchproject.com'
    )
    svc_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            'Wrench DocumentSearch Service URL (<<SVC URL>>). '
            'Leave blank to use the same base URL. '
            'e.g. https://svc.wrenchproject.com'
        )
    )
    # Wrench uses SERVER_ID (integer) to identify the server on login
    server_id = models.IntegerField(
        default=1,
        help_text='Wrench SERVER_ID – passed to /api/AccessControl/Login'
    )
    # --- Auth credentials (both encrypted at rest) ---
    login_name = models.CharField(
        max_length=255, default='',
        help_text='Wrench LOGIN_NAME (username)'
    )
    encrypted_password = models.TextField(
        default='',
        help_text='Fernet-encrypted Wrench password – never stored in plaintext'
    )
    # Legacy field kept for potential API-key auth mode (future)
    encrypted_api_key = models.TextField(blank=True, default='')

    # Rolling session token – updated after every successful API call
    session_token = models.TextField(
        blank=True, default='',
        help_text='Current Wrench session TOKEN (refreshed on each API call)'
    )
    token_obtained_at = models.DateTimeField(null=True, blank=True)

    # Pre-shared token – when set, used directly for every API call (no login required).
    # Wrench's rolling-token mechanism still applies: each response token overwrites this field.
    # Set via the "Inject Token" action; cleared by saving new credentials.
    #
    # SECURITY: Stored as a Fernet-encrypted ciphertext (same key derivation as
    # `encrypted_password`). Always read/write via `get_pre_shared_token()` and
    # `set_pre_shared_token()` — never access `.pre_shared_token` directly.
    pre_shared_token = models.TextField(
        blank=True, default='',
        help_text=(
            'Fernet-encrypted Wrench session token (pre-shared from the Wrench team). '
            'When non-empty, this token is used directly — bypassing the username/password '
            'login flow. The rolling refresh from each API response keeps it current. '
            'Never stored in plaintext.'
        )
    )

    # ─── Pre-shared token: secure access helpers ──────────────────────────────
    # All credential I/O goes through these methods so the raw ciphertext never
    # leaks into application code, logs, or serialiser output. The getter is
    # backward-compatible: legacy plaintext values are auto-detected, transparently
    # re-encrypted on first access, and persisted — so existing rows upgrade in place.
    def get_pre_shared_token(self) -> str:
        """Return the decrypted pre-shared token (empty string when unset).

        Self-heals legacy plaintext rows: if the stored value isn't valid Fernet
        ciphertext, treat it as plaintext, re-encrypt, and persist.
        """
        from .crypto import decrypt_value, encrypt_value  # local import → no cycle
        raw = self.pre_shared_token or ''
        if not raw:
            return ''
        try:
            return decrypt_value(raw)
        except Exception:
            # Legacy plaintext — upgrade to ciphertext on the fly.
            try:
                self.pre_shared_token = encrypt_value(raw)
                # Avoid touching `updated_at` and other fields
                type(self).objects.filter(pk=self.pk).update(
                    pre_shared_token=self.pre_shared_token
                )
            except Exception:
                # If self-heal fails (e.g. unsaved instance), still surface the
                # plaintext to keep the integration working.
                pass
            return raw

    def set_pre_shared_token(self, plaintext: str) -> None:
        """Encrypt and store the pre-shared token. Empty input clears the field."""
        from .crypto import encrypt_value  # local import → no cycle
        self.pre_shared_token = encrypt_value(plaintext) if plaintext else ''

    def has_pre_shared_token(self) -> bool:
        """Cheap check that avoids decrypting just to test for presence."""
        return bool(self.pre_shared_token)

    organization_name = models.CharField(max_length=255, blank=True, default='')
    client_id = models.CharField(max_length=255, blank=True, default='')

    # --- Optional login parameters (from SmartProject API spec) ---
    # IS_PASSWORD_ENCRYPTED: 0 = plain-text password, 1 = pre-encrypted (default 0)
    is_password_encrypted = models.SmallIntegerField(
        default=0,
        choices=[(0, 'Plain-text (0)'), (1, 'Pre-encrypted (1)')],
        help_text='Wrench IS_PASSWORD_ENCRYPTED flag: 0=plain, 1=pre-encrypted'
    )
    # OTP: one-time password (leave blank unless MFA is enforced on the Wrench server)
    otp = models.CharField(
        max_length=64, blank=True, default='',
        help_text='One-time password for MFA (leave blank if not required)'
    )
    # Optional session parameters
    language = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Wrench LANGUAGE code, e.g. en-US (leave blank for server default)'
    )
    time_zone_id = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Wrench TIME_ZONE_ID (leave blank for server default)'
    )
    workstation_name = models.CharField(
        max_length=255, blank=True, default='RADAI',
        help_text='WORKSTATION_NAME sent on login to identify this client'
    )

    # --- Status ---
    is_active = models.BooleanField(default=True)
    connection_verified = models.BooleanField(default=False)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    # Audit
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='wrench_configs_created'
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='wrench_configs_updated'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wrench Configuration'
        verbose_name_plural = 'Wrench Configurations'
        ordering = ['-created_at']

    def __str__(self):
        return f'Wrench Config – {self.organization_name or self.base_url}'


class WrenchSyncLog(models.Model):
    """
    Records every sync attempt between RADAI and Wrench.
    """
    DIRECTION_CHOICES = [
        ('radai_to_wrench', 'RADAI → Wrench'),
        ('wrench_to_radai', 'Wrench → RADAI'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('partial', 'Partial'),
    ]
    ENTITY_CHOICES = [
        ('project', 'Project'),
        ('document', 'Document'),
        ('transmittal', 'Transmittal'),
        ('user', 'User'),
        ('all', 'All'),
        ('doc_search', 'Document Search'),
    ]

    config = models.ForeignKey(
        WrenchConfig, on_delete=models.SET_NULL, null=True,
        related_name='sync_logs'
    )
    triggered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='wrench_sync_logs'
    )
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    entity_type = models.CharField(max_length=30, choices=ENTITY_CHOICES, default='all')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    records_requested = models.IntegerField(default=0)
    records_synced = models.IntegerField(default=0)
    records_failed = models.IntegerField(default=0)

    error_message = models.TextField(blank=True, default='')
    sync_details = models.JSONField(default=dict, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Wrench Sync Log'
        verbose_name_plural = 'Wrench Sync Logs'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['started_at']),
        ]

    def __str__(self):
        return f'Sync {self.direction} [{self.status}] @ {self.started_at:%Y-%m-%d %H:%M}'

    @property
    def duration_seconds(self):
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class WrenchS3SyncJob(models.Model):
    """
    Tracks a Wrench → RADAI → S3 export job.
    Supports both batch (one-off full export) and real-time (polling loop) modes.
    """
    MODE_BATCH = 'batch'
    MODE_REALTIME = 'realtime'
    MODE_CHOICES = [
        (MODE_BATCH,    'Batch – full export, run once'),
        (MODE_REALTIME, 'Real-time – continuous polling'),
    ]

    ENTITY_TRANSMITTALS = 'transmittals'
    ENTITY_DOCUMENTS    = 'documents'
    ENTITY_ALL          = 'all'
    ENTITY_CHOICES = [
        (ENTITY_TRANSMITTALS, 'Transmittals'),
        (ENTITY_DOCUMENTS,    'Documents'),
        (ENTITY_ALL,          'All (Transmittals + Documents)'),
    ]

    STATUS_PENDING     = 'pending'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_SUCCESS     = 'success'
    STATUS_FAILED      = 'failed'
    STATUS_STOPPED     = 'stopped'
    STATUS_CHOICES = [
        (STATUS_PENDING,     'Pending'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_SUCCESS,     'Success'),
        (STATUS_FAILED,      'Failed'),
        (STATUS_STOPPED,     'Stopped'),
    ]

    config = models.ForeignKey(
        WrenchConfig, on_delete=models.SET_NULL, null=True,
        related_name='s3_sync_jobs',
    )
    triggered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='wrench_s3_sync_jobs',
    )

    mode        = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_BATCH)
    entity_type = models.CharField(max_length=30, choices=ENTITY_CHOICES, default=ENTITY_TRANSMITTALS)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    # S3 target (all soft-coded; bucket sourced from env at runtime — not stored)
    s3_prefix   = models.CharField(
        max_length=500, blank=True, default='wrench/',
        help_text='S3 key prefix, e.g. "wrench/transmittals/"',
    )

    # Progress counters
    records_exported = models.IntegerField(default=0)
    records_failed   = models.IntegerField(default=0)
    pages_processed  = models.IntegerField(default=0)

    # Real-time state: last Wrench page successfully exported
    last_page_exported = models.IntegerField(default=0)

    error_message = models.TextField(blank=True, default='')
    job_details   = models.JSONField(default=dict, blank=True)

    # Celery task ID so we can revoke a real-time job
    celery_task_id = models.CharField(max_length=255, blank=True, default='')

    started_at   = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wrench S3 Sync Job'
        verbose_name_plural = 'Wrench S3 Sync Jobs'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['mode']),
            models.Index(fields=['started_at']),
        ]

    def __str__(self):
        return f'S3 Sync [{self.mode}/{self.entity_type}] [{self.status}] @ {self.started_at:%Y-%m-%d %H:%M}'

    @property
    def duration_seconds(self):
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
