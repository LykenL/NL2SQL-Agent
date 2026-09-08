import json
import time
from sqlalchemy import create_engine, inspect

# Simple in-memory schema cache: db_path -> (schema_json, timestamp)
_schema_cache = {}
_schema_cache_timestamp = {}

# Cache TTL in seconds (5 minutes default)
SCHEMA_CACHE_TTL = 300

def extract_db_schema(db_path: str = "sqlite:///company.db", force_refresh: bool = False) -> str:
    """
    Acts as a 'CT Scanner' for the database.
    It automatically scans all tables, columns, data types, and foreign keys.
    Returns a JSON string representing the database schema to be fed to the LLM.
    Uses an in-memory cache to avoid redundant scans within the cache TTL.
    """
    # Check cache first (unless force refresh)
    if not force_refresh and db_path in _schema_cache:
        elapsed = time.time() - _schema_cache_timestamp[db_path]
        if elapsed < SCHEMA_CACHE_TTL:
            return _schema_cache[db_path]
    
    try:
        engine = create_engine(db_path)
        # The inspector is SQLAlchemy's built-in database scanner
        inspector = inspect(engine)
        
        schema_info = {}
        
        # 1. Get all table names in the database
        table_names = inspector.get_table_names()
        
        for table in table_names:
            schema_info[table] = {
                "columns": [],
                "foreign_keys": []
            }
            
            # 2. Extract column names and their exact data types
            for column in inspector.get_columns(table):
                schema_info[table]["columns"].append({
                    "name": column['name'],
                    "type": str(column['type']) # e.g., INTEGER, VARCHAR
                })
                
            # 3. Extract foreign keys (CRUCIAL for LLM to know how to write JOINs)
            """
            fk is a dictionary that SQLAlchemy returns describing a single foreign key relationship in your database.

                It contains the following keys:
                - constrained_columns: a list of the column(s) in the current table that point outward (e.g., ['dept_id'])
                - referred_table: the name of the table being pointed to (e.g., 'departments')
                - referred_columns: a list of the column(s) in that target table (e.g., ['id'])
            """
            for fk in inspector.get_foreign_keys(table):
                schema_info[table]["foreign_keys"].append({
                    "from_column": fk['constrained_columns'][0],
                    "to_table": fk['referred_table'],
                    "to_column": fk['referred_columns'][0]
                })
                
        result = json.dumps(schema_info, indent=2)
        
        # Update cache
        _schema_cache[db_path] = result
        _schema_cache_timestamp[db_path] = time.time()
        
        return result
        
    except Exception as e:
        return f"Error extracting schema: {e}"

# test part
if __name__ == "__main__":
    # Test our scanner on the database we just created
    print("🔍 Testing db_reader.py (Scanning company.db)...\n")
    schema_json = extract_db_schema()
    print("=== 🩻 Extracted Database Schema ===")
    print(schema_json)
