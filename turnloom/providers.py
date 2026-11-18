"""Discover models and bind a Turnloom session to one selected provider."""

import getpass
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from turnloom import gateway


PROVIDERS = json.loads(files("turnloom").joinpath("data", "providers.json").read_text(encoding="utf-8"))
POPULAR = ["openrouter", "huggingface", "ollama", "lmstudio", "vllm", "llamacpp", "groq", "deepseek", "google", "openai"]


def known(provider):
    return isinstance(provider, str) and (provider in PROVIDERS or provider.startswith(gateway.PREFIX))


def info(provider):
    """Display name, endpoint and API-key variable for a preset or a LiteLLM gateway provider."""
    if provider in PROVIDERS:
        return tuple(PROVIDERS[provider])
    if not known(provider):
        raise ValueError(f"Unknown provider: {provider}")
    key = provider.removeprefix(gateway.PREFIX)
    return gateway.NAMES.get(key, key.replace("_", " ").title()) + " via LiteLLM", gateway.SCHEME + key, gateway.key_variable(provider)


def profile_path():
    folder = os.environ.get("TURNLOOM_CONFIG_DIR")
    if not folder:
        base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
        folder = Path(base or Path.home() / ".config") / "turnloom"
    return Path(folder) / "connection.json"


def profile():
    try:
        record = json.loads(profile_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        record = None
    if not isinstance(record, dict) or not known(record.get("provider")) or not isinstance(record.get("model"), str):
        raise ValueError("Invalid saved connection. Run turnloom connect to replace it.")
    return record


def store_profile(record):
    target = profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False, encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    try:
        os.replace(stream.name, target)
    finally:
        Path(stream.name).unlink(missing_ok=True)


def vault():
    import keyring
    backend = keyring.get_keyring()
    candidates = getattr(backend, "backends", [backend])
    for item in candidates:
        if type(item).__module__ in {"keyring.backends.Windows", "keyring.backends.macOS",
                                     "keyring.backends.SecretService", "keyring.backends.kwallet",
                                     "keyring.backends.libsecret"}:
            return item
    raise ValueError("An OS credential store is unavailable. Set the provider's API key environment variable.")


def secret(provider, required=True):
    _, _, variable = info(provider)
    if not variable:
        return ""
    value = os.environ.get(variable)
    if not value:
        try:
            value = vault().get_password("turnloom:" + provider, "api-key")
        except Exception:
            value = None
    if required and not value:
        raise ValueError(f"Set {variable} or use turnloom connect {provider}.")
    return value or ""


def fetch(url, key=""):
    from turnloom import __version__
    request = urllib.request.Request(url, headers={"User-Agent": "Turnloom/" + __version__, "Accept": "application/json"})
    if key:
        request.add_unredirected_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def window(value):
    """A context length in tokens, or None when the catalog does not report a usable one."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 1000 <= value <= 100_000_000 else None


def rate(value, scale=1):
    try:
        number = float(value) * scale
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 and math.isfinite(number) else None


def catalog():
    """The public Models.dev catalog, cached for a day beside the saved connection."""
    cache = profile_path().with_name("models-dev.json")
    try:
        if time.time() - cache.stat().st_mtime < 86400:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    data = fetch("https://models.dev/api.json")
    if not isinstance(data, dict):
        raise ValueError("Models.dev returned an unexpected catalog.")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data


def models(provider, key=""):
    if provider.startswith(gateway.PREFIX):
        return gateway.models(provider)
    name, endpoint, variable = PROVIDERS[provider]
    try:
        data = fetch(endpoint + "/models", key)
    except urllib.error.HTTPError as error:
        if error.code not in (404, 405) or not variable:
            raise ValueError(f"{name} model discovery returned HTTP {error.code}. Check the provider credential and access.") from error
        entries = catalog().get(provider, {}).get("models", {}).values()
        return [{"model": item["id"], "input": rate(item.get("cost", {}).get("input")),
                 "output": rate(item.get("cost", {}).get("output")), "tier": listed_tier(item),
                 "tools": True, "source": "models.dev catalog", "context": window((item.get("limit") or {}).get("context")),
                 "cache_read": rate(item.get("cost", {}).get("cache_read"))}
                for item in entries if item.get("tool_call")]
    except urllib.error.URLError as error:
        hint = "Start the local server and try again." if not variable else "Check your network connection."
        raise ValueError(f"Could not reach {name} at {endpoint}. {hint} ({error.reason})") from error
    except ValueError as error:
        raise ValueError(f"{name} returned a model list that is not JSON.") from error
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{name} returned an unsupported model-list format.")
    result = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        if provider == "huggingface" and isinstance(item.get("providers"), list):
            for route in item["providers"]:
                if route.get("status") != "live" or route.get("supports_tools") is False or not route.get("provider"):
                    continue
                pricing = route.get("pricing") or {}
                prompt, completion = rate(pricing.get("input")), rate(pricing.get("output"))
                free = route.get("is_free")
                tier = "free" if free is True else "paid" if free is False else "unknown"
                result.append({"model": item["id"] + ":" + route["provider"], "input": prompt, "output": completion,
                               "tier": tier, "tools": route.get("supports_tools"), "source": "live catalog",
                               "context": window(route.get("context_length"))})
            continue
        parameters = item.get("supported_parameters")
        if isinstance(parameters, list) and "tools" not in parameters:
            continue
        if item.get("type") in ("embedding", "image", "audio", "rerank") or item.get("active") is False:
            continue
        pricing = item.get("pricing") or {}
        advertised = provider == "openrouter"
        prompt = rate(pricing.get("prompt"), 1e6) if advertised else None
        completion = rate(pricing.get("completion"), 1e6) if advertised else None
        tier = "unknown"
        if prompt is not None and completion is not None:
            tier = "free" if all(rate(value) == 0 for value in pricing.values()) else "paid"
        result.append({"model": item["id"], "input": prompt, "output": completion, "tier": tier,
                       "tools": True if isinstance(parameters, list) else None, "source": "live catalog",
                       "context": window(item.get("context_length") or item.get("context_window")),
                       "cache_read": rate(pricing.get("input_cache_read"), 1e6) if advertised else None})
    return sorted(result, key=lambda item: item["model"].casefold())


def listed_tier(item):
    costs = item.get("cost") or {}
    return "paid" if any((rate(costs.get(key)) or 0) > 0 for key in ("input", "output")) else "unknown"


def choose(title, entries):
    if not entries:
        raise ValueError("No matching choices. Try a different filter or provider.")
    if not sys.stdin.isatty():
        raise ValueError(f"Interactive {title} selection needs a terminal. Supply the ID on the command line.")
    matches = entries
    offset = 0
    while True:
        print(f"\n{title} ({len(matches)} matches)")
        for index, (_, label) in enumerate(matches[offset:offset + 12], offset + 1):
            print(f"{index:3}  {label}")
        value = input("Number, search text, /next, /all or /quit: ").strip()
        if value == "/quit":
            raise ValueError("Connection cancelled.")
        if value == "/next":
            offset = offset + 12 if offset + 12 < len(matches) else 0
        elif value == "/all":
            matches, offset = entries, 0
        elif value.isdigit() and 1 <= int(value) <= len(matches):
            return matches[int(value) - 1][0]
        else:
            matches = [entry for entry in entries if value.casefold() in entry[1].casefold()]
            offset = 0


def label(row):
    price = row["tier"]
    if row["input"] is not None and row["output"] is not None:
        price += f"  USD {row['input']:g}/{row['output']:g} per million input/output"
    support = "tools listed" if row["tools"] else "tool support unreported"
    return f"{row['model']}  [{price}, {support}]"


def command(args):
    providers = list(dict.fromkeys(POPULAR + sorted(PROVIDERS)))
    options = [(key, f"{key}  {PROVIDERS[key][0]}  {PROVIDERS[key][1]}") for key in providers]
    if gateway.available() and (args.action == "providers" or not getattr(args, "provider", None)):
        options += [(key, f"{key}  {name} via LiteLLM  {count} models") for key, name, count in gateway.providers()]
    if args.action == "providers":
        for _, text in options:
            if args.search.casefold() in text.casefold():
                print(text)
        return 0
    if args.action in ("connection", "disconnect"):
        record = profile()
        if args.action == "disconnect":
            if record and args.forget_key and info(record["provider"])[2]:
                store = vault()
                service = "turnloom:" + record["provider"]
                if store.get_password(service, "api-key"):
                    store.delete_password(service, "api-key")
            profile_path().unlink(missing_ok=True)
            print("Cleared the active Turnloom connection.")
            return 0
        print(json.dumps(record, indent=2) if record else "No saved connection.")
        return 0
    provider = args.provider or choose("Provider", options)
    if not known(provider):
        raise ValueError(f"Unknown provider: {provider}. Run turnloom providers to list them.")
    unset = gateway.requirements(provider) if provider.startswith(gateway.PREFIX) else []
    if len(unset) > 1 and args.action == "connect":
        raise ValueError(f"{provider} reads {', '.join(unset)}. Set them in your environment, then connect again.")
    name, endpoint, variable = info(provider)
    key = secret(provider, required=False)
    entered = False
    if args.action == "connect" and provider != "openrouter" and variable and not key:
        if not sys.stdin.isatty():
            raise ValueError(f"Set {variable} to connect noninteractively.")
        vault()
        print(f"{name} uses {endpoint}. Enter its API key to save it in the OS credential store.")
        key = getpass.getpass(f"{variable}: ").strip()
        if not key:
            raise ValueError("No key supplied.")
        entered = True
    rows = models(provider, key)
    listing = {}
    if args.open_weights or (variable and provider not in ("openrouter", "huggingface") and not provider.startswith(gateway.PREFIX)):
        try:
            listing = catalog()
        except (OSError, ValueError):
            if args.open_weights:
                raise ValueError("Open-weight metadata is unavailable. Retry the catalog request.")
    details = listing.get(provider, {}).get("models", {})
    for row in rows:
        item = details.get(row["model"], {})
        if row["tools"] is None:
            row["tools"] = item.get("tool_call")
        if row["tier"] == "unknown" and listed_tier(item) == "paid":
            row.update(tier="paid", input=rate(item["cost"].get("input")), output=rate(item["cost"].get("output")))
        if not row.get("context"):
            row["context"] = window((item.get("limit") or {}).get("context"))
    rows = [row for row in rows if row["tools"] is not False and args.search.casefold() in row["model"].casefold()
            and (args.tier == "all" or args.tier == row["tier"])]
    if args.open_weights:
        weights = {item["id"].casefold() for ident, entry in listing.items() for item in entry["models"].values()
                   if item.get("open_weights") is True and (ident == provider or "/" in item["id"])}
        rows = [row for row in rows if row["model"].casefold() in weights
                or row["model"].split(":", 1)[0].casefold() in weights]
    if not rows:
        raise ValueError("No matching models. Check the filter or install a tool-capable model on your local server.")
    if args.action == "models":
        print(f"{name} · {rows[0]['source']}", file=sys.stderr)
        for row in rows:
            print(label(row))
        return 0
    selected = args.model or choose("Model", [(row["model"], label(row)) for row in rows])
    row = next((item for item in rows if item["model"] == selected), None)
    if row is None:
        raise ValueError(f"Model {selected} is absent from the filtered catalog.")
    if provider == "openrouter":
        if not key:
            if not sys.stdin.isatty():
                raise ValueError(f"Set {variable} to connect noninteractively.")
            vault()
            if input("Press Enter to sign in with OpenRouter, or type 'key' to paste an API key: ").strip().lower() == "key":
                key = getpass.getpass("OpenRouter key: ").strip()
            else:
                from turnloom.openrouter import login
                key = login()
            entered = True
        if not key:
            raise ValueError("No OpenRouter key supplied.")
        fetch(endpoint + "/key", key)
    if entered:
        try:
            vault().set_password("turnloom:" + provider, "api-key", key)
        except Exception as error:
            raise ValueError(f"Could not store the key securely. Set {variable} and retry.") from error
    store_profile({"provider": provider, **row})
    print(f"Selected {name} / {selected} ({row['source']}).")
    return 0


def register(commands):
    commands.add_parser("providers", help="Browse inference providers").add_argument("--search", default="")
    commands.add_parser("connection", help="Show the saved model selection")
    commands.add_parser("disconnect", help="Clear the active model selection").add_argument(
        "--forget-key", action="store_true", help="Also delete this provider's key from the OS credential store")
    for name in ("models", "connect"):
        sub = commands.add_parser(name, help="Browse the model catalog" if name == "models" else "Choose a provider and save a model")
        sub.add_argument("provider", nargs="?" if name == "connect" else None, metavar="PROVIDER",
                         help="A preset id from turnloom providers, or litellm:<provider> for a native API")
        sub.add_argument("--tier", choices=["all", "free", "paid", "unknown"], default="all")
        sub.add_argument("--search", default="")
        sub.add_argument("--open-weights", action="store_true", help="Show model IDs marked open weights in Models.dev")
        if name == "connect":
            sub.add_argument("--model", help="Select an exact catalog ID without the menu")


@dataclass
class Connection:
    endpoint: str
    model: str
    key: str
    input_rate: float = 0
    output_rate: float = 0
    free: bool = False
    cache_rate: float | None = None
    context: int = 0
    provider: str = ""


def setting(name):
    """Read TURNLOOM_<NAME>, accepting the HARNESS_<NAME> spelling used before 0.7."""
    return os.environ.get("TURNLOOM_" + name) or os.environ.get("HARNESS_" + name) or ""


def custom_endpoint():
    """The (endpoint, model) pair from the environment, which takes precedence over the saved profile."""
    endpoint, model = setting("ENDPOINT"), setting("MODEL")
    if not endpoint and not model:
        return None
    if not endpoint or not model:
        raise ValueError("Custom inference requires both TURNLOOM_ENDPOINT and TURNLOOM_MODEL.")
    return endpoint, model


def resolve(endpoint=None, model=None, key=None, lookup=False):
    if endpoint or model:
        if not endpoint or not model:
            raise ValueError("Custom inference requires both a model and an endpoint.")
        custom = (endpoint, model)
    else:
        custom = custom_endpoint()
    if custom:
        return Connection(custom[0], custom[1], key if key is not None else setting("API_KEY"))
    record = profile()
    if not record:
        raise ValueError("Choose your provider and model with turnloom connect.")
    provider = record["provider"]
    return connection(provider, record, secret(provider, required=bool(info(provider)[2])), lookup)


def connection(provider, row, key, lookup=True):
    """Bind a catalog row to its endpoint. With lookup, missing limits and cache pricing come from
    LiteLLM's public map. Loading that map takes seconds, so command-line runs skip it."""
    complete = row.get("context") and row.get("cache_read") is not None
    listed = gateway.lookup(row["model"], provider) if lookup and not complete else None
    context = row.get("context") or (listed or {}).get("context") or 0
    cache = rate(row.get("cache_read"))
    if cache is None and listed and listed.get("input") == rate(row.get("input")):
        cache = rate(listed.get("cache_read"))
    return Connection(info(provider)[1], row["model"], key, rate(row.get("input")) or 0, rate(row.get("output")) or 0,
                      row.get("tier") == "free" and not rate(row.get("input")) and not rate(row.get("output")),
                      cache, window(int(context)) or 0 if isinstance(context, (int, float)) else 0, provider)
