-- ================================================================
-- PHASE 4: Migrate Foreign Keys to hr_employee_master
-- ================================================================
-- This script migrates foreign key constraints from old tables to hr_employee_master
-- Run this AFTER Phase 3 (code migration) is complete
--
-- CRITICAL: Test in dev/staging first!
-- BACKUP: Ensure full database backup before running
--
-- Usage:
--   psql -U aiflow_user -d aiflow_dev -f migrate_foreign_keys.sql
-- ================================================================

\echo '========================================================================'
\echo '  PHASE 4: Foreign Key Migration to hr_employee_master'
\echo '  WARNING: This will modify table structure'
\echo '  Ensure you have a backup before proceeding!'
\echo '========================================================================'
\echo ''

-- Start transaction (can rollback if issues)
BEGIN;

\echo 'Step 1: Add new FK columns to finance tables...'

-- ========================================
-- FINANCE TABLES (5 tables)
-- ========================================

-- 1. finance_employee_salary_components
ALTER TABLE finance_employee_salary_components 
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

-- Populate from hr_employee_master using employee_code mapping
UPDATE finance_employee_salary_components 
SET employee_master_id = (
    SELECT em.id 
    FROM hr_employee_master em
    JOIN finance_employee_salary_info esi ON em.employee_code = esi.employee_code
    WHERE esi.id = finance_employee_salary_components.employee_salary_info_id
);

-- 2. finance_salary_slips
ALTER TABLE finance_salary_slips
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

UPDATE finance_salary_slips
SET employee_master_id = (
    SELECT em.id 
    FROM hr_employee_master em
    JOIN finance_employee_salary_info esi ON em.employee_code = esi.employee_code
    WHERE esi.id = finance_salary_slips.employee_salary_info_id
);

-- 3. payroll_ai_insight_snapshot
ALTER TABLE payroll_ai_insight_snapshot
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

UPDATE payroll_ai_insight_snapshot
SET employee_master_id = (
    SELECT em.id 
    FROM hr_employee_master em
    JOIN finance_employee_salary_info esi ON em.employee_code = esi.employee_code
    WHERE esi.id = payroll_ai_insight_snapshot.employee_salary_info_id
);

-- 4. payroll_audit_alert
ALTER TABLE payroll_audit_alert
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

UPDATE payroll_audit_alert
SET employee_master_id = (
    SELECT em.id 
    FROM hr_employee_master em
    JOIN finance_employee_salary_info esi ON em.employee_code = esi.employee_code
    WHERE esi.id = payroll_audit_alert.employee_salary_info_id
);

-- 5. payroll_validation_log
ALTER TABLE payroll_validation_log
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

UPDATE payroll_validation_log
SET employee_master_id = (
    SELECT em.id 
    FROM hr_employee_master em
    JOIN finance_employee_salary_info esi ON em.employee_code = esi.employee_code
    WHERE esi.id = payroll_validation_log.employee_salary_info_id
);

\echo 'Step 2: Add new FK columns to onboarding tables...'

-- ========================================
-- ONBOARDING TABLES (4 tables)
-- ========================================

-- 1. onboarding_access
ALTER TABLE onboarding_access
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

-- Map via user_id (since hr_employee_master has user_id)
UPDATE onboarding_access
SET employee_master_id = (
    SELECT em.id
    FROM hr_employee_master em
    JOIN onboarding_record orec ON em.user_id = orec.user_id
    WHERE orec.id = onboarding_access.onboarding_record_id
);

-- 2. onboarding_checklist
ALTER TABLE onboarding_checklist
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

UPDATE onboarding_checklist
SET employee_master_id = (
    SELECT em.id
    FROM hr_employee_master em
    JOIN onboarding_record orec ON em.user_id = orec.user_id
    WHERE orec.id = onboarding_checklist.onboarding_record_id
);

-- 3. onboarding_document
ALTER TABLE onboarding_document
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

