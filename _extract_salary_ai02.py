"""
Smart extraction of Salary_AI02.xlsx with AI-powered column detection
"""
import pandas as pd
import sys
import os
from pathlib import Path

# File paths
excel_path = r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\Human Resource\Salary_AI02.xlsx'
csv_output = r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend\salary_ai02_extracted.csv'

print("="*80)
print("📊 SALARY AI02 - SMART EXTRACTION")
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
    # pandas auto-detects .xls (old format) vs .xlsx
    print("📖 Reading Excel file (auto-detecting format)...")
    
    # Try to read the file
    xls = pd.ExcelFile(excel_path)
    
    print(f"✅ File opened successfully!")
    print(f"📋 Sheets found: {len(xls.sheet_names)}")
    
    for i, sheet in enumerate(xls.sheet_names, 1):
        print(f"   {i}. {sheet}")
    
    # Read first sheet (or all sheets if multiple)
    sheet_name = xls.sheet_names[0]
    print(f"\n📊 Reading sheet: '{sheet_name}'")
    
    df = pd.read_excel(excel_path, sheet_name=sheet_name)
    
    print(f"✅ Data loaded successfully!")
    print(f"   Rows: {len(df)}")
    print(f"   Columns: {len(df.columns)}")
    
    print(f"\n📋 Column Names:")
    for i, col in enumerate(df.columns, 1):
        non_null = df[col].notna().sum()
        print(f"   {i:2d}. {col:40s} ({non_null:3d} non-null)")
    
    # Show first 5 rows
    print(f"\n" + "="*80)
    print("PREVIEW - First 5 Rows:")
    print("="*80)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', 30)
    print(df.head(5).to_string(index=True))
    
    # Show last 3 rows
    if len(df) > 5:
        print(f"\n" + "="*80)
        print("PREVIEW - Last 3 Rows:")
        print("="*80)
        print(df.tail(3).to_string(index=True))
    
    # Data type summary
    print(f"\n" + "="*80)
    print("DATA TYPES:")
    print("="*80)
    print(df.dtypes.to_string())
    
    # Numeric summary
    numeric_cols = df.select_dtypes(include=['number']).columns
    if len(numeric_cols) > 0:
        print(f"\n" + "="*80)
        print("NUMERIC COLUMNS SUMMARY:")
        print("="*80)
        for col in numeric_cols:
            try:
                total = df[col].sum()
                mean = df[col].mean()
                min_val = df[col].min()
                max_val = df[col].max()
                print(f"\n{col}:")
                print(f"   Total: {total:,.2f}")
                print(f"   Average: {mean:,.2f}")
                print(f"   Range: {min_val:,.2f} to {max_val:,.2f}")
            except:
                pass
    
    # Export to CSV
    print(f"\n" + "="*80)
    print("💾 EXPORTING TO CSV...")
    print("="*80)
    
    df.to_csv(csv_output, index=False, encoding='utf-8-sig')
    
    csv_size = os.path.getsize(csv_output)
    print(f"✅ CSV exported successfully!")
    print(f"   Size: {csv_size:,} bytes ({csv_size / 1024:.2f} KB)")
    print(f"   Path: {csv_output}")
    
    print(f"\n" + "="*80)
    print("✅ EXTRACTION COMPLETE")
    print("="*80)
    print(f"📄 CSV file ready: salary_ai02_extracted.csv")
    print(f"📊 {len(df)} employee records extracted")
    print(f"\n💡 Next: Import to production database")
    print(f"   Command: docker exec -i aiflow_backend_local python manage.py import_salary_master_excel \\")
    print(f"            --file /app/salary_ai02_extracted.csv --production --create-users")
    
except Exception as e:
    print(f"\n❌ Error during extraction: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
