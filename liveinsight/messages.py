"""Texts for the UI, without the words: the backend never writes UI prose.

Two forms, both resolved by the frontend with web/src/locales/*.json in the language the user picked:
- msg(key, **params): a message, {"key": ..., "params": {...}}, for notes, errors and notices;
- token(key): "⟦key⟧" inside a string the frontend does not build itself (Vega-Lite titles, column labels):
  the frontend replaces every token before drawing, so a language switch also translates what is already shown.
Every key used here must exist in every language file.

What the model reads is not here: it is English, in prompt.py and session.py.
"""
TOKEN_OPEN, TOKEN_CLOSE = "⟦", "⟧"


def msg(key: str, **params) -> dict:
    return {"key": key, "params": params}


def token(key: str) -> str:
    return f"{TOKEN_OPEN}{key}{TOKEN_CLOSE}"


class UiError(ValueError):
    """An error the user sees: English text for logs and tests, key and params for the UI (see msg)."""

    def __init__(self, text: str, key: str, **params):
        super().__init__(text)
        self.key, self.params = key, params

    def message(self) -> dict:
        return msg(self.key, **self.params)
