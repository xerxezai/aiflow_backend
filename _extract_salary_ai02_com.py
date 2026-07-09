"""
Extract Salary_AI02.xlsx using Excel COM automation (works with locked files)
"""
import sys
import os

try:
    import win32com.client as win32
    print("✅ win32com available - using Excel COM automation")
    use_com = True
except ImportError:
    print("❌ win32com not available - please install: pip install pywin32")
    sys.exit(1)

# File paths
excel_path = os.path.abspath(r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\Human Resource\Salary_AI02.xlsx')
csv_output = os.path.abspath(r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend\salary_ai02_extracted.csv')

print("="*80)
print("📊 SALARY AI02 - SMART EXTRACTION (COM Automation)")
print("="*80)
print(f"Source: {excel_path}")
print(f"Target: {csv_output}")
print("="*80)

# Check file exists
if not os.path.exists(excel_path):
    print(f"❌ File not found: {excel_path}")
    sys.exit(1)

file_size = os.path.getsize(excel_path)
print(f"✅ File exists: {file_size:,} bytes ({file_size / 1024 / 1024:.2f} MB)")
print("")

try:
    print("🚀 Launching Excel application...")
    excel = win32.gencache.EnsureDispatch('Excel.Application')
    excel.Visible = False
    excel.DisplayAlerts = False
    
    print("📖 Opening workbook (read-only mode)...")
    wb = excel.Workbooks.Open(excel_path, ReadOnly=True, IgnoreReadOnlyRecommended=True)
    
    print(f"✅ Workbook opened: {wb.Name}")
    print(f"📋 Sheets: {wb.Sheets.Count}")
    
    # Get first sheet
    ws = wb.Sheets(1)
    sheet_name = ws.Name
    
    print(f"\n📊 Reading sheet: '{sheet_name}'")
    
    # Get used range
    used_range = ws.UsedRange
    rows = used_range.Rows.Count
    cols = used_range.Columns.Count
    
    print(f"   Rows: {rows}")
    print(f"   Columns: {cols}")
    
    # Get header row
    print(f"\n📋 Column Headers:")
    headers = []
    for col in range(1, cols + 1):
        cell_value = ws.Cells(1, col).Value
        if cell_value:
            headers.append(str(cell_value).strip())
            print(f"   {col:2d}. {cell_value}")
        else:
            headers.append(f'Column_{col}')
            print(f"   {col:2d}. (empty) → Column_{col}")
    
    # Preview first 5 data rows
    print(f"\n" + "="*80)
    print("PREVIEW - First 5 Data Rows:")
    print("="*80)
    
    preview_rows = min(6, rows)  # Header + 5 data rows
    for row in range(1, preview_rows + 1):
        row_data = []
        for col in range(1, min(cols + 1, 6)):  # Show first 5 columns
            cell_value = ws.Cells(row, col).Value
            if cell_value is None:
                row_data.append('')
            else:
                row_data.append(str(cell_value))
        
        if row == 1:
            print(f"  Row {row} (Headers): {' | '.join(row_data[:5])}")
        else:
            print(f"  Row {row}: {' | '.join(row_data[:5])}")
    
    if cols > 5:
        print(f"  ... and {cols - 5} more columns")
    
    # Export to CSV
    print(f"\n" + "="*80)
    print("💾 EXPORTING TO CSV...")
    print("="*80)
    
    # Delete existing CSV if present
    if os.path.exists(csv_output):
        os.remove(csv_output)
        print(f"   Deleted old CSV file")
    
    # Save as CSV
    wb.SaveAs(csv_output, FileFormat=6)  # 6 = CSV format
    
    print(f"✅ CSV exported successfully!")
    
    # Close workbook
    wb.Close(SaveChanges=False)
    excel.Quit()
    
    # Verify CSV was created
    if os.path.exists(csv_output):
        csv_size = os.path.getsize(csv_output)
        print(f"   Size: {csv_size:,} bytes ({csv_size / 1024:.2f} KB)")
        print(f"   Path: {csv_output}")
        
        # Count lines in CSV
        with open(csv_output, 'r', encoding='utf-8') as f:
            line_count = sum(1 for line in f)
        
        data_rows = line_count - 1  # Exclude header
        
        print(f"\n" + "="*80)
        print("✅ EXTRACTION COMPLETE")
        print("="*80)
        print(f"📄 CSV file ready: salary_ai02_extracted.csv")
        print(f"📊 {data_rows} employee records extracted")
        print(f"\n💡 Next: Import to production database")
        print(f"\n   Step 1: Copy CSV to Docker container")
        print(f"   docker cp backend/salary_ai02_extracted.csv aiflow_backend_local:/app/")
        print(f"\n   Step 2: Import to production database")
        print(f"   docker exec -i aiflow_backend_local bash -c \"")
        print(f"   export DATABASE_URL='postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway'")
        print(f"   python manage.py import_salary_master_excel --file /app/salary_ai02_extracted.csv --production --create-users\"")
    else:
        print(f"❌ CSV file was not created!")
        sys.exit(1)
    
except Exception as e:
    print(f"\n❌ Error during extraction: {e}")
    import traceback
    traceback.print_exc()
    try:
        wb.Close(SaveChanges=False)
        excel.Quit()
    except:
        pass
    sys.exit(1)
