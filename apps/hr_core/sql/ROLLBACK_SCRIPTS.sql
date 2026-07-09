-- ================================================================
-- ROLLBACK SCRIPTS FOR GRADUAL TABLE MIGRATION
-- ================================================================
-- These scripts allow you to safely rollback each migration phase
-- if issues are encountered.
--
-- USAGE: Run the appropriate rollback script for the phase you want to undo
-- ================================================================

-- ========================================
-- PHASE 2 ROLLBACK: Drop Compatibility Views
-- ========================================
-- Use if: Compatibility views causing issues
-- Safe to run: YES - only affects views, not data
-- ========================================

-- ROLLBACK_PHASE2.sql
\echo 'Rolling back Phase 2: Dropping compatibility views...'

DROP VIEW IF EXISTS user_profiles_from_master CASCADE;
DROP VIEW IF EXISTS finance_employee_salary_info_from_master CASCADE;
DROP VIEW IF EXISTS onboarding_employee_data CASCADE;
DROP VIEW IF EXISTS biometric_employee_mapping CASCADE;
DROP MATERIALIZED VIEW IF EXISTS active_employees_cache CASCADE;

\echo '✅ Phase 2 rollback complete. Compatibility views removed.';

-- ========================================
-- PHASE 4 ROLLBACK: Restore Old Foreign Keys
-- ========================================
-- Use if: New FK constraints causing issues
-- Safe to run: YES (if old tables still exist)
-- WARNING: Only works if old tables not deleted yet!
-- ========================================

-- ROLLBACK_PHASE4.sql
\echo 'Rolling back Phase 4: Restoring old foreign keys...'

BEGIN;

-- Drop new FK constraints
\echo 'Step 1: Dropping new FK constraints...'

ALTER TABLE finance_employee_salary_components DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE finance_salary_slips DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE payroll_ai_insight_snapshot DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE payroll_audit_alert DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE payroll_validation_log DROP CONSTRAINT IF EXISTS fk_employee_master;

ALTER TABLE onboarding_access DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE onboarding_checklist DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE onboarding_document DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE onboarding_equipment DROP CONSTRAINT IF EXISTS fk_employee_master;

-- Drop new indexes
\echo 'Step 2: Dropping new indexes...'

DROP INDEX IF EXISTS idx_finance_salary_components_employee_master;
DROP INDEX IF EXISTS idx_finance_salary_slips_employee_master;
DROP INDEX IF EXISTS idx_payroll_insight_employee_master;
DROP INDEX IF EXISTS idx_payroll_alert_employee_master;
DROP INDEX IF EXISTS idx_payroll_validation_employee_master;
DROP INDEX IF EXISTS idx_onboarding_access_employee_master;
DROP INDEX IF EXISTS idx_onboarding_checklist_employee_master;
DROP INDEX IF EXISTS idx_onboarding_document_employee_master;
DROP INDEX IF EXISTS idx_onboarding_equipment_employee_master;

-- Remove new columns
\echo 'Step 3: Removing new FK columns...'

ALTER TABLE finance_employee_salary_components DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE finance_salary_slips DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE payroll_ai_insight_snapshot DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE payroll_audit_alert DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE payroll_validation_log DROP COLUMN IF EXISTS employee_master_id;

ALTER TABLE onboarding_access DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE onboarding_checklist DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE onboarding_document DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE onboarding_equipment DROP COLUMN IF EXISTS employee_master_id;

-- Restore old FK constraints (if old tables exist)
\echo 'Step 4: Restoring old FK constraints...'

-- Check if old tables exist before adding constraints
DO $$
BEGIN
    -- Finance table FKs
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'finance_employee_salary_info') THEN
        ALTER TABLE finance_employee_salary_components
        ADD CONSTRAINT finance_employee_salary_components_employee_salary_info_id_fkey
        FOREIGN KEY (employee_salary_info_id) REFERENCES finance_employee_salary_info(id);
        
        ALTER TABLE finance_salary_slips
        ADD CONSTRAINT finance_salary_slips_employee_salary_info_id_fkey
        FOREIGN KEY (employee_salary_info_id) REFERENCES finance_employee_salary_info(id);
        
        ALTER TABLE payroll_ai_insight_snapshot
        ADD CONSTRAINT payroll_ai_insight_snapshot_employee_salary_info_id_fkey
        FOREIGN KEY (employee_salary_info_id) REFERENCES finance_employee_salary_info(id);
        
        ALTER TABLE payroll_audit_alert
        ADD CONSTRAINT payroll_audit_alert_employee_salary_info_id_fkey
        FOREIGN KEY (employee_salary_info_id) REFERENCES finance_employee_salary_info(id);
        
        ALTER TABLE payroll_validation_log
        ADD CONSTRAINT payroll_validation_log_employee_salary_info_id_fkey
        FOREIGN KEY (employee_salary_info_id) REFERENCES finance_employee_salary_info(id);
        
        RAISE NOTICE 'Finance FK constraints restored';
    ELSE
        RAISE WARNING 'finance_employee_salary_info table not found - cannot restore FKs';
    END IF;
    
    -- Onboarding table FKs
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'onboarding_record') THEN
        ALTER TABLE onboarding_access
        ADD CONSTRAINT onboarding_access_onboarding_record_id_fkey
        FOREIGN KEY (onboarding_record_id) REFERENCES onboarding_record(id);
        
        ALTER TABLE onboarding_checklist
        ADD CONSTRAINT onboarding_checklist_onboarding_record_id_fkey
        FOREIGN KEY (onboarding_record_id) REFERENCES onboarding_record(id);
        
        ALTER TABLE onboarding_document
        ADD CONSTRAINT onboarding_document_onboarding_record_id_fkey
        FOREIGN KEY (onboarding_record_id) REFERENCES onboarding_record(id);
        
        ALTER TABLE onboarding_equipment
        ADD CONSTRAINT onboarding_equipment_onboarding_record_id_fkey
        FOREIGN KEY (onboarding_record_id) REFERENCES onboarding_record(id);
        
        RAISE NOTICE 'Onboarding FK constraints restored';
    ELSE
        RAISE WARNING 'onboarding_record table not found - cannot restore FKs';
    END IF;
