"""
Extract salary_details.xlsx using COM automation (works even if file is locked by Excel)
"""
import sys
import os

try:
    import win32com.client
    print("✅ win32com available - using COM automation")
    use_com = True
except:
    print("⚠️  win32com not available - using pandas")
    use_com = False
    import pandas as pd

excel_path = r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\Human Resource\salary_details.xlsx'
csv_output = r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend\salary_details_extracted.csv'

print("="*80)
print("SALARY DETAILS EXTRACTION")
print("="*80)
print(f"Source: {excel_path}")
print(f"Target: {csv_output}")
print("="*80)

if use_com:
    # Method 1: COM automation (can read locked files)
    print("\n📊 Using COM automation (Excel.Application)...")
    
    excel = win32com.client.Dispatch('Excel.Application')
    excel.Visible = False
    excel.DisplayAlerts = False
    
    try:
        wb = excel.Workbooks.Open(excel_path, ReadOnly=True)
        print(f"✅ File opened: {wb.Name}")
        print(f"📋 Sheets: {wb.Sheets.Count}")
        
        # Get first sheet
        ws = wb.Sheets(1)
        sheet_name = ws.Name
        print(f"📊 Reading sheet: {sheet_name}")
        print(f"📏 Rows: {ws.UsedRange.Rows.Count}")
        print(f"📏 Columns: {ws.UsedRange.Columns.Count}")
        
        # Get header row
        header_row = []
        for col in range(1, ws.UsedRange.Columns.Count + 1):
            cell_value = ws.Cells(1, col).Value
            if cell_value:
                header_row.append(str(cell_value))
            else:
                header_row.append(f'Column_{col}')
        
        print(f"\n📋 Column Headers ({len(header_row)}):")
        for i, h in enumerate(header_row[:15], 1):
            print(f"   {i}. {h}")
        if len(header_row) > 15:
            print(f"   ... and {len(header_row) - 15} more")
        
        # Export to CSV
        print(f"\n💾 Exporting to CSV...")
        wb.SaveAs(csv_output, FileFormat=6)  # 6 = CSV format
        print("✅ CSV exported successfully!")
        
        wb.Close(SaveChanges=False)
        excel.Quit()
        
        print("\n" + "="*80)
        print("✅ EXTRACTION COMPLETE")
        print("="*80)
        print(f"CSV file ready: {csv_output}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        excel.Quit()
        sys.exit(1)

else:
    # Method 2: Pandas (if COM not available)
    print("\n📊 Using pandas...")
    
    try:
        # Try reading with pandas
        df = pd.read_excel(excel_path)
        print(f"✅ File read successfully")
        print(f"📊 Rows: {len(df)}")
        print(f"📊 Columns: {len(df.columns)}")
        
        print(f"\n📋 Column Names:")
        for i, col in enumerate(df.columns, 1):
            print(f"   {i}. {col}")
        
        # Save to CSV
        df.to_csv(csv_output, index=False, encoding='utf-8-sig')
        print(f"\n✅ CSV exported: {csv_output}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

print("\n💡 Next step: Import CSV to production database")
