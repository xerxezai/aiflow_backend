#!/usr/bin/env bash
#
# Quick User Sync - Using Django's dumpdata/loaddata (More Efficient)
# This method is faster for large datasets
#
# Usage:
#   ./sync_users_quick.sh          # Dry run (preview)
#   ./sync_users_quick.sh --apply  # Actually sync

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0.31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo ""
echo "======================================================================"
echo "RAD AI - Quick User Sync (Production → Local)"
echo "Using Django dumpdata/loaddata for efficiency"
echo "======================================================================"

# Check if --apply flag is set
DRY_RUN=true
if [[ "$*" == *"--apply"* ]]; then
    DRY_RUN=false
    echo "Mode: LIVE (will import users)"
else
    echo "Mode: DRY RUN (preview only)"
fi
echo "======================================================================"
echo ""

# Step 1: Export from production
echo "📦 Step 1: Exporting users from production database..."
docker exec aiflow_backend_local python manage.py dumpdata \
    users.User users.UserProfile rbac.UserProfile \
    --database=production \
    --output=/tmp/production_users.json \
    --indent=2

echo "✓ Exported to /tmp/production_users.json"
echo ""

# Step 2: Show preview
echo "📊 Step 2: Preview of what will be synced..."
USER_COUNT=$(docker exec aiflow_backend_local python -c "
import json
with open('/tmp/production_users.json') as f:
    data = json.load(f)
    users = [obj for obj in data if obj['model'] == 'users.user']
    print(len(users))
")

echo "  Users found: $USER_COUNT"
echo ""

# Step 3: Import to local (if not dry run)
if [ "$DRY_RUN" = false ]; then
    echo "📥 Step 3: Importing users to local database..."
    
    read -p "⚠️  This will update local users. Continue? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo ""
        echo "❌ Cancelled by user"
        echo ""
        exit 0
    fi
    
    docker exec aiflow_backend_local python manage.py loaddata \
        /tmp/production_users.json \
        --database=default
    
    echo ""
    echo "✅ Users synced successfully!"
    echo "   Refresh http://localhost:5173/admin/users to see imported users"
    echo ""
else
    echo "💡 To actually import users, run:"
    echo "   ./sync_users_quick.sh --apply"
    echo ""
fi
