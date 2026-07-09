"""
Database Views for Backward Compatibility

These views maintain 100% backward compatibility with existing Django models
while data is migrated to the new EmployeeMaster table.

DEPLOYMENT INSTRUCTIONS:
1. Apply hr_core migrations first: python manage.py migrate hr_core
2. Run this SQL script: psql -d aiflow_db -f create_compatibility_views.sql
3. Existing code continues working with ZERO changes
4. Gradually migrate data using dual-write approach
5. After full migration, drop views and old tables

CRITICAL: These views are READ-ONLY. Updates must go through EmployeeService
during the migration period to ensure dual-write works correctly.
"""

-- ========================================
-- VIEW 1: user_profiles (from EmployeeMaster)
-- ========================================
-- This view allows existing UserProfile model to read from EmployeeMaster
-- without any code changes.

CREATE OR REPLACE VIEW user_profiles_from_master AS
SELECT
    -- Primary key (maps to user_id for OneToOne relationship)
    user_id as id,
    user_id,
    
    -- Legacy identifiers
    employee_number,
    employment_id,
    candidate_id,
    account_name,
    
    -- Personal
    preferred_given_name,
    initials,
    date_of_birth,
    
    -- Organization
    manager_id,
    company,
    business_unit,
    division,
    business_area,
    office,
    
    -- Job
    job_title_uae,
    job_title_finland,
    
    -- Contact
    country,
    city,
    address,
    postal_code,
    
    -- Flags
    protected_identity,
    is_test_person,
    not_signed,
    
    -- Metadata
    created_at,
    updated_at
FROM hr_employee_master;

COMMENT ON VIEW user_profiles_from_master IS 'Backward compatibility view for UserProfile model during hr_core migration';


-- ========================================
-- VIEW 2: finance_employee_salary_info (from EmployeeMaster)
-- ========================================
-- This view allows existing finance models to read from EmployeeMaster

CREATE OR REPLACE VIEW finance_employee_salary_info_from_master AS
SELECT
    -- Use employee_code as primary key for finance tables
    employee_code as id,
    employee_code,
    
    -- Employee info
    CONCAT(first_name, ' ', last_name) as employee_name,
    email,
    
    -- Salary (denormalized from EmployeeMaster)
    current_base_salary as base_salary,
    
    -- Organization
    department,
    designation,
    
    -- Employment
    join_date,
    employment_status as status,
    
    -- Banking
    bank_account_number,
    bank_name,
    
    -- Tax
    pan_number,
    uan_number,
    
    -- Metadata
    created_at,
    updated_at
FROM hr_employee_master
WHERE employment_status IN ('active', 'probation', 'notice_period');

COMMENT ON VIEW finance_employee_salary_info_from_master IS 'Backward compatibility view for finance_employee_salary_info during hr_core migration';


-- ========================================
-- VIEW 3: onboarding_record_employee_data (from EmployeeMaster)
-- ========================================
-- This view provides employee data for onboarding records

CREATE OR REPLACE VIEW onboarding_employee_data AS
SELECT
    id,
    employee_number as employee_id,
    email as employee_email,
    CONCAT(first_name, ' ', last_name) as employee_name,
    
    -- Photo fields
    photo_file_path,
    photo_url,
    photo_file_size,
    photo_mime_type,
    
    -- Organization
    designation as position,
    department,
    branch,
    
    -- Manager
    manager_id as reporting_manager_id,
    
    -- Employment
    join_date as joining_date,
    
    created_at,
    updated_at
FROM hr_employee_master;

COMMENT ON VIEW onboarding_employee_data IS 'Employee data view for onboarding integration during hr_core migration';


-- ========================================
-- VIEW 4: biometric_employee_mapping (from EmployeeMaster)
-- ========================================
-- This view maps biometric emp_code to EmployeeMaster

CREATE OR REPLACE VIEW biometric_employee_mapping AS
SELECT
    emp_code,
    employee_code,
    employee_number,
    CONCAT(first_name, ' ', last_name) as employee_name,
    email,
    department,
    employment_status
FROM hr_employee_master
WHERE employment_status IN ('active', 'probation', 'notice_period');

COMMENT ON VIEW biometric_employee_mapping IS 'Biometric system employee mapping during hr_core migration';


-- ========================================
-- MATERIALIZED VIEW: Active Employees Cache
-- ========================================
-- For performance, create a materialized view of active employees
-- Refresh this view daily via Celery task

CREATE MATERIALIZED VIEW IF NOT EXISTS active_employees_cache AS
SELECT
    id,
    employee_number,
    employee_code,
    emp_code,
    email,
    first_name,
    last_name,
    preferred_given_name,
    department,
    division,
    designation,
    branch,
    photo_url,
    manager_id,
    employment_status,
    join_date
FROM hr_employee_master
WHERE employment_status IN ('active', 'probation')
ORDER BY department, last_name, first_name;

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_employees_cache_id ON active_employees_cache (id);
CREATE INDEX IF NOT EXISTS idx_active_employees_cache_emp_num ON active_employees_cache (employee_number);
CREATE INDEX IF NOT EXISTS idx_active_employees_cache_dept ON active_employees_cache (department);

COMMENT ON MATERIALIZED VIEW active_employees_cache IS 'Cached active employees for fast lookups (refreshed daily)';

-- Function to refresh the materialized view
CREATE OR REPLACE FUNCTION refresh_active_employees_cache()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY active_employees_cache;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION refresh_active_employees_cache IS 'Refresh active employees cache (call from Celery daily)';


