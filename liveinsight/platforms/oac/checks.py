"""Structural checks on a workbook definition and on a generated .dva.

The rules of FORMATO-DVA.md turned into checks: the app runs them before offering a file, the tests run them on
every visual kind. Every function returns a list of problems (empty = ok).
"""
import json
import re
import zipfile
import zlib

from liveinsight.platforms.oac.arc_codec import parse
from liveinsight.platforms.oac.li_dva import LOCAL, logical_roles

COLUMN_REF = re.compile(r'"(?:columnID|valueColumnID)":\s*"([^"]+)"')


def _refs(obj):
    return set(COLUMN_REF.findall(json.dumps(obj)))


def _global(ids):
    """Workbook columns: without the visuals' local columns and the internal placeholders (__X__)."""
    return {c for c in ids if not c.startswith((LOCAL, "__"))}


def definition_problems(defn, template_columns=()):
    """Problems of a definition (_projectdefn).

    `template_columns`: the column ids of the templates, which must not survive in the visuals.
    """
    problems = []
    columns = {c["columnID"] for c in defn["criteria"]["columns"]["children"]}
    views = [v for v in defn["views"]["children"] if v["type"] == "saw:pluginView"]
    canvases = [v for v in defn["views"]["children"] if v["type"] == "saw:canvas"]
    layouts = {l["name"]: l for l in defn["layouts"]["children"]}
    placed = set()
    for canvas in canvases:
        layout = layouts.get(canvas["rootLayoutName"])
        if layout is None:
            problems.append(f"{canvas['viewName']}: layout {canvas['rootLayoutName']} missing")
            continue
        placed |= {c["content"]["viewName"] for c in layout["children"]}

    for v in views:
        name = v["viewName"]
        used = _refs(v)
        local = {c["columnID"] for c in v.get("viewConfig", {}).get("settings", {}).get("viz:columns", {}).get("columns", [])}
        orphan = _global(used) - columns
        if orphan:
            problems.append(f"{name}: columns not defined in the workbook {sorted(orphan)}")
        foreign = {u for u in used if u.startswith(LOCAL) and not u.startswith(f"{LOCAL}{name}.")}
        if foreign:
            problems.append(f"{name}: local columns of another visual {sorted(foreign)}")
        missing_local = {u for u in used if u.startswith(f"{LOCAL}{name}.")} - local
        if missing_local:
            problems.append(f"{name}: local columns used but not defined {sorted(missing_local)}")
        leftovers = _global(used) & set(template_columns) - columns
        if leftovers:
            problems.append(f"{name}: template leftovers {sorted(leftovers)}")

        # the binding is there twice: the logical and the physical model must name the same columns
        logical = _global({c for cols in logical_roles(v).values() for c in cols})
        physical = _global(_refs({k: x for k, x in v.items() if k != "dataModels"}) |
                           _refs([{k: x for k, x in dm.items() if k != "logicalDataModel"}
                                  for dm in v.get("dataModels", {}).get("children", [])]))
        if logical != physical:
            problems.append(f"{name}: inconsistent binding, logical only {sorted(logical - physical)}, "
                            f"physical only {sorted(physical - logical)}")
        if name not in placed:
            problems.append(f"{name}: in no canvas")
        if not v.get("viewCaption", {}).get("caption", {}).get("text"):
            problems.append(f"{name}: no title")

    unused = {n for n in placed if n not in {v["viewName"] for v in views}}
    if unused:
        problems.append(f"layout with visuals that do not exist {sorted(unused)}")
    return problems


def dva_problems(path, name, forbidden=()):
    """Problems of a .dva: package, name, no embedded dataset, definition.

    `forbidden`: strings that must not appear (e.g. the skeleton's name).
    """
    problems = []
    z = zipfile.ZipFile(path)
    if z.testzip() is not None:
        return ["corrupted ZIP"]
    arc_name = next((n for n in z.namelist() if n.startswith("content/") and n.endswith(".arc")), None)
    if arc_name is None:
        return ["content/*.arc missing"]
    raw = zlib.decompress(z.read(arc_name))
    items = parse(raw)["items"]
    defn = json.loads(next(i for i in items if i["header"]["ItemName"] == "_projectdefn")["body"])
    folder = next(i for i in items if i["header"]["ItemType"] == 1)
    if folder["header"]["ItemName"] != name:
        problems.append(f"name in the archive '{folder['header']['ItemName']}', expected '{name}'")
    manifests = {n: z.read(n).decode("utf-8") for n in ("content/MANIFEST.MF", "META-INF/MANIFEST.MF")}
    unfolded = {n: m.replace("\r\n ", "").replace("\n ", "") for n, m in manifests.items()}
    if f"Object1: {name}" not in unfolded["content/MANIFEST.MF"]:
        problems.append("content/MANIFEST.MF without the workbook name")
    if f"Bundle-Application-Name: {name}" not in unfolded["META-INF/MANIFEST.MF"]:
        problems.append("META-INF/MANIFEST.MF without the workbook name")
    embedded = [n for n in z.namelist() if n.startswith(("datasets/embedded", "datasets/datamodel")) and not n.endswith("/")]
    if embedded:
        problems.append(f"the .dva holds dataset definitions {embedded}: on import OAC would create a copy")
    blob = raw + "".join(manifests.values()).encode()
    for s in forbidden:
        if s.encode() in blob:
            problems.append(f"leftover '{s}' in the package")
    return problems + definition_problems(defn)
