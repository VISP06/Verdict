import os
import pandas as pd

dataset_dir = "dataset"
print("=== EXACT CSV SCHEMAS ===")
for file in sorted(os.listdir(dataset_dir)):
    if file.endswith(".csv"):
        path = os.path.join(dataset_dir, file)
        try:
            # Read only the header row to save time and memory
            df = pd.read_csv(path, nrows=0, skipinitialspace=True)
            columns = [c.strip() for c in df.columns]
            print(f"\n[ {file} ]")
            print(columns)
        except Exception as e:
            print(f"\n[ {file} ] - ERROR READING: {e}")