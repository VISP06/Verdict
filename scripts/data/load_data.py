import os
import pandas as pd

DATASET_DIR = "dataset"

def check_dataset_files():
    required_files = [
        "requests.csv",
        "sample_requests.csv",
        "financial_profiles.csv",
        "financial_events.csv",
        "exchange_rates.csv",
        "request_payment_options.csv",
        "messages.csv",
        "images.csv",
        "output.csv"
    ]
    
    print("--- Checking Dataset Folder ---")
    for file in required_files:
        path = os.path.join(DATASET_DIR, file)
        if os.path.exists(path):
            df = pd.read_csv(path)
            print(f"✓ Found {file:<30} (Rows: {len(df)})")
        else:
            print(f"✗ Missing {file:<28} (Place inside dataset/)")

if __name__ == "__main__":
    check_dataset_files()