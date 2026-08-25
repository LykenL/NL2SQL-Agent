import pandas as pd
import json

def extract_csv_metadata(file_path: str, num_sample_rows: int = 3) -> str:
    """
    Reads a CSV file intelligently to extract metadata for the LLM.
    Instead of sending the whole file, we only send:
    1. Column names
    2. Data types
    3. The first few sample rows
    
    This saves tokens, reduces latency, and protects data privacy.
    """
    try:
        # We only read the first few rows to save memory and time
        # This is very important when dealing with 10GB+ CSV files!
        df_sample = pd.read_csv(file_path, nrows=num_sample_rows)
        
        # 1. Extract columns and their data types
        dtypes_dict = {col: str(dtype) for col, dtype in df_sample.dtypes.items()}
        
        # 2. Extract sample data as a list of dictionaries
        sample_data = df_sample.to_dict(orient="records")
        
        # 3. Assemble the metadata summary
        metadata = {
            "file_path": file_path,
            "columns": list(df_sample.columns),
            "dtypes": dtypes_dict,
            "sample_data": sample_data
        }
        
        # Return as a nicely formatted JSON string
        return json.dumps(metadata, indent=2, ensure_ascii=False)
        
    except FileNotFoundError:
        return f"Error: File '{file_path}' not found."
    except Exception as e:
        return f"Error reading CSV: {str(e)}"

if __name__ == "__main__":
    # Test our data reader with the dummy data
    print("Testing data_reader.py...")
    metadata_json = extract_csv_metadata("dummy_data.csv")
    print("\n=== Extracted Metadata for LLM ===")
    print(metadata_json)