UPDATE onboarding_document
SET employee_master_id = (
    SELECT em.id
    FROM hr_employee_master em
    JOIN onboarding_record orec ON em.user_id = orec.user_id
    WHERE orec.id = onboarding_document.onboarding_record_id
);

-- 4. onboarding_equipment
ALTER TABLE onboarding_equipment
ADD COLUMN IF NOT EXISTS employee_master_id uuid;

UPDATE onboarding_equipment
SET employee_master_id = (
    SELECT em.id
    FROM hr_employee_master em
    JOIN onboarding_record orec ON em.user_id = orec.user_id
    WHERE orec.id = onboarding_equipment.onboarding_record_id
);

\echo 'Step 3: Verify data migration...'

-- Check if any rows failed to get employee_master_id
DO $$
DECLARE
    finance_null_count integer;
    onboarding_null_count integer;
BEGIN
    -- Check finance tables
    SELECT 
        (SELECT COUNT(*) FROM finance_employee_salary_components WHERE employee_master_id IS NULL) +
        (SELECT COUNT(*) FROM finance_salary_slips WHERE employee_master_id IS NULL) +
        (SELECT COUNT(*) FROM payroll_ai_insight_snapshot WHERE employee_master_id IS NULL) +
        (SELECT COUNT(*) FROM payroll_audit_alert WHERE employee_master_id IS NULL) +
        (SELECT COUNT(*) FROM payroll_validation_log WHERE employee_master_id IS NULL)
    INTO finance_null_count;
    
    -- Check onboarding tables
    SELECT 
        (SELECT COUNT(*) FROM onboarding_access WHERE employee_master_id IS NULL) +
        (SELECT COUNT(*) FROM onboarding_checklist WHERE employee_master_id IS NULL) +
        (SELECT COUNT(*) FROM onboarding_document WHERE employee_master_id IS NULL) +
        (SELECT COUNT(*) FROM onboarding_equipment WHERE employee_master_id IS NULL)
    INTO onboarding_null_count;
    
    IF finance_null_count > 0 OR onboarding_null_count > 0 THEN
        RAISE EXCEPTION 'Migration failed: % finance rows and % onboarding rows have NULL employee_master_id',
            finance_null_count, onboarding_null_count;
    END IF;
    
    RAISE NOTICE 'Data migration verified: All rows have employee_master_id';
END $$;

\echo 'Step 4: Add foreign key constraints...'

-- ========================================
-- ADD FOREIGN KEY CONSTRAINTS
-- ========================================

-- Finance tables
ALTER TABLE finance_employee_salary_components
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

ALTER TABLE finance_salary_slips
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

ALTER TABLE payroll_ai_insight_snapshot
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

ALTER TABLE payroll_audit_alert
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

ALTER TABLE payroll_validation_log
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

-- Onboarding tables
ALTER TABLE onboarding_access
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

ALTER TABLE onboarding_checklist
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

ALTER TABLE onboarding_document
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

ALTER TABLE onboarding_equipment
ADD CONSTRAINT fk_employee_master
FOREIGN KEY (employee_master_id) REFERENCES hr_employee_master(id)
ON DELETE CASCADE;

\echo 'Step 5: Create indexes on new FK columns...'

-- ========================================
-- CREATE INDEXES
-- ========================================