-- ========================================
-- INDEXES for Performance
-- ========================================
-- These indexes ensure views perform well even with large datasets

-- Note: Indexes are on the base table (hr_employee_master), not views
-- The main table already has these indexes from the Django model Meta class

-- Verify key indexes exist
DO $$
BEGIN
    -- Check if required indexes exist, create if missing
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'hr_employee_master' AND indexname = 'idx_hr_emp_email') THEN
        CREATE INDEX idx_hr_emp_email ON hr_employee_master(email);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'hr_employee_master' AND indexname = 'idx_hr_emp_number') THEN
        CREATE INDEX idx_hr_emp_number ON hr_employee_master(employee_number);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'hr_employee_master' AND indexname = 'idx_hr_emp_code') THEN
        CREATE INDEX idx_hr_emp_code ON hr_employee_master(employee_code);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'hr_employee_master' AND indexname = 'idx_hr_emp_biometric') THEN
        CREATE INDEX idx_hr_emp_biometric ON hr_employee_master(emp_code);
    END IF;
END $$;


-- ========================================
-- TRIGGERS: Auto-update User.avatar when photo changes
-- ========================================
-- This trigger keeps user.avatar in sync with EmployeeMaster.photo_url

CREATE OR REPLACE FUNCTION sync_employee_photo_to_user()
RETURNS TRIGGER AS $$
BEGIN
    -- Update user.avatar when employee photo changes
    IF NEW.photo_url IS DISTINCT FROM OLD.photo_url THEN
        UPDATE users
        SET avatar = NEW.photo_url
        WHERE id = NEW.user_id;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_sync_employee_photo ON hr_employee_master;
CREATE TRIGGER trigger_sync_employee_photo
    AFTER UPDATE OF photo_url ON hr_employee_master
    FOR EACH ROW
    WHEN (NEW.photo_url IS DISTINCT FROM OLD.photo_url)
    EXECUTE FUNCTION sync_employee_photo_to_user();

COMMENT ON TRIGGER trigger_sync_employee_photo ON hr_employee_master IS 'Auto-sync employee photo to user.avatar';


-- ========================================
-- HELPER FUNCTIONS
-- ========================================

-- Function to get employee by any identifier
CREATE OR REPLACE FUNCTION get_employee_by_identifier(identifier TEXT)
RETURNS TABLE (
    id UUID,
    employee_number VARCHAR(50),
    employee_code VARCHAR(50),
    emp_code VARCHAR(20),
    email VARCHAR(254),
    full_name TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        em.id,
        em.employee_number,
        em.employee_code,
        em.emp_code,
        em.email,
        CONCAT(em.first_name, ' ', em.last_name) as full_name
    FROM hr_employee_master em
    WHERE em.employee_number = identifier
       OR em.employee_code = identifier
       OR em.emp_code = identifier
       OR em.email = identifier
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_employee_by_identifier IS 'Find employee by any legacy identifier';


-- ========================================
-- VERIFICATION QUERIES
-- ========================================

-- Run these queries to verify views are working correctly

-- Check view row counts
SELECT 'user_profiles_from_master' as view_name, COUNT(*) as row_count FROM user_profiles_from_master
UNION ALL
SELECT 'finance_employee_salary_info_from_master', COUNT(*) FROM finance_employee_salary_info_from_master
UNION ALL
SELECT 'onboarding_employee_data', COUNT(*) FROM onboarding_employee_data
UNION ALL
SELECT 'biometric_employee_mapping', COUNT(*) FROM biometric_employee_mapping
UNION ALL
SELECT 'active_employees_cache', COUNT(*) FROM active_employees_cache;

-- Sample data from each view
SELECT * FROM user_profiles_from_master LIMIT 5;
SELECT * FROM finance_employee_salary_info_from_master LIMIT 5;
SELECT * FROM onboarding_employee_data LIMIT 5;
SELECT * FROM biometric_employee_mapping LIMIT 5;
SELECT * FROM active_employees_cache LIMIT 5;

-- Test employee lookup function
SELECT * FROM get_employee_by_identifier('test@example.com');


-- ========================================
-- CLEANUP (Run after full migration is complete)
-- ========================================

/*
-- STEP 1: Verify all data is in hr_employee_master
SELECT COUNT(*) FROM hr_employee_master;

-- STEP 2: Drop views (after confirming code is updated)
DROP VIEW IF EXISTS user_profiles_from_master CASCADE;
DROP VIEW IF EXISTS finance_employee_salary_info_from_master CASCADE;
DROP VIEW IF EXISTS onboarding_employee_data CASCADE;
DROP VIEW IF EXISTS biometric_employee_mapping CASCADE;
DROP MATERIALIZED VIEW IF EXISTS active_employees_cache CASCADE;

-- STEP 3: Drop triggers and functions
DROP TRIGGER IF EXISTS trigger_sync_employee_photo ON hr_employee_master;
DROP FUNCTION IF EXISTS sync_employee_photo_to_user();
DROP FUNCTION IF EXISTS refresh_active_employees_cache();
DROP FUNCTION IF EXISTS get_employee_by_identifier(TEXT);

-- STEP 4: Archive old tables (rename, don't drop immediately)
-- Wait 30 days before actually dropping tables for safety
ALTER TABLE user_profiles RENAME TO user_profiles_archived_20260703;
ALTER TABLE finance_employee_salary_info RENAME TO finance_employee_salary_info_archived_20260703;

-- STEP 5: After 30-day safety period, drop archived tables
-- DROP TABLE user_profiles_archived_20260703;
-- DROP TABLE finance_employee_salary_info_archived_20260703;
*/
