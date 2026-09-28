"""Terminal chat with OAC data (M4): the MVP flow without the web UI.

Usage:  uv run python -m liveinsight.cli [--model openai:gpt-5.6-luna] [--dataset "Retail Orders"]

Commands (they do not go to the model and cost no tokens):
          /visuals           lists the proposed visuals (* = pinned)
          /show N            shows the preview data of visual N
          /keep N [title]    pins visual N to the workbook, with a new title if given
          /drop N            removes it from the workbook
          /generate Name     writes out/<Name>.dva with the pinned visuals
          /cost              tokens and cost of the session
          /help              this list
          /quit              exits (also /exit)
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from liveinsight.api import default_skeleton
from liveinsight.engine.platform import StructuralError
from liveinsight.engine.session import NOTICES, Session
from liveinsight.llm import make_model
from liveinsight.messages import UiError
from liveinsight.platforms.oac.auth import OacToken
from liveinsight.platforms.oac.catalog import safe_filename
from liveinsight.platforms.oac.mcp import OacMcp
from liveinsight.platforms.oac.platform import OacPlatform
from liveinsight.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
GREY, BOLD, RESET = "\033[90m", "\033[1m", "\033[0m"


def choose_dataset(platform, hint):
    items = platform.datasets(hint or "*")
    if not items:
        sys.exit("no visible dataset")
    if len(items) == 1:
        return items[0]
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it['name']}  {GREY}({it['folder']}){RESET}")
    return items[int(input("Dataset no.: ")) - 1]


def table(rows, limit=20):
    if not rows:
        return "  (no rows)"
    keys = list(rows[0])
    fmt = lambda v: f"{v:,.2f}" if isinstance(v, float) else str(v)
    widths = [max(len(k), *(len(fmt(r.get(k))) for r in rows[:limit])) for k in keys]
    lines = ["  " + "  ".join(k.ljust(w) for k, w in zip(keys, widths))]
    lines += ["  " + "  ".join(fmt(r.get(k)).rjust(w) if isinstance(r.get(k), (int, float)) else fmt(r.get(k)).ljust(w)
                               for k, w in zip(keys, widths)) for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"  … {len(rows) - limit} more rows")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--dataset")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    settings = Settings.load(ROOT / ".secrets/settings.json")      # as the app: Settings, then .env
    model = make_model(args.model or settings.model, settings.keys, oci_compartment=settings.oci_compartment,
                       base_url=settings.custom_base_url)
    token = OacToken.from_file(os.environ["OAC_URL"], ROOT / os.environ["OAC_TOKENS"])
    with OacMcp(os.environ["OAC_URL"], token) as mcp:
        platform = OacPlatform(mcp, default_skeleton(),
                               [n.strip() for n in os.environ.get("TEMPLATE_WORKBOOKS", "").split(",") if n.strip()])
        ds_item = choose_dataset(platform, args.dataset)
        source = platform.source(ds_item["xsa"], ds_item["name"])
        ds = source.dataset
        s = Session(source, model)
        print(f"{BOLD}{ds_item['name']}{RESET}: {len(ds.columns)} columns · model {model.provider}:{model.model}\n"
              f"Ask a question about the data. Commands: /visuals /show N /keep N /drop N /generate Name /cost /help /quit\n")
        while True:
            try:
                q = input(f"{BOLD}> {RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                continue
            if q.startswith("/"):
                cmd, _, rest = q.partition(" ")
                try:
                    if cmd in ("/quit", "/exit"):
                        break
                    elif cmd == "/visuals":
                        for p in s.proposals:
                            print(f"  {'*' if p.pinned else ' '} {p.n}. [{p.visual.kind}] {p.visual.title}  {GREY}{len(p.rows)} rows{RESET}")
                    elif cmd in ("/keep", "/drop", "/show"):
                        n, _, title = rest.partition(" ")
                        if not n.isdigit():
                            raise ValueError("the visual number is needed: /visuals for the list")
                        if cmd == "/show":
                            print(table(s.proposal(int(n)).rows))
                        else:
                            p = s.pin(int(n), title or None) if cmd == "/keep" else s.unpin(int(n))
                            print(f"  {'pinned' if p.pinned else 'removed'}: {p.visual.title}")
                    elif cmd == "/generate":
                        name = rest.strip() or "Live Insight"
                        out = ROOT / "out" / (safe_filename(name) + ".dva")
                        out.parent.mkdir(exist_ok=True)
                        try:
                            platform.target().package(s.pinned_spec(name).model_dump_json(), out)
                            print(f"  written {out.relative_to(ROOT)}  (checks passed)")
                        except StructuralError as e:
                            print(f"  written {out.relative_to(ROOT)}  PROBLEMS: {e.problems}")
                        except UiError as e:                     # no skeleton .dva configured
                            print(f"  {e}")
                    elif cmd == "/cost":
                        u, c = s.usage, s.cost()
                        print(f"  token: input {u.input_tokens:,} · in cache {u.cached_input_tokens:,} · output {u.output_tokens:,}"
                              + (f" · cost ${c:.4f}" if c is not None else ""))
                    else:
                        print(__doc__.split("Commands", 1)[1].split("\n", 1)[1].rstrip())
                except (ValueError, IndexError) as e:
                    print(f"  {e}")
                continue
            try:
                turn = s.ask(q)
            except Exception as e:                  # provider or network error: the session stays open
                last_user = max(i for i, m in enumerate(s.messages) if m.role == "user")
                s.messages = s.messages[:last_user]
                print(f"  model error: {e}\n  (the question was cancelled, you can try again)\n")
                continue
            for e in turn.events:
                if e.kind == "query":
                    print(f"{GREY}  ↳ query ({e.detail['rows']} rows): {e.detail['query'][:140]}{RESET}")
                elif e.kind == "visual":
                    p = s.proposals[e.detail["n"] - 1]
                    print(f"{GREY}  ↳ visual {p.n} [{p.visual.kind}] {p.visual.title}{RESET}\n{table(p.rows, 8)}")
                else:
                    print(f"{GREY}  ↳ error in {e.detail['tool']}: {e.detail['error'][:200]}{RESET}")
            cost = model.price.cost(turn.usage) if model.price else None
            text = "\n".join(t for t in (turn.text, NOTICES.get(turn.notice, "")) if t)
            print(f"\n{text}\n{GREY}  {turn.seconds:.1f}s · {turn.steps} steps"
                  + (f" · ${cost:.4f}" if cost is not None else "") + f"{RESET}\n")


if __name__ == "__main__":
    main()
