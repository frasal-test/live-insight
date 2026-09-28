"""Reads the dataset embedded in a .dva (data.xlsx) to compute the expected values of the tests and golden set.

Applies the normalizations observed in OAC: strings are trimmed. Dates (Excel serials) become datetimes.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def load_rows(dva_path, entry="datasets/embedded2/data.xlsx", date_columns=("Order Date", "Ship Date")):
    x = zipfile.ZipFile(io.BytesIO(zipfile.ZipFile(dva_path).read(entry)))
    ss = ["".join(t.text or "" for t in si.iter("{%s}t" % NS["m"]))
          for si in ET.fromstring(x.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    raw = []
    for _, el in ET.iterparse(x.open(next(n for n in x.namelist() if n.startswith("xl/worksheets/sheet")))):
        if el.tag.endswith("}row"):
            r = {}
            for cell in el.findall("m:c", NS):
                v = cell.find("m:v", NS)
                if v is not None:
                    col = re.match(r"[A-Z]+", cell.get("r")).group()
                    r[col] = ss[int(v.text)] if cell.get("t") == "s" else float(v.text)
            raw.append(r)
            el.clear()
    header = raw[0]
    rows = []
    for r in raw[1:]:
        row = {}
        for col, name in header.items():
            v = r.get(col)
            if isinstance(v, str):
                v = v.strip()
            if name in date_columns and isinstance(v, float):
                v = datetime(1899, 12, 30) + timedelta(days=v)
            row[name] = v
        rows.append(row)
    return rows
