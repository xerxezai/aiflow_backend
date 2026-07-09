# RAD AI - Quick User Sync (Production → Local)
# Using Django's dumpdata/loaddata for efficiency
#
# Usage:
#   .\sync_users_quick.ps1          # Dry run (preview)
#   .\sync_users_quick.ps1 -Apply   # Actually sync

param(
    [switch]$Apply = $false
)

$DryRun = -not $Apply

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "RAD AI - Quick User Sync (Production → Local)" -ForegroundColor Cyan
Write-Host "Using Django dumpdata/loaddata for efficiency" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

if ($DryRun) {
    Write-Host "Mode: DRY RUN (preview only)" -ForegroundColor Yellow
} else {
    Write-Host "Mode: LIVE (will import users)" -ForegroundColor Green
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Export from production
Write-Host "📦 Step 1: Exporting users from production database..." -ForegroundColor Cyan
docker exec aiflow_backend_local python manage.py dumpdata `
    users.User users.UserProfile rbac.UserProfile `
    --database=production `
    --output=/tmp/production_users.json `
    --indent=2

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Error exporting users from production" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Exported to /tmp/production_users.json" -ForegroundColor Green
Write-Host ""

# Step 2: Show preview
Write-Host "📊 Step 2: Preview of what will be synced..." -ForegroundColor Cyan
$UserCount = docker exec aiflow_backend_local python -c @"
import json
with open('/tmp/production_users.json') as f:
    data = json.load(f)
    users = [obj for obj in data if obj['model'] == 'users.user']
    print(len(users))
"@

Write-Host "  Users found: $UserCount" -ForegroundColor White
Write-Host ""

# Step 3: Import to local (if not dry run)
if (-not $DryRun) {
    Write-Host "📥 Step 3: Importing users to local database..." -ForegroundColor Cyan
    
    $Confirm = Read-Host "⚠️  This will update local users. Continue? (yes/no)"
    if ($Confirm -ne "yes") {
        Write-Host ""
        Write-Host "❌ Cancelled by user" -ForegroundColor Red
        Write-Host ""
        exit 0
    }
    
    docker exec aiflow_backend_local python manage.py loaddata `
        /tmp/production_users.json `
        --database=default
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "✅ Users synced successfully!" -ForegroundColor Green
        Write-Host "   Refresh http://localhost:5173/admin/users to see imported users" -ForegroundColor White
        Write-Host ""
    } else {
        Write-Host ""
        Write-Host "❌ Error importing users" -ForegroundColor Red
        Write-Host ""
        exit 1
    }
} else {
    Write-Host "💡 To actually import users, run:" -ForegroundColor Yellow
    Write-Host "   .\sync_users_quick.ps1 -Apply" -ForegroundColor White
    Write-Host ""
}
