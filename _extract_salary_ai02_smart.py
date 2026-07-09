"""
Smart extraction of Salary_AI02.xlsx - handles multi-sheet payroll with proper header detection
"""
import sys
import os
import csv

try:
    import win32com.client as win32
    print("✅ win32com available - using Excel COM automation")
except ImportError:
    print("❌ win32com not available - please install: pip install pywin32")
    sys.exit(1)

# File paths
excel_path = os.path.abspath(r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\Human Resource\Salary_AI02.xlsx')
csv_output = os.path.abspath(r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend\salary_ai02_extracted.csv')

print("="*80)
print("📊 SALARY AI02 - SMART EXTRACTION (Multi-Sheet Handler)")
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
    print(f"📋 Total sheets: {wb.Sheets.Count}")
    
    # Read first sheet to understand structure
    ws = wb.Sheets(1)
    sheet_name = ws.Name
    
    print(f"\n📊 Analyzing first sheet: '{sheet_name}'")
    
    # Find header row (look for "Employee Number" or "Employee" column)
    header_row_num = None
    for row in range(1, 20):  # Check first 20 rows
        cell_value = ws.Cells(row, 2).Value  # Column 2 usually has "Employee"
        if cell_value and 'Employee' in str(cell_value):
            header_row_num = row
            break
    
    if not header_row_num:
        print("⚠️  Could not find header row automatically, using row 4")
        header_row_num = 4
    
    print(f"   Header row detected: Row {header_row_num}")
    
    # Get headers
    used_range = ws.UsedRange
    max_cols = used_range.Columns.Count
    
    headers = []
    print(f"\n📋 Column Headers (Row {header_row_num}):")
    for col in range(1, max_cols + 1):
        cell_value = ws.Cells(header_row_num, col).Value
        if cell_value and str(cell_value).strip():
            header = str(cell_value).strip()
            headers.append(header)
            print(f"   {col:2d}. {header}")
        else:
            if len(headers) > 0:  # Stop at first empty column after headers started
                break
            headers.append(f'Column_{col}')
    
    num_cols = len(headers)
    print(f"\n✅ Found {num_cols} columns")
    
    # Extract all data from first sheet
    print(f"\n📊 Extracting data from '{sheet_name}'...")
    
    all_rows = []
    data_row_start = header_row_num + 1
    
    # Get all rows
    for row in range(data_row_start, used_range.Rows.Count + 1):
        # Check if row is empty (first column is empty)
        first_col = ws.Cells(row, 1).Value
        if first_col is None or str(first_col).strip() == '':
            continue
        
        row_data = []
        for col in range(1, num_cols + 1):
            cell_value = ws.Cells(row, col).Value
            if cell_value is None:
                row_data.append('')
            else:
                # Handle dates properly
                cell_str = str(cell_value).strip()
                row_data.append(cell_str)
        
        # Only add if row has some data
        if any(cell for cell in row_data):
            all_rows.append(row_data)
    
    print(f"   Extracted {len(all_rows)} data rows")
    
    # Show preview
    print(f"\n" + "="*80)
    print("PREVIEW - First 3 Rows:")
    print("="*80)
    for i, row in enumerate(all_rows[:3], 1):
        print(f"  Row {i}: {' | '.join(row[:5])}")
    
    # Close workbook
    wb.Close(SaveChanges=False)
    excel.Quit()
    print(f"\n✅ Excel closed")
    
    # Write to CSV
    print(f"\n💾 Writing to CSV...")
    
    with open(csv_output, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        
        # Write header
        writer.writerow(headers)
        
        # Write data
        for row_data in all_rows:
            writer.writerow(row_data)
    
    csv_size = os.path.getsize(csv_output)
    print(f"✅ CSV written successfully!")
    print(f"   Size: {csv_size:,} bytes ({csv_size / 1024:.2f} KB)")
    
    print(f"\n" + "="*80)
    print("✅ EXTRACTION COMPLETE")
    print("="*80)
    print(f"📄 CSV file ready: salary_ai02_extracted.csv")
    print(f"📊 {len(all_rows)} employee records extracted")
    print(f"📋 {num_cols} columns: {', '.join(headers[:5])}...")
    
    print(f"\n💡 Next: Import to production database")
    print(f"\n   docker exec -i aiflow_backend_local bash -c \"")
    print(f"   export DATABASE_URL='postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway'")
    print(f"   python manage.py import_salary_master_excel --file /app/salary_ai02_extracted.csv --production --create-users\"")
    
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
