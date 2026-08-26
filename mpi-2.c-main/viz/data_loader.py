import json
import pandas as pd
from typing import List, Dict, Any

class TraceLoader:
    """
    Handles loading and parsing of Chrome Trace Format (JSON) files.
    Follows SRP (Single Responsibility Principle).
    """
    
    def __init__(self, filepath: str):
        self.filepath = filepath

    def load_raw_data(self) -> List[Dict[str, Any]]:
        """Reads the JSON trace file."""
        try:
            with open(self.filepath, 'r') as f:
                content = f.read().strip()
                # Handle cases where the trace might be incomplete or missing the closing bracket
                if content.endswith(','):
                    content = content[:-1]
                if not content.endswith(']'):
                    content += ']'
                return json.loads(content)
        except Exception as e:
            print(f"Error loading trace file: {e}")
            return []

    def get_dataframe(self) -> pd.DataFrame:
        """Converts raw trace events into a pandas DataFrame for analysis."""
        data = self.load_raw_data()
        if not data:
            return pd.DataFrame()

        # Filter out empty or invalid events
        events = [e for e in data if isinstance(e, dict) and 'ts' in e]
        
        df = pd.DataFrame(events)
        
        # Convert timestamps from microseconds to milliseconds for better display
        if not df.empty:
            df['ts_ms'] = df['ts'] / 1000.0
            
        return df

class LogLoader:
    """
    Handles reading the simulation log files.
    """
    def __init__(self, filepath: str):
        self.filepath = filepath

    def read_logs(self) -> str:
        try:
            with open(self.filepath, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return "Log file not found."