END $$;

COMMIT;

\echo '✅ Phase 4 rollback complete. Old FK constraints restored.';

-- ========================================
-- PHASE 5 ROLLBACK: Restore Deprecated Tables
-- ========================================
-- Use if: Need to restore deprecated tables
-- Safe to run: YES (if tables still exist with _deprecated suffix)
-- ========================================

-- ROLLBACK_PHASE5.sql
\echo 'Rolling back Phase 5: Restoring deprecated tables...'

BEGIN;

-- Check and rename tables back
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_profiles_deprecated_20260703') THEN
        ALTER TABLE user_profiles_deprecated_20260703 RENAME TO user_profiles;
        RAISE NOTICE 'user_profiles table restored';
    ELSE
        RAISE WARNING 'user_profiles_deprecated_20260703 not found';
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'finance_employee_salary_info_deprecated_20260703') THEN
        ALTER TABLE finance_employee_salary_info_deprecated_20260703 RENAME TO finance_employee_salary_info;
        RAISE NOTICE 'finance_employee_salary_info table restored';
    ELSE
        RAISE WARNING 'finance_employee_salary_info_deprecated_20260703 not found';
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'onboarding_record_deprecated_20260703') THEN
        ALTER TABLE onboarding_record_deprecated_20260703 RENAME TO onboarding_record;
        RAISE NOTICE 'onboarding_record table restored';
    ELSE
        RAISE WARNING 'onboarding_record_deprecated_20260703 not found';
    END IF;
END $$;

COMMIT;

\echo '✅ Phase 5 rollback complete. Tables restored from deprecated state.';

-- ========================================
-- EMERGENCY ROLLBACK: Complete Undo
-- ========================================
-- Use if: Need to completely rollback to pre-migration state
-- Safe to run: ONLY if backups exist
-- WARNING: This will undo ALL migration work!
-- ========================================

-- ROLLBACK_EMERGENCY.sql
\echo 'EMERGENCY ROLLBACK: Undoing all migration phases...'
\echo 'WARNING: This will undo all migration work!'
\echo 'Press Ctrl+C within 5 seconds to cancel...'

SELECT pg_sleep(5);

BEGIN;

\echo 'Step 1: Dropping compatibility views...'
DROP VIEW IF EXISTS user_profiles_from_master CASCADE;
DROP VIEW IF EXISTS finance_employee_salary_info_from_master CASCADE;
DROP VIEW IF EXISTS onboarding_employee_data CASCADE;
DROP VIEW IF EXISTS biometric_employee_mapping CASCADE;
DROP MATERIALIZED VIEW IF EXISTS active_employees_cache CASCADE;

\echo 'Step 2: Removing new FK constraints and columns...'
ALTER TABLE finance_employee_salary_components DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE finance_salary_slips DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE payroll_ai_insight_snapshot DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE payroll_audit_alert DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE payroll_validation_log DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE onboarding_access DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE onboarding_checklist DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE onboarding_document DROP CONSTRAINT IF EXISTS fk_employee_master;
ALTER TABLE onboarding_equipment DROP CONSTRAINT IF EXISTS fk_employee_master;

ALTER TABLE finance_employee_salary_components DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE finance_salary_slips DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE payroll_ai_insight_snapshot DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE payroll_audit_alert DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE payroll_validation_log DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE onboarding_access DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE onboarding_checklist DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE onboarding_document DROP COLUMN IF EXISTS employee_master_id;
ALTER TABLE onboarding_equipment DROP COLUMN IF EXISTS employee_master_id;

\echo 'Step 3: Restoring deprecated tables...'
ALTER TABLE user_profiles_deprecated_20260703 RENAME TO user_profiles;
ALTER TABLE finance_employee_salary_info_deprecated_20260703 RENAME TO finance_employee_salary_info;
ALTER TABLE onboarding_record_deprecated_20260703 RENAME TO onboarding_record;

\echo 'Step 4: Restoring old FK constraints...'
-- (Same as ROLLBACK_PHASE4.sql Step 4)

COMMIT;

\echo '✅ EMERGENCY ROLLBACK COMPLETE';
\echo '';
\echo 'System restored to pre-migration state.';
\echo 'You can now restore code from git to match database state.';
\echo '';

-- ========================================
-- ROLLBACK VERIFICATION
-- ========================================

-- Check current table state
SELECT 
    table_name,
    CASE 
        WHEN table_name LIKE '%_deprecated_%' THEN '⚠️ DEPRECATED'
        WHEN table_name = 'hr_employee_master' THEN '✅ NEW MASTER'
        WHEN table_name IN ('users', 'rbac_user_profiles') THEN '✅ ACTIVE'
        ELSE '📊 LEGACY'
    END as status
FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN (
    'users',
    'user_profiles',
    'user_profiles_deprecated_20260703',
    'rbac_user_profiles',
    'hr_employee_master',
    'finance_employee_salary_info',
    'finance_employee_salary_info_deprecated_20260703',
    'onboarding_record',
    'onboarding_record_deprecated_20260703'
)
ORDER BY table_name;
