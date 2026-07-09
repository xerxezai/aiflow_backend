"""
Data Mining Transformation Engine
Soft-coded transformation operations for data pipeline processing
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)

# ─── Soft-coded operator mapping for filter conditions ───────────────────────
FILTER_OPERATORS = {
    'equals': lambda x, v: x == v,
    'not_equals': lambda x, v: x != v,
    'greater_than': lambda x, v: x > v,
    'greater_equal': lambda x, v: x >= v,
    'less_than': lambda x, v: x < v,
    'less_equal': lambda x, v: x <= v,
    'contains': lambda x, v: str(v).lower() in str(x).lower(),
    'not_contains': lambda x, v: str(v).lower() not in str(x).lower(),
    'starts_with': lambda x, v: str(x).lower().startswith(str(v).lower()),
    'ends_with': lambda x, v: str(x).lower().endswith(str(v).lower()),
    'is_null': lambda x, v: pd.isna(x),
    'not_null': lambda x, v: pd.notna(x),
    'in_list': lambda x, v: x in v if isinstance(v, (list, tuple, set)) else False,
}


class TransformationEngine:
    """
    Executes transformation operations on pandas DataFrames
    Soft-coded design allows adding new operations without code changes
    """
    
    def __init__(self):
        self.operation_registry = {
            'join': self.execute_join,
            'filter': self.execute_filter,
            'aggregate': self.execute_aggregate,
            'clean': self.execute_clean,
            'derive': self.execute_derive,
            'pivot': self.execute_pivot,
            'unpivot': self.execute_unpivot,
            'union': self.execute_union,
            'rename': self.execute_rename,
            'select': self.execute_select,
            'sort': self.execute_sort,
            'sample': self.execute_sample,
        }
    
    def execute(self, operation_type: str, df: pd.DataFrame, config: Dict[str, Any],
                additional_inputs: Optional[Dict[str, pd.DataFrame]] = None) -> pd.DataFrame:
        """
        Execute a transformation operation
        
        Args:
            operation_type: Type of operation (join, filter, etc.)
            df: Input DataFrame (primary input)
            config: Operation-specific configuration
            additional_inputs: Additional DataFrames needed (for joins, unions)
        
        Returns:
            Transformed DataFrame
        """
        if operation_type not in self.operation_registry:
            raise ValueError(f"Unknown operation type: {operation_type}")
        
        operation_func = self.operation_registry[operation_type]
        
        try:
            logger.info(f"Executing {operation_type} operation with config: {config}")
            result_df = operation_func(df, config, additional_inputs or {})
            logger.info(f"{operation_type} completed: {len(result_df)} rows, {len(result_df.columns)} columns")
            return result_df
        except Exception as e:
            logger.error(f"Error executing {operation_type}: {str(e)}", exc_info=True)
            raise
    
    # ─── TRANSFORMATION OPERATIONS ────────────────────────────────────────────
    
    def execute_join(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Join operation: merge two datasets
        
        Config:
            join_type: inner|left|right|outer
            right_input: key of DataFrame in inputs dict
            left_key: column name in left df
            right_key: column name in right df
        """
        join_type = config.get('join_type', 'inner')
        right_key = config.get('right_input')
        left_on = config.get('left_key')
        right_on = config.get('right_key')
        
        if not right_key or right_key not in inputs:
            raise ValueError(f"Right input DataFrame '{right_key}' not found")
        
        right_df = inputs[right_key]
        
        # Pandas join type mapping
        how_map = {'inner': 'inner', 'left': 'left', 'right': 'right', 'outer': 'outer'}
        how = how_map.get(join_type, 'inner')
        
        result = pd.merge(df, right_df, left_on=left_on, right_on=right_on, how=how,
                         suffixes=('', '_right'))
        return result
    
    def execute_filter(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Filter operation: remove rows based on conditions
        
        Config:
            conditions: [
                {"column": "status", "operator": "equals", "value": "Active"},
                ...
            ]
            logic: "and"|"or"
        """
        conditions = config.get('conditions', [])
        logic = config.get('logic', 'and')
        
        if not conditions:
            return df
        
        # Build boolean mask for each condition
        masks = []
        for cond in conditions:
            column = cond['column']
            operator = cond['operator']
            value = cond.get('value')
            
            if column not in df.columns:
                logger.warning(f"Column '{column}' not found in DataFrame, skipping condition")
                continue
            
            if operator not in FILTER_OPERATORS:
                logger.warning(f"Unknown operator '{operator}', skipping condition")
                continue
            
            op_func = FILTER_OPERATORS[operator]
            mask = df[column].apply(lambda x: op_func(x, value))
            masks.append(mask)
        
        if not masks:
            return df
        
        # Combine masks based on logic
        if logic == 'or':
            final_mask = masks[0]
            for mask in masks[1:]:
                final_mask = final_mask | mask
        else:  # and
            final_mask = masks[0]
            for mask in masks[1:]:
                final_mask = final_mask & mask
        
        return df[final_mask]
    
    def execute_aggregate(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Aggregate operation: group and summarize
        
        Config:
            group_by: ["project", "category"]
            aggregations: [
                {"column": "amount", "function": "sum", "output_name": "total_amount"}
            ]
        """
        group_by = config.get('group_by', [])
        aggregations = config.get('aggregations', [])
        
        if not group_by:
            # No grouping - aggregate entire dataset
            grouped = df
        else:
            grouped = df.groupby(group_by)
        
        # Build aggregation dict
        agg_dict = {}
        for agg in aggregations:
            column = agg['column']
            function = agg['function']
            output_name = agg.get('output_name', f"{column}_{function}")
            
            if column not in df.columns:
                continue
            
            agg_dict[output_name] = (column, function)
        
        if group_by:
            result = grouped.agg(**agg_dict).reset_index()
        else:
            # Apply aggregations to entire DataFrame
            result_dict = {}
            for output_name, (column, function) in agg_dict.items():
                result_dict[output_name] = [getattr(df[column], function)()]
            result = pd.DataFrame(result_dict)
        
        return result
    
    def execute_clean(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Clean operation: remove duplicates, handle nulls
        
        Config:
            remove_duplicates: true/false
            drop_null_rows: ["column1", "column2"]
            fill_null_value: {"column3": 0, "column4": "N/A"}
        """
        result = df.copy()
        
        # Remove duplicates
        if config.get('remove_duplicates', False):
            result = result.drop_duplicates()
        
        # Drop rows with null in specific columns
        drop_null_cols = config.get('drop_null_rows', [])
        if drop_null_cols:
            result = result.dropna(subset=drop_null_cols)
        
        # Fill null values
        fill_values = config.get('fill_null_value', {})
        if fill_values:
            result = result.fillna(fill_values)
        
        return result
    
    def execute_derive(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Derive operation: create calculated columns
        
        Config:
            new_columns: [
                {
                    "name": "total_price",
                    "expression": "quantity * unit_price",
                    "data_type": "float"
                }
            ]
        """
        result = df.copy()
        new_columns = config.get('new_columns', [])
        
        for col_def in new_columns:
            name = col_def['name']
            expression = col_def['expression']
            data_type = col_def.get('data_type', 'str')
            
            try:
                # Evaluate expression using DataFrame columns
                result[name] = result.eval(expression)
                
                # Cast to specified type
                if data_type == 'int':
                    result[name] = result[name].astype(int)
                elif data_type == 'float':
                    result[name] = result[name].astype(float)
                elif data_type == 'str':
                    result[name] = result[name].astype(str)
                
            except Exception as e:
                logger.warning(f"Failed to derive column '{name}': {str(e)}")
        
        return result
    
    def execute_pivot(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Pivot operation: convert rows to columns
        
        Config:
            index: ["row_identifier"]
            columns: "category_column"
            values: "value_column"
            agg_func: "sum"|"mean"|"count"
        """
        index = config.get('index', [])
        columns = config.get('columns')
        values = config.get('values')
        agg_func = config.get('agg_func', 'sum')
        
        result = df.pivot_table(
            index=index,
            columns=columns,
            values=values,
            aggfunc=agg_func,
            fill_value=0
        ).reset_index()
        
        # Flatten column names
        result.columns = ['_'.join(map(str, col)).strip('_') if isinstance(col, tuple) else col 
                         for col in result.columns]
        
        return result
    
    def execute_unpivot(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Unpivot operation: convert columns to rows
        
        Config:
            id_vars: ["keep_column1", "keep_column2"]
            value_vars: ["pivot_col1", "pivot_col2"]
            var_name: "category"
            value_name: "value"
        """
        id_vars = config.get('id_vars', [])
        value_vars = config.get('value_vars')
        var_name = config.get('var_name', 'variable')
        value_name = config.get('value_name', 'value')
        
        result = pd.melt(
            df,
            id_vars=id_vars,
            value_vars=value_vars,
            var_name=var_name,
            value_name=value_name
        )
        
        return result
    
    def execute_union(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Union operation: stack datasets vertically
        
        Config:
            inputs: ["step_id1", "step_id2"]
            align_columns: true/false
        """
        input_keys = config.get('inputs', [])
        align_columns = config.get('align_columns', True)
        
        dfs_to_union = [df]
        for key in input_keys:
            if key in inputs:
                dfs_to_union.append(inputs[key])
        
        if align_columns:
            result = pd.concat(dfs_to_union, ignore_index=True, sort=False)
        else:
            result = pd.concat(dfs_to_union, ignore_index=True)
        
        return result
    
    def execute_rename(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Rename operation: rename columns
        
        Config:
            column_mapping: {
                "old_name1": "new_name1",
                "old_name2": "new_name2"
            }
        """
        column_mapping = config.get('column_mapping', {})
        result = df.rename(columns=column_mapping)
        return result
    
    def execute_select(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Select operation: choose specific columns
        
        Config:
            columns: ["col1", "col2", "col3"]
        """
        columns = config.get('columns', [])
        
        # Filter to existing columns only
        existing_cols = [col for col in columns if col in df.columns]
        
        if not existing_cols:
            logger.warning("No valid columns selected, returning empty DataFrame")
            return pd.DataFrame()
        
        return df[existing_cols]
    
    def execute_sort(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Sort operation: order by columns
        
        Config:
            sort_by: [
                {"column": "date", "ascending": false},
                {"column": "amount", "ascending": true}
            ]
        """
        sort_by = config.get('sort_by', [])
        
        if not sort_by:
            return df
        
        columns = [item['column'] for item in sort_by if item['column'] in df.columns]
        ascending = [item.get('ascending', True) for item in sort_by if item['column'] in df.columns]
        
        if not columns:
            return df
        
        return df.sort_values(by=columns, ascending=ascending)
    
    def execute_sample(self, df: pd.DataFrame, config: Dict, inputs: Dict) -> pd.DataFrame:
        """
        Sample operation: take random subset
        
        Config:
            sample_size: 1000 (number of rows)
            sample_fraction: 0.1 (fraction of dataset, alternative to sample_size)
            random_state: 42 (for reproducibility)
        """
        sample_size = config.get('sample_size')
        sample_fraction = config.get('sample_fraction')
        random_state = config.get('random_state', 42)
        
        if sample_size:
            n = min(sample_size, len(df))
            return df.sample(n=n, random_state=random_state)
        elif sample_fraction:
            return df.sample(frac=sample_fraction, random_state=random_state)
        else:
            # Default: sample 10%
            return df.sample(frac=0.1, random_state=random_state)
