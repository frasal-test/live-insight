"""UI translations (web/src/locales): every language has the same keys, every key used in the code exists,
and every {placeholder} of a key is the same in all languages."""
import json
import re
from pathlib import Path

WEB = Path(__file__).parent.parent / "web" / "src"
LOCALES = {p.stem: json.loads(p.read_text()) for p in sorted((WEB / "locales").glob("*.json"))}
PLURAL = re.compile(r"_(zero|one|two|few|many|other)$")


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def test_at_least_english_and_italian():
    assert {"en", "it"} <= set(LOCALES)


def test_same_keys_in_every_language():
    base = set(LOCALES["en"])
    for lang, catalog in LOCALES.items():
        assert set(catalog) == base, f"{lang}: missing {base - set(catalog)}, extra {set(catalog) - base}"


def test_same_placeholders_in_every_language():
    for key, text in LOCALES["en"].items():
        for lang, catalog in LOCALES.items():
            assert placeholders(catalog[key]) == placeholders(text), f"{lang}:{key}"


def test_keys_used_in_the_code_exist():
    keys = {PLURAL.sub("", k) for k in LOCALES["en"]}
    used = set()
    for path in WEB.glob("*.tsx"):
        used |= set(re.findall(r"\bt[n]?\('([\w.]+)'", path.read_text()))
    assert used, "no t('...') calls found"
    assert used <= keys, f"keys used but not translated: {used - keys}"


BACKEND = Path(__file__).parent.parent / "liveinsight"
BACKEND_KEY = re.compile(r"""["']((?:errors|settings\.errors|preview|probe|models|grain|chat\.notice)\.[\w.]+)["']""")


def test_keys_sent_by_the_backend_exist():
    """liveinsight/messages.py: the backend sends keys, the UI writes them. Every literal key in the backend,
    plus the ones built at run time (date grains, turn notices), must be translated."""
    from liveinsight.engine.session import NOTICES
    from liveinsight.engine.spec import DateColumn
    keys = {PLURAL.sub("", k) for k in LOCALES["en"]}
    used = {k for path in BACKEND.rglob("*.py") for k in BACKEND_KEY.findall(path.read_text())}
    used |= {f"grain.{g}" for g in DateColumn.model_fields["grain"].annotation.__args__}
    used |= {f"chat.notice.{n}" for n in NOTICES}
    assert len(used) > 30, "the key pattern found too few keys"
    assert used <= keys, f"keys sent by the backend but not translated: {sorted(used - keys)}"
