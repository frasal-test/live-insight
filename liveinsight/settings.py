"""App settings (Settings page): OAC URL, model, API keys of the LLM providers, custom server URL.

Saved in .secrets/settings.json (outside git, permissions 600), they win over .env, which stays the starting value
(and the only source for the CLI and the golden set). Keys stay in the backend: the browser only learns whether each
one is set.

Only the fields the app uses: for OAC the URL is enough. OCI Generative AI also needs the compartment (OCID): the
app uses the native Generative AI API (on-demand chat), the only one where Gemini and Cohere call tools
(liveinsight/llm/oci_native.py), and it wants the compartment in every request. The OCI region is in .env
(OCI_REGION). The "custom" provider is your own OpenAI-compatible server: its URL, an optional key, any model ID.

Proposed models are suggestions: any model ID of the provider can be typed, and the tool-calling check
(liveinsight.llm.probe) tells whether it really works.
"""
import json
import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path

from liveinsight.llm import KEY_ENV, KEY_OPTIONAL, PROVIDERS
from liveinsight.messages import UiError


class SettingsError(UiError):
    pass


def normalize_oac_url(url: str) -> str:
    """Only the origin (https://host): pasting an OAC link with a path gives the same."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise SettingsError("the OAC URL must start with https://", "settings.errors.oacUrl")
    return f"https://{parts.netloc}"


def check_model(spec: str) -> str:
    provider, _, model = spec.partition(":")
    if provider not in PROVIDERS:
        raise SettingsError(f"unknown provider: {provider!r}", "settings.errors.provider", provider=provider)
    allowed = r"[\w.\-:/]+" if provider == "custom" else r"[\w.\-]+(@\w+)?"   # e.g. qwen2.5:14b, org/model
    if not re.fullmatch(allowed, model):
        raise SettingsError("model missing (e.g. openai.gpt-oss-120b)", "settings.errors.model")
    return spec


def check_compartment(ocid: str | None) -> str | None:
    ocid = (ocid or "").strip()
    if ocid and not re.fullmatch(r"ocid1\.(compartment|tenancy)\.[\w.\-]+", ocid):   # the root is the tenancy
        raise SettingsError("the compartment OCID starts with ocid1.compartment. (or ocid1.tenancy. for the root)",
                            "settings.errors.compartmentFormat")
    return ocid or None


def check_base_url(url: str | None) -> str | None:
    """The custom server's URL: http(s), e.g. http://localhost:11434/v1 (Ollama)."""
    url = (url or "").strip().rstrip("/")
    if url and not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
        raise SettingsError("the server URL must start with http:// or https://", "settings.errors.baseUrlFormat")
    return url or None


@dataclass
class Settings:
    oac_url: str | None = None
    model: str | None = None                                  # "provider:model[@effort]", like LLM_MODEL
    keys: dict[str, str] = field(default_factory=dict)        # provider -> API key
    oci_compartment: str | None = None                       # OCI compartment OCID (native Generative AI API)
    custom_base_url: str | None = None                       # the custom provider's OpenAI-compatible server

    @classmethod
    def load(cls, path: Path | None, env=os.environ) -> "Settings":
        saved = json.loads(path.read_text()) if path and path.exists() else {}
        keys = {p: env[name] for p, name in KEY_ENV.items() if env.get(name)}
        keys |= {p: k for p, k in saved.get("keys", {}).items() if k}
        return cls(oac_url=saved.get("oac_url") or env.get("OAC_URL") or None,
                   model=saved.get("model") or env.get("LLM_MODEL") or None, keys=keys,
                   oci_compartment=saved.get("oci_compartment") or env.get("OCI_COMPARTMENT_ID") or None,
                   custom_base_url=saved.get("custom_base_url") or env.get("CUSTOM_LLM_BASE_URL") or None)

    def save(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(asdict(self), f, indent=2)
        os.chmod(tmp, 0o600)                                  # even if the .tmp existed already
        os.replace(tmp, path)

    def with_model(self, model: str, api_key: str | None = None, oci_compartment: str | None = None,
                   custom_base_url: str | None = None) -> "Settings":
        """The validated model. The key is the model's provider's; empty = the saved one stays."""
        model = check_model(model.strip())
        provider = model.partition(":")[0]
        keys = dict(self.keys)
        if api_key and api_key.strip():
            keys[provider] = api_key.strip()
        if not keys.get(provider) and provider not in KEY_OPTIONAL:
            raise SettingsError(f"{PROVIDERS[provider]} API key missing", "settings.errors.apiKey", provider=PROVIDERS[provider])
        compartment = self.oci_compartment
        if provider == "oci":
            compartment = check_compartment(oci_compartment) or compartment
            if not compartment:
                raise SettingsError("OCI compartment (OCID) missing", "settings.errors.compartment")
        base_url = self.custom_base_url
        if provider == "custom":
            base_url = check_base_url(custom_base_url) or base_url
            if not base_url:
                raise SettingsError("server URL missing for the OpenAI-compatible provider", "settings.errors.baseUrl")
        return Settings(self.oac_url, model, keys, compartment, base_url)

    def updated(self, oac_url: str, model: str, api_key: str | None = None,
                oci_compartment: str | None = None, custom_base_url: str | None = None) -> "Settings":
        """New validated settings (OAC URL included)."""
        new = self.with_model(model, api_key, oci_compartment, custom_base_url)
        new.oac_url = normalize_oac_url(oac_url)
        return new

    def public(self) -> dict:
        """What the browser may see: never the keys, only whether they are set."""
        return {"oac_url": self.oac_url, "model": self.model, "oci_compartment": self.oci_compartment,
                "custom_base_url": self.custom_base_url,
                "keys_set": {p: bool(self.keys.get(p)) for p in PROVIDERS}}
