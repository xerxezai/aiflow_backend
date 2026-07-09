"""
Site Visit Tracking — Soft-Coded Configuration
===============================================
All thresholds, toggles, and settings loaded from environment variables.
Never hardcode magic numbers — use these config values in all modules.
"""
from decouple import config


# ─────────────────────────────────────────────────────────────────────────────
# Feature Toggles
# ─────────────────────────────────────────────────────────────────────────────
ENABLED = config('SITE_VISIT_ENABLED', default=True, cast=bool)

TRACKING_METHODS = config(
    'SITE_VISIT_TRACKING_METHODS',
    default='gps,geofence,qrcode'
).lower().split(',')


# ─────────────────────────────────────────────────────────────────────────────
# GPS Configuration
# ─────────────────────────────────────────────────────────────────────────────
GPS_ACCURACY_THRESHOLD = config('SITE_VISIT_GPS_ACCURACY_THRESHOLD', default=50, cast=int)
GPS_ACCURACY_STRICT = config('SITE_VISIT_GPS_ACCURACY_STRICT', default=20, cast=int)
GPS_TIMEOUT_SECONDS = config('SITE_VISIT_GPS_TIMEOUT_SECONDS', default=10, cast=int)


# ─────────────────────────────────────────────────────────────────────────────
# Geofencing
# ─────────────────────────────────────────────────────────────────────────────
GEOFENCE_RADIUS = config('SITE_VISIT_GEOFENCE_RADIUS', default=100, cast=int)
ALLOW_OUT_OF_GEOFENCE = config('SITE_VISIT_ALLOW_OUT_OF_GEOFENCE', default=True, cast=bool)
CHECKOUT_RADIUS_MULTIPLIER = config('SITE_VISIT_CHECKOUT_RADIUS_MULTIPLIER', default=2.0, cast=float)


# ─────────────────────────────────────────────────────────────────────────────
# Photo Verification
# ─────────────────────────────────────────────────────────────────────────────
REQUIRE_PHOTO = config('SITE_VISIT_REQUIRE_PHOTO', default=False, cast=bool)
PHOTO_STAGE = config('SITE_VISIT_PHOTO_STAGE', default='checkin')  # checkin|checkout|both
VALIDATE_PHOTO_EXIF = config('SITE_VISIT_VALIDATE_PHOTO_EXIF', default=True, cast=bool)
MAX_PHOTO_SIZE_MB = config('SITE_VISIT_MAX_PHOTO_SIZE_MB', default=5, cast=int)


# ─────────────────────────────────────────────────────────────────────────────
# Visit Duration
# ─────────────────────────────────────────────────────────────────────────────
MIN_DURATION_HOURS = config('SITE_VISIT_MIN_DURATION_HOURS', default=2, cast=int)
AUTO_CHECKOUT_HOURS = config('SITE_VISIT_AUTO_CHECKOUT_HOURS', default=12, cast=int)
LATE_CHECKIN_GRACE_MINUTES = config('SITE_VISIT_LATE_CHECKIN_GRACE_MINUTES', default=30, cast=int)


# ─────────────────────────────────────────────────────────────────────────────
# Approval Workflow
# ─────────────────────────────────────────────────────────────────────────────
REQUIRE_APPROVAL = config('SITE_VISIT_REQUIRE_APPROVAL', default=True, cast=bool)
APPROVAL_ROLES = config('SITE_VISIT_APPROVAL_ROLES', default='Manager,Admin,HR').split(',')
AUTO_APPROVE_ROLES = config('SITE_VISIT_AUTO_APPROVE_ROLES', default='Director,GM').split(',')
AUTO_APPROVE_DAYS = config('SITE_VISIT_AUTO_APPROVE_DAYS', default=1, cast=int)


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────
NOTIFY_ON_CHECKIN = config('SITE_VISIT_NOTIFY_ON_CHECKIN', default=True, cast=bool)
CHECKOUT_REMINDER_MINUTES = config('SITE_VISIT_CHECKOUT_REMINDER_MINUTES', default=15, cast=int)
MANAGER_DIGEST_ENABLED = config('SITE_VISIT_MANAGER_DIGEST_ENABLED', default=True, cast=bool)
MANAGER_DIGEST_TIME = config('SITE_VISIT_MANAGER_DIGEST_TIME', default='17:00')


# ─────────────────────────────────────────────────────────────────────────────
# Integration with Timesheet
# ─────────────────────────────────────────────────────────────────────────────
SYNC_TO_TIMESHEET = config('SITE_VISIT_SYNC_TO_TIMESHEET', default=True, cast=bool)
TIMESHEET_EVENT_TYPE = config('SITE_VISIT_TIMESHEET_EVENT_TYPE', default='SITE_VISIT')
SYNC_TO_DAILY_SUMMARY = config('SITE_VISIT_SYNC_TO_DAILY_SUMMARY', default=True, cast=bool)


# ─────────────────────────────────────────────────────────────────────────────
# Offline Support
# ─────────────────────────────────────────────────────────────────────────────
OFFLINE_ENABLED = config('SITE_VISIT_OFFLINE_ENABLED', default=True, cast=bool)
OFFLINE_MAX_AGE_HOURS = config('SITE_VISIT_OFFLINE_MAX_AGE_HOURS', default=24, cast=int)
OFFLINE_SYNC_INTERVAL = config('SITE_VISIT_OFFLINE_SYNC_INTERVAL', default=60, cast=int)


# ─────────────────────────────────────────────────────────────────────────────
# Security & Compliance
# ─────────────────────────────────────────────────────────────────────────────
ANOMALY_DETECTION = config('SITE_VISIT_ANOMALY_DETECTION', default=True, cast=bool)
MAX_LOCATION_JUMP_KM = config('SITE_VISIT_MAX_LOCATION_JUMP_KM', default=5, cast=int)
DEVICE_VERIFICATION = config('SITE_VISIT_DEVICE_VERIFICATION', default=False, cast=bool)
ALLOW_BACKDATE = config('SITE_VISIT_ALLOW_BACKDATE', default=True, cast=bool)
BACKDATE_ROLES = config('SITE_VISIT_BACKDATE_ROLES', default='Admin,HR').split(',')


# ─────────────────────────────────────────────────────────────────────────────
# UI Configuration
# ─────────────────────────────────────────────────────────────────────────────
MAP_DEFAULT_LAT = config('SITE_VISIT_MAP_DEFAULT_LAT', default=24.4539, cast=float)  # Abu Dhabi
MAP_DEFAULT_LON = config('SITE_VISIT_MAP_DEFAULT_LON', default=54.3773, cast=float)
MAP_DEFAULT_ZOOM = config('SITE_VISIT_MAP_DEFAULT_ZOOM', default=10, cast=int)
GOOGLE_MAPS_API_KEY = config('SITE_VISIT_GOOGLE_MAPS_API_KEY', default='')


# ─────────────────────────────────────────────────────────────────────────────
# Reporting & Analytics
# ─────────────────────────────────────────────────────────────────────────────
INCLUDE_IN_PAYROLL = config('SITE_VISIT_INCLUDE_IN_PAYROLL', default=True, cast=bool)
OVERTIME_THRESHOLD_HOURS = config('SITE_VISIT_OVERTIME_THRESHOLD_HOURS', default=8, cast=int)
INCLUDE_TRAVEL_TIME = config('SITE_VISIT_INCLUDE_TRAVEL_TIME', default=False, cast=bool)
