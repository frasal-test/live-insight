"""Codec for the content/*.arc file of an Oracle Analytics .dva (worked out by observation).

Structure (zlib-decompressed):
  u32 magic (16)
  4 x [u32 len + bytes]   preamble: version, host, CatalogPhysicalPath (JSON), account (JSON)
  repeated:
    u16 0 + u32 len + JSON   header of the catalog object (ACL, ItemName, ItemType, ...)
    u16 1 + u64 len + bytes  body of the object (optional; folders have none)
  u16 2                     end
"""
import struct, json

def parse(buf):
    p = 0
    def take(fmt):
        nonlocal p; v = struct.unpack_from(fmt, buf, p)[0]; p += struct.calcsize(fmt); return v
    def blob(n):
        nonlocal p; b = buf[p:p+n]; p += n; return b
    arc = {"magic": take("<I"), "preamble": [blob(take("<I")) for _ in range(4)], "items": []}
    while True:
        tag = take("<H")
        if tag == 0:
            raw = blob(take("<I"))
            arc["items"].append({"header": json.loads(raw), "header_raw": raw, "body": None})
        elif tag == 1:
            arc["items"][-1]["body"] = blob(take("<Q"))
        elif tag == 2:
            break
        else:
            raise ValueError(f"unknown tag {tag} at offset {p-2}")
    if p != len(buf):
        raise ValueError(f"bytes not consumed: {len(buf)-p}")
    return arc

def serialize(arc):
    out = [struct.pack("<I", arc["magic"])]
    for b in arc["preamble"]:
        out += [struct.pack("<I", len(b)), b]
    for it in arc["items"]:
        h = it.get("header_raw") or json.dumps(it["header"]).encode()
        out += [struct.pack("<HI", 0, len(h)), h]
        if it["body"] is not None:
            out += [struct.pack("<HQ", 1, len(it["body"])), it["body"]]
    out.append(struct.pack("<H", 2))
    return b"".join(out)
