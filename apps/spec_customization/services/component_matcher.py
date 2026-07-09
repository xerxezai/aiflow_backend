"""
Spec Customization — Component Matching Service
=================================================

Soft-coded configuration and logic for matching PDF-extracted components
to CAT workbook commodity codes using Match.xlsx lookup table.

Workflow:
  1. Parse Match.xlsx → create MatchingRule records
  2. Load SPEC.xlsx → cache specification rules
  3. Load CAT.xlsx → cache component catalog
  4. Match component:
       a. Lookup PDF name in Match.xlsx → get catalog name
       b. Find CAT sheet by catalog name
       c. Apply SPEC filters (pressure/temp/material)
       d. Match by Npd, PressureRating, MaterialGrade
       e. Return IndustryCommodityCode

All thresholds, column mappings, and sheet names are soft-coded below.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from django.core.files.base import File

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded matching configuration
# ─────────────────────────────────────────────────────────────────────────────
COMPONENT_MATCHING_CONFIG = {
    # Match.xlsx structure
    'match_xlsx': {
        'sheet_name': 0,  # First sheet (or specify name like 'Sheet1')
        'pdf_column': 'A',  # Column containing PDF component names
        'catalog_column': 'D',  # Column containing Excel catalog names
        'header_row': 0,  # 0-indexed row number for headers (0 = first row)
        'skip_rows': 0,  # Rows to skip before reading data
    },
    
    # CAT.xlsx structure
    'cat_xlsx': {
        'commodity_code_col': 'IndustryCommodityCode',  # Unique part identifier
        'npd_cols': ['Npd[1]', 'Npd[2]'],  # Nominal pipe diameter columns
        'pressure_rating_cols': ['PressureRating[1]', 'PressureRating[2]'],
        'material_grade_col': 'MaterialGrade',
        'end_prep_cols': ['EndPreparation[1]', 'EndPreparation[2]'],
        'end_standard_cols': ['EndStandard[1]', 'EndStandard[2]'],
        'commodity_type_col': 'CommodityType',
        'description_col': 'Description',
        'weight_col': 'DryWeight',
        'symbol_col': 'SymbolDefinition',
    },
    
    # SPEC.xlsx critical sheets (for filtering)
    'spec_xlsx': {
        'service_limits_sheet': 'ServiceLimits',
        'materials_data_sheet': 'MaterialsData',
        'gasket_selection_sheet': 'GasketSelectionFilter',
        'bolt_selection_sheet': 'BoltSelectionFilter',
        'pipe_nominal_diameters_sheet': 'PipeNominalDiameters',
    },
    
    # Matching priorities (1 = highest)
    'match_criteria': {
        'component_type': 1,  # Exact match from Match.xlsx
        'nominal_size': 2,  # Npd match
        'pressure_rating': 3,  # Pressure class match
        'material_grade': 4,  # Material match
        'end_preparation': 5,  # Connection type match
    },
    
    # Fuzzy matching thresholds
    'fuzzy': {
        'enabled': True,
        'min_score': 0.80,  # Minimum similarity score (0-1)
        'use_levenshtein': True,
    },
    
    # Catalog sheet name normalization
    'sheet_normalization': {
        'remove_spaces': True,  # "Gate Valve" → "GateValve"
        'remove_degree_symbol': True,  # "90° Elbow" → "90DegElbow"
        'replacements': {
            '90 Degree Direction Change': '90DegLRElbow',  # Special case
            '45 Degree Direction Change': '45DegElbow',
            'Tee': 'Tee',
            'Reducing Tee': 'ReducingTee',
            'Nipple': 'Nipple',
            'Sockolet': 'Sockolet',
            'Weldolet': 'Weldolet',
        }
    },
    
    # Size normalization (convert to standard format)
    'size_normalization': {
        'strip_units': True,  # Remove ", DN, NPS
        'convert_mm_to_inch': False,  # Keep as-is
        'decimal_precision': 2,
    },
    
    # Pressure rating normalization
    'pressure_normalization': {
        'strip_symbols': True,  # Remove #, lbs, etc.
        'standard_ratings': ['150', '300', '600', '900', '1500', '2500'],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Normalize catalog name to CAT sheet name
# ─────────────────────────────────────────────────────────────────────────────
def normalize_catalog_name_to_sheet(catalog_name: str) -> str:
    """
    Convert catalog component name to CAT.xlsx sheet name.
    
    Examples:
        "Gate Valve" → "GateValve"
        "90 Degree Direction Change" → "90DegLRElbow"
        "45 Degree Direction Change" → "45DegElbow"
    """
    cfg = COMPONENT_MATCHING_CONFIG['sheet_normalization']
    
    # Check for exact replacements first
    if catalog_name in cfg['replacements']:
        return cfg['replacements'][catalog_name]
    
    # Default: remove spaces
    result = catalog_name
    if cfg['remove_spaces']:
        result = result.replace(' ', '')
    
    if cfg['remove_degree_symbol']:
        result = result.replace('°', 'Deg')
        result = result.replace(' Deg', 'Deg')
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Normalize size values
# ─────────────────────────────────────────────────────────────────────────────
def normalize_size(size: str) -> str:
    """
    Normalize size to standard format.
    
    Examples:
        '2"' → '2'
        'DN50' → '50' (if strip_units enabled)
        '1.5' → '1.5'
    """
    if not size:
        return ''
    
    cfg = COMPONENT_MATCHING_CONFIG['size_normalization']
    
    # Strip common units
    if cfg['strip_units']:
        size = str(size).replace('"', '').replace('DN', '').replace('NPS', '').strip()
    
    # Try to convert to float and format
    try:
        val = float(size)
        if cfg.get('decimal_precision'):
            return f"{val:.{cfg['decimal_precision']}f}".rstrip('0').rstrip('.')
        return str(val)
    except (ValueError, TypeError):
        return str(size).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Normalize pressure rating
# ─────────────────────────────────────────────────────────────────────────────
def normalize_pressure(pressure: str) -> str:
    """
    Normalize pressure rating to standard format.
    
    Examples:
        '150#' → '150'
        '300 lbs' → '300'
        'PN16' → '16'
    """
    if not pressure:
        return ''
    
    cfg = COMPONENT_MATCHING_CONFIG['pressure_normalization']
    
    # Strip symbols
    if cfg['strip_symbols']:
        pressure = str(pressure).replace('#', '').replace('lbs', '').replace('PN', '').strip()
    
    # Extract numeric part
    match = re.search(r'\d+', str(pressure))
    if match:
        return match.group(0)
    
    return str(pressure).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Service: Parse Match.xlsx and create MatchingRule records
# ─────────────────────────────────────────────────────────────────────────────
def parse_match_xlsx(
    file_path: str,
    workbook_set,
) -> Tuple[int, str]:
    """
    Parse Match.xlsx and create MatchingRule records.
    
    Returns:
        (rules_count, error_message)
    """
    from .matching_models import MatchingRule
    
    cfg = COMPONENT_MATCHING_CONFIG['match_xlsx']
    
    try:
        # Read Excel file
        df = pd.read_excel(
            file_path,
            sheet_name=cfg['sheet_name'],
            header=cfg['header_row'] if cfg['header_row'] >= 0 else None,
            skiprows=cfg['skip_rows'] if cfg['skip_rows'] > 0 else None,
        )
        
        # If no header, use column letters
        if cfg['header_row'] < 0:
            # Convert column letters to indices (A=0, D=3)
            pdf_col_idx = ord(cfg['pdf_column'].upper()) - ord('A')
            cat_col_idx = ord(cfg['catalog_column'].upper()) - ord('A')
        else:
            # Use actual column names or indices
            pdf_col_idx = cfg['pdf_column'] if isinstance(cfg['pdf_column'], int) else 0
            cat_col_idx = cfg['catalog_column'] if isinstance(cfg['catalog_column'], int) else 3
        
        # Extract mappings
        rules_created = 0
        for idx, row in df.iterrows():
            try:
                # Get values by column index
                pdf_name = str(row.iloc[pdf_col_idx]).strip() if pd.notna(row.iloc[pdf_col_idx]) else ''
                catalog_name = str(row.iloc[cat_col_idx]).strip() if pd.notna(row.iloc[cat_col_idx]) else ''
                
                # Skip empty rows
                if not pdf_name or not catalog_name:
                    continue
                
                # Derive CAT sheet name
                cat_sheet = normalize_catalog_name_to_sheet(catalog_name)
                
                # Create or update rule
                MatchingRule.objects.update_or_create(
                    workbook_set=workbook_set,
                    pdf_component_name=pdf_name,
                    defaults={
                        'catalog_component_name': catalog_name,
                        'cat_sheet_name': cat_sheet,
                        'row_number': idx + 2,  # Excel row number (1-indexed + header)
                    }
                )
                rules_created += 1
                
            except Exception as e:
                logger.warning(f"[MatchParser] Failed to parse row {idx}: {e}")
                continue
        
        return rules_created, ''
        
    except Exception as e:
        error_msg = f"Failed to parse Match.xlsx: {str(e)}"
        logger.exception(f"[MatchParser] {error_msg}")
        return 0, error_msg


# ─────────────────────────────────────────────────────────────────────────────
# Service: Load CAT sheet as DataFrame
# ─────────────────────────────────────────────────────────────────────────────
def load_cat_sheet(
    cat_file_path: str,
    sheet_name: str,
) -> Optional[pd.DataFrame]:
    """
    Load a specific sheet from CAT.xlsx.
    
    Returns:
        DataFrame or None if sheet not found
    """
    try:
        df = pd.read_excel(cat_file_path, sheet_name=sheet_name)
        logger.info(f"[CATLoader] Loaded sheet '{sheet_name}' with {len(df)} rows")
        return df
    except Exception as e:
        logger.warning(f"[CATLoader] Failed to load sheet '{sheet_name}': {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Service: Load SPEC sheet as DataFrame
# ─────────────────────────────────────────────────────────────────────────────
def load_spec_sheet(
    spec_file_path: str,
    sheet_name: str,
) -> Optional[pd.DataFrame]:
    """
    Load a specific sheet from SPEC.xlsx.
    
    Returns:
        DataFrame or None if sheet not found
    """
    try:
        df = pd.read_excel(spec_file_path, sheet_name=sheet_name)
        logger.info(f"[SPECLoader] Loaded sheet '{sheet_name}' with {len(df)} rows")
        return df
    except Exception as e:
        logger.warning(f"[SPECLoader] Failed to load sheet '{sheet_name}': {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Service: Match component to CAT commodity code
# ─────────────────────────────────────────────────────────────────────────────
def match_component(
    pdf_component_name: str,
    nominal_size: str,
    pressure_rating: str,
    material_grade: str,
    workbook_set,
) -> Dict[str, Any]:
    """
    Match a PDF component to CAT commodity code.
    
    Args:
        pdf_component_name: Component name from PDF (e.g., "GATE VALVE")
        nominal_size: Pipe size (e.g., "2", "4", "DN50")
        pressure_rating: Pressure class (e.g., "150#", "300#")
        material_grade: Material (e.g., "A105", "316SS")
        workbook_set: MatchingWorkbookSet instance
    
    Returns:
        {
            'matched': bool,
            'commodity_code': str,
            'description': str,
            'match_score': float,
            'match_method': str,
            'error': str,
        }
    """
    from .matching_models import MatchingRule, ComponentMatchingResult
    
    result = {
        'matched': False,
        'commodity_code': '',
        'description': '',
        'match_score': 0.0,
        'match_method': '',
        'error': '',
    }
    
    try:
        # Step 1: Lookup in Match.xlsx
        rule = MatchingRule.objects.filter(
            workbook_set=workbook_set,
            pdf_component_name__iexact=pdf_component_name
        ).first()
        
        if not rule:
            result['error'] = f"No matching rule found for '{pdf_component_name}'"
            return result
        
        catalog_name = rule.catalog_component_name
        cat_sheet_name = rule.cat_sheet_name
        
        # Step 2: Load CAT sheet
        if not workbook_set.cat_file:
            result['error'] = "CAT workbook not uploaded"
            return result
        
        cat_df = load_cat_sheet(workbook_set.cat_file.path, cat_sheet_name)
        if cat_df is None or cat_df.empty:
            result['error'] = f"CAT sheet '{cat_sheet_name}' not found or empty"
            return result
        
        # Step 3: Normalize input criteria
        norm_size = normalize_size(nominal_size)
        norm_pressure = normalize_pressure(pressure_rating)
        norm_material = str(material_grade).strip().upper()
        
        # Step 4: Filter CAT data by criteria
        cfg = COMPONENT_MATCHING_CONFIG['cat_xlsx']
        
        matches = cat_df.copy()
        
        # Filter by size (check both Npd[1] and Npd[2])
        if norm_size:
            size_matches = pd.Series([False] * len(matches))
            for npd_col in cfg['npd_cols']:
                if npd_col in matches.columns:
                    size_matches |= matches[npd_col].astype(str).apply(normalize_size) == norm_size
            matches = matches[size_matches]
        
        # Filter by pressure rating (check both PressureRating[1] and PressureRating[2])
        if norm_pressure:
            pressure_matches = pd.Series([False] * len(matches))
            for pr_col in cfg['pressure_rating_cols']:
                if pr_col in matches.columns:
                    pressure_matches |= matches[pr_col].astype(str).apply(normalize_pressure) == norm_pressure
            matches = matches[pressure_matches]
        
        # Filter by material grade
        if norm_material and cfg['material_grade_col'] in matches.columns:
            matches = matches[
                matches[cfg['material_grade_col']].astype(str).str.upper().str.contains(norm_material, na=False)
            ]
        
        # Step 5: Return best match
        if matches.empty:
            result['error'] = f"No matching component found in CAT for criteria: size={norm_size}, rating={norm_pressure}, material={norm_material}"
            return result
        
        # Take first match (TODO: implement scoring for multiple matches)
        best_match = matches.iloc[0]
        
        result['matched'] = True
        result['commodity_code'] = str(best_match.get(cfg['commodity_code_col'], ''))
        result['description'] = str(best_match.get(cfg['description_col'], catalog_name))
        result['match_score'] = 1.0  # Exact match
        result['match_method'] = 'rule-based'
        
        # Save result to database (optional)
        ComponentMatchingResult.objects.create(
            workbook_set=workbook_set,
            pdf_component_name=pdf_component_name,
            nominal_size=nominal_size,
            pressure_rating=pressure_rating,
            material_grade=material_grade,
            matched_commodity_code=result['commodity_code'],
            matched_description=result['description'],
            match_score=result['match_score'],
            match_method=result['match_method'],
            result_data=result,
        )
        
        return result
        
    except Exception as e:
        error_msg = f"Error matching component: {str(e)}"
        logger.exception(f"[ComponentMatcher] {error_msg}")
        result['error'] = error_msg
        return result
