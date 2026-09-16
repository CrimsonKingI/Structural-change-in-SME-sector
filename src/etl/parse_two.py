"Использовать: в Терминале писать python3 parse_two.py путь/к/папке/прошлого/месяца путь/к/папке/настоящего/месяца"

import sys
import csv
from pathlib import Path
from lxml import etree
from collections import defaultdict


def parse_folder(folder_path):
    data = {}
    month = None

    for xml_file in Path(folder_path).glob("*.xml"):
        try:
            for event, elem in etree.iterparse(str(xml_file), tag="Документ"):
                if month is None:
                    d, m, y = elem.get("ДатаСост").split(".")
                    month = f"{y}-{m}"

                inn = None
                region = None
                okved = None
                cat = elem.get("КатСубМСП")

                for child in elem:
                    if child.tag == "ИПВклМСП":
                        inn = child.get("ИННФЛ")
                    elif child.tag == "ОргВклМСП":
                        inn = child.get("ИННЮЛ")
                    elif child.tag == "СведМН":
                        code = child.get("КодРегион", "").strip()
                        region = code.zfill(2) if code and code != "0" else None
                    elif child.tag == "СвОКВЭД":
                        for sub in child:
                            if sub.tag == "СвОКВЭДОсн":
                                okved = sub.get("КодОКВЭД")[:2]
                                break

                if inn and region and okved and cat:
                    data[inn] = (region, okved, cat)

                elem.clear()
        except etree.XMLSyntaxError as e:
            print(f"  Skipping broken file: {xml_file.name} ({e})")
            continue

    return data, month


def aggregate(prev, curr, month):
    counts = defaultdict(lambda: defaultdict(int))

    for inn, (region, okved, cat) in curr.items():
        counts[(region, okved)][f"active_{cat}"] += 1

    for inn, (region, okved, cat) in prev.items():
        if inn not in curr:
            counts[(region, okved)][f"closed_{cat}"] += 1

    for inn, (region, okved, cat) in curr.items():
        if inn not in prev:
            counts[(region, okved)][f"new_{cat}"] += 1

    rows = []
    for (region, okved), values in sorted(counts.items()):
        rows.append({
            "month":         month,
            "region":        region,
            "okved":         okved,
            "active_micro":  values.get("active_1", 0),
            "active_small":  values.get("active_2", 0),
            "active_medium": values.get("active_3", 0),
            "closed_micro":  values.get("closed_1", 0),
            "closed_small":  values.get("closed_2", 0),
            "closed_medium": values.get("closed_3", 0),
            "new_micro":     values.get("new_1", 0),
            "new_small":     values.get("new_2", 0),
            "new_medium":    values.get("new_3", 0),
        })
    return rows


def save_csv(rows, output_path):
    fieldnames = [
        "month", "region", "okved",
        "active_micro", "active_small", "active_medium",
        "closed_micro", "closed_small", "closed_medium",
        "new_micro", "new_small", "new_medium",
    ]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if len(sys.argv) < 3:
    print("Использование: python3 parse_two.py <папка прошлого месяца> <папка текущего месяца>")
    sys.exit(1)

prev_path = sys.argv[1]
curr_path = sys.argv[2]

print(f"Parsing previous month: {prev_path}")
prev, _ = parse_folder(prev_path)
print(f"  {len(prev)} records")

print(f"Parsing current month: {curr_path}")
curr, month = parse_folder(curr_path)
print(f"  {len(curr)} records")

rows = aggregate(prev, curr, month)
output_path = f"{month[5:]}-{month[:4]}.csv"
save_csv(rows, output_path)
print(f"Done: {len(rows)} rows → {output_path}")
