import csv
from pathlib import Path

input_folder = Path(".")
output_path = input_folder / "msp_final.csv"

csv_files = sorted(f for f in input_folder.glob("*.csv") if f.name != "msp_final.csv")
print(f"Found {len(csv_files)} CSV files")

fieldnames = [
    "month", "region", "okved",
    "active_micro", "active_small", "active_medium",
    "closed_micro", "closed_small", "closed_medium",
    "new_micro", "new_small", "new_medium",
]

with open(output_path, "w", newline="", encoding="utf-8-sig") as out:
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()

    for i, csv_file in enumerate(csv_files):
        with open(csv_file, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            writer.writerows(rows)
        print(f"[{i+1}/{len(csv_files)}] {csv_file.name} → {len(rows)} rows")

print(f"\nDone → {output_path}")
