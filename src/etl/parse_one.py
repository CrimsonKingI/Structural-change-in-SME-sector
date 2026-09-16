"Использовать: в Терминале писать python3 parse_one.py Путь к файлу с данными"

import sys
import csv
from lxml import etree


def parse_xml(xml_path):
    records = []
    for event, elem in etree.iterparse(xml_path, tag="Документ"):
        d, m, y = elem.get("ДатаСост").split(".")
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
            records.append({"inn": inn, "month": f"{y}-{m}", "region": region, "okved": okved, "cat": cat})
    return records


def save_csv(records, output_path):
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["inn", "month", "region", "okved", "cat"])
        writer.writeheader()
        writer.writerows(records)


if len(sys.argv) < 2:
    print("Использование: python3 parse_one.py <путь к XML-файлу с данными>")
    sys.exit(1)

xml_path = sys.argv[1]
records = parse_xml(xml_path)
save_csv(records, xml_path.replace(".xml", ".csv"))
print(f"{len(records)} records")