CREATE INDEX IF NOT EXISTS idx_finance_salary_components_employee_master 
ON finance_employee_salary_components(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_finance_salary_slips_employee_master 
ON finance_salary_slips(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_payroll_insight_employee_master 
ON payroll_ai_insight_snapshot(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_payroll_alert_employee_master 
ON payroll_audit_alert(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_payroll_validation_employee_master 
ON payroll_validation_log(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_access_employee_master 
ON onboarding_access(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_checklist_employee_master 
ON onboarding_checklist(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_document_employee_master 
ON onboarding_document(employee_master_id);

CREATE INDEX IF NOT EXISTS idx_onboarding_equipment_employee_master 
ON onboarding_equipment(employee_master_id);

\echo 'Step 6: Drop old foreign key constraints...'

-- ========================================
-- DROP OLD FOREIGN KEY CONSTRAINTS
-- ========================================

-- Finance tables
ALTER TABLE finance_employee_salary_components
DROP CONSTRAINT IF EXISTS finance_employee_salary_components_employee_salary_info_id_fkey CASCADE;

ALTER TABLE finance_salary_slips
DROP CONSTRAINT IF EXISTS finance_salary_slips_employee_salary_info_id_fkey CASCADE;

ALTER TABLE payroll_ai_insight_snapshot
DROP CONSTRAINT IF EXISTS payroll_ai_insight_snapshot_employee_salary_info_id_fkey CASCADE;

ALTER TABLE payroll_audit_alert
DROP CONSTRAINT IF EXISTS payroll_audit_alert_employee_salary_info_id_fkey CASCADE;

ALTER TABLE payroll_validation_log
DROP CONSTRAINT IF EXISTS payroll_validation_log_employee_salary_info_id_fkey CASCADE;

-- Onboarding tables
ALTER TABLE onboarding_access
DROP CONSTRAINT IF EXISTS onboarding_access_onboarding_record_id_fkey CASCADE;

ALTER TABLE onboarding_checklist
DROP CONSTRAINT IF EXISTS onboarding_checklist_onboarding_record_id_fkey CASCADE;

ALTER TABLE onboarding_document
DROP CONSTRAINT IF EXISTS onboarding_document_onboarding_record_id_fkey CASCADE;

ALTER TABLE onboarding_equipment
DROP CONSTRAINT IF EXISTS onboarding_equipment_onboarding_record_id_fkey CASCADE;

\echo 'Step 7: Generate migration summary...'

-- ========================================
-- MIGRATION SUMMARY
-- ========================================

DO $$
DECLARE
    finance_migrated integer;
    onboarding_migrated integer;
BEGIN
    -- Count migrated records
    SELECT 
        (SELECT COUNT(*) FROM finance_employee_salary_components WHERE employee_master_id IS NOT NULL) +
        (SELECT COUNT(*) FROM finance_salary_slips WHERE employee_master_id IS NOT NULL) +
        (SELECT COUNT(*) FROM payroll_ai_insight_snapshot WHERE employee_master_id IS NOT NULL) +
        (SELECT COUNT(*) FROM payroll_audit_alert WHERE employee_master_id IS NOT NULL) +
        (SELECT COUNT(*) FROM payroll_validation_log WHERE employee_master_id IS NOT NULL)
    INTO finance_migrated;
    
    SELECT 
        (SELECT COUNT(*) FROM onboarding_access WHERE employee_master_id IS NOT NULL) +
        (SELECT COUNT(*) FROM onboarding_checklist WHERE employee_master_id IS NOT NULL) +
        (SELECT COUNT(*) FROM onboarding_document WHERE employee_master_id IS NOT NULL) +
        (SELECT COUNT(*) FROM onboarding_equipment WHERE employee_master_id IS NOT NULL)
    INTO onboarding_migrated;
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'MIGRATION SUMMARY';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Finance Records Migrated: %', finance_migrated;
    RAISE NOTICE 'Onboarding Records Migrated: %', onboarding_migrated;
    RAISE NOTICE 'Total Records: %', finance_migrated + onboarding_migrated;
    RAISE NOTICE '';
    RAISE NOTICE 'New FK Constraints: 9 added';
    RAISE NOTICE 'Old FK Constraints: 9 removed';
    RAISE NOTICE 'Indexes Created: 9';
    RAISE NOTICE '========================================';
END $$;

-- Commit transaction
COMMIT;

\echo ''
\echo '✅ Phase 4 Migration Complete!'
\echo ''
\echo 'Next Steps:'
\echo '1. Test all finance/payroll functionality'
\echo '2. Test all onboarding workflows'
\echo '3. Verify foreign key constraints working'
\echo '4. Update Django models to use employee_master_id'
\echo '5. Run: python manage.py makemigrations'
\echo '6. Run: python manage.py migrate'
\echo ''
