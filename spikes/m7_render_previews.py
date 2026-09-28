"""M7 — The previews drawn without the frontend: real data from OAC, no model (no LLM token).

For every visual kind it builds the proposal with Session.propose_visual (the app's own query), makes the preview
(liveinsight/engine/preview.py) and renders it as PNG with vl-convert. Used for the eraser test and the collision
test of the tufte-viz skill before writing the UI.

Usage:  uv run python spikes/m7_render_previews.py [folder]
"""
import json
import os
import sys
from pathlib import Path

import vl_convert as vlc
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liveinsight.platforms.oac.catalog import describe                                        # noqa: E402
from liveinsight.platforms.oac.li_dva import Workbook                                         # noqa: E402
from liveinsight.platforms.oac.auth import OacToken                                       # noqa: E402
from liveinsight.platforms.oac.mcp import OacMcp                                          # noqa: E402
from liveinsight.engine.session import Session                                         # noqa: E402

C = {"Sales": {"kind": "column", "id": "Sales", "source": "Sales"},
     "Profit": {"kind": "column", "id": "Profit", "source": "Profit"},
     "Qty": {"kind": "column", "id": "Qty", "source": "Quantity Ordered"},
     "Seg": {"kind": "column", "id": "Seg", "source": "Customer Segment"},
     "Sub": {"kind": "column", "id": "Sub", "source": "Product Sub Category"},
     "Cat": {"kind": "column", "id": "Cat", "source": "Product Category"},
     "City": {"kind": "column", "id": "City", "source": "City"},
     "Prio": {"kind": "column", "id": "Prio", "source": "Order Priority"},
     "Q": {"kind": "date", "id": "Q", "source": "Order Date", "grain": "quarter"},
     "Y": {"kind": "date", "id": "Y", "source": "Order Date", "grain": "year"},
     "AOV": {"kind": "calc", "id": "AOV", "caption": "Average order value", "expression": "{Sales} / (COUNT(DISTINCT {Order ID}))"}}
CASES = [
    ("bar", "Corporate outsells the other segments", {"measures": ["Sales"], "detail": ["Seg"]}),
    ("bar", "Every segment loses money, Corporate the most", {"measures": ["Profit"], "detail": ["Seg"]}),
    ("hbar", "Paper is the most ordered sub-category", {"measures": ["Qty"], "detail": ["Sub"]}),
    ("line", "Le vendite crescono con un picco ogni quarto trimestre", {"measures": ["Sales"], "detail": ["Q"]}),
    ("area", "Il profitto trimestrale resta negativo", {"measures": ["Profit"], "detail": ["Q"]}),
    ("scatter", "Sales and quantity by sub-category", {"measures": ["Sales", "Qty"], "detail": ["Sub"]}),
    ("radar", "Average order value by priority", {"measures": ["AOV"], "detail": ["Prio"]}),
    ("boxplot", "Sales by category", {"measures": ["Sales"], "detail": ["Cat"]}),
    ("map", "Quantity by city", {"detail": ["City"], "size": ["Qty"]}),
    ("table", "Segments: sales and quantity", {"row": ["Seg", "Sales", "Qty"]}),
    ("pivot", "Sales by priority and year", {"row": ["Prio"], "col": ["Y"], "measures": ["Sales"]}),
    ("tile", "Total quantity", {"measures": ["Qty"], "detail": ["Seg"]}),
    # M3c
    ("line", "Sales by year: Corporate pulls away from the other segments", {"measures": ["Sales"], "detail": ["Y"], "color": ["Seg"]}),
    ("line", "Profit by year, by category", {"measures": ["Profit"], "detail": ["Y"], "col": ["Cat"]}),
    ("bar", "Sales and profit by category", {"measures": ["Sales", "Profit"], "detail": ["Cat"]}),
    ("bar", "Quantity by priority and category", {"measures": ["Qty"], "detail": ["Prio"], "color": ["Cat"]}),
    ("hbar", "Telephones and Communication sells the most", {"measures": ["Sales"], "detail": ["Sub"]}, "desc"),
]


class NoModel:
    provider, model, price = "none", "none", None


def main(out_dir):
    load_dotenv(ROOT / ".env")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    token = OacToken.from_file(os.environ["OAC_URL"], ROOT / os.environ["OAC_TOKENS"])
    xsa = Workbook(str(ROOT / "dva-lab/templates/Examples.dva")).source
    with OacMcp(os.environ["OAC_URL"], token) as mcp:
        s = Session(mcp, NoModel(), describe(mcp, xsa), "Retail Orders FS")
        for i, (kind, title, roles, *sort) in enumerate(CASES, 1):
            ids = list(dict.fromkeys(c for cols in roles.values() for c in cols))
            p = s.propose_visual(kind, title, roles, [C[c] for c in ids], sort=sort[0] if sort else None)
            prev = s.proposal_json(p)["preview"]
            name = f"{i:02d}_{kind}"
            if prev["renderer"] == "vega":
                spec = json.loads(json.dumps(prev["spec"]).replace('"width": "container"', '"width": 520'))
                spec["title"] = title                         # in the UI the title sits in the card
                try:
                    png = vlc.vegalite_to_png(spec, scale=2, format_locale="it-IT")
                except ValueError as e:
                    (out / f"{name}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))
                    print(f"{name}: ERROR {str(e).splitlines()[1][:150]} (spec in {name}.json)")
                    continue
                (out / f"{name}.png").write_bytes(png)
                print(f"{name}: PNG" + (f" · note: {prev['note']}" if prev.get("note") else ""))
            else:
                print(f"{name}: {prev['renderer']} " + json.dumps({k: v for k, v in prev.items() if k not in ('rows',)},
                                                                   ensure_ascii=False)[:200])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "out/previews")
