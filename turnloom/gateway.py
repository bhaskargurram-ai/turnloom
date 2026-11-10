"""Reach native provider APIs through LiteLLM.

Turnloom's own client speaks OpenAI chat completions. Anthropic, Bedrock, Vertex AI,
Azure and other native or signed APIs go through LiteLLM, which translates the same
request and tool-call format. A gateway provider has the id ``litellm:<provider>``
and the endpoint ``litellm://<provider>``.

https://github.com/BerriAI/litellm
"""

import logging
from functools import cache
from importlib.util import find_spec

SCHEME = "litellm://"
PREFIX = "litellm:"
# LiteLLM lists some providers under several catalog names. Each maps to one route prefix.
ROOTS = {"bedrock_converse": "bedrock", "bedrock_mantle": "bedrock", "cohere_chat": "cohere",
         "text-completion-openai": "openai", "vertex_ai_beta": "vertex_ai"}
NAMES = {"anthropic": "Anthropic", "bedrock": "Amazon Bedrock", "vertex_ai": "Google Vertex AI", "azure": "Azure OpenAI",
         "azure_ai": "Azure AI Foundry", "gemini": "Google AI Studio", "cohere": "Cohere", "watsonx": "IBM watsonx",
         "databricks": "Databricks", "sagemaker": "Amazon SageMaker", "snowflake": "Snowflake Cortex",
         "github_copilot": "GitHub Copilot", "oci": "Oracle Cloud", "openai": "OpenAI", "xai": "xAI",
         "mistral": "Mistral", "replicate": "Replicate", "cloudflare": "Cloudflare Workers AI"}
FIRST = ["anthropic", "bedrock", "vertex_ai", "azure", "azure_ai", "gemini", "cohere", "watsonx", "databricks"]


def available():
    return find_spec("litellm") is not None


def _litellm():
    try:
        import litellm
    except ImportError as error:
        raise ValueError('Install the gateway with: python -m pip install "turnloom[llm]"') from error
    litellm.suppress_debug_info = True
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    litellm.telemetry = False
    return litellm


def root(provider):
    provider = ROOTS.get(provider, provider or "")
    return provider.split("-", 1)[0] if provider.startswith("vertex_ai") else provider


@cache
def _index():
    """Tool-capable chat models from LiteLLM's price map, grouped by route prefix."""
    litellm = _litellm()
    groups = {}
    for key, item in litellm.model_cost.items():
        if not isinstance(item, dict) or item.get("mode") != "chat" or not item.get("supports_function_calling"):
            continue
        provider = root(item.get("litellm_provider"))
        if not provider or key.startswith("ft:"):
            continue
        model = key if key.startswith(provider + "/") else provider + "/" + key
        def per_million(name):
            value = item.get(name)
            return round(value * 1e6, 6) if isinstance(value, (int, float)) else None
        prompt, completion = per_million("input_cost_per_token"), per_million("output_cost_per_token")
        groups.setdefault(provider, {})[model] = {
            "model": model, "input": prompt, "output": completion, "cache_read": per_million("cache_read_input_token_cost"),
            "context": item.get("max_input_tokens") or item.get("max_tokens"), "max_output": item.get("max_output_tokens"),
            "tier": "unknown" if prompt is None or completion is None else "free" if prompt == completion == 0 else "paid",
            "tools": True, "caching": bool(item.get("supports_prompt_caching")), "source": "LiteLLM price map"}
    return groups


def providers():
    """Gateway providers as (id, display name, model count), native-only APIs first."""
    order = {key: index for index, key in enumerate(FIRST)}
    keys = sorted(_index(), key=lambda key: (order.get(key, len(order)), key))
    return [(PREFIX + key, NAMES.get(key, key.replace("_", " ").title()), len(_index()[key])) for key in keys]


def models(provider):
    rows = _index().get(provider.removeprefix(PREFIX))
    if not rows:
        raise ValueError(f"LiteLLM lists no tool-capable chat models for {provider}.")
    return sorted(rows.values(), key=lambda row: row["model"].casefold())


def requirements(provider):
    """Environment variables LiteLLM reads for this provider and still finds unset."""
    rows = _index().get(provider.removeprefix(PREFIX)) or {}
    if not rows:
        return []
    try:
        return list(_litellm().validate_environment(model=next(iter(rows)))["missing_keys"])
    except Exception:
        return []


def key_variable(provider):
    """The single API-key variable for providers that authenticate with one, else an empty string."""
    missing = requirements(provider)
    return missing[0] if len(missing) == 1 and missing[0].endswith(("_API_KEY", "_KEY", "_TOKEN")) else ""


PRESETS = {"google": "gemini", "togetherai": "together_ai", "fireworks-ai": "fireworks_ai", "fireworks": "fireworks_ai",
           "x-ai": "xai", "moonshotai": "moonshot", "alibaba": "dashscope", "huggingface": "huggingface"}


def lookup(model, provider=None):
    """Price and limits for a model id from LiteLLM's map, or None.

    A row found under the caller's own provider carries ``same_provider``. Other matches share
    the model name only, so their limits are a fair guide but their prices are another host's.
    """
    if not available():
        return None
    if provider:
        key = PRESETS.get(provider, provider.removeprefix(PREFIX))
        row = _index().get(key, {}).get(model if model.startswith(key + "/") else key + "/" + model)
        if row:
            return {**row, "same_provider": True}
    for rows in _index().values():
        if model in rows:
            return rows[model]
    tail = model.split("/", 1)[-1].split(":", 1)[0].casefold()
    for rows in _index().values():
        for key, row in rows.items():
            if key.split("/", 1)[-1].casefold() == tail:
                return row
    return None


def complete(payload, api_key, timeout):
    """One chat completion in OpenAI format. Raises RuntimeError with the provider's message."""
    litellm = _litellm()
    try:
        response = litellm.completion(**payload, api_key=api_key or None, timeout=timeout, num_retries=0, drop_params=True)
    except Exception as error:  # LiteLLM maps provider failures onto its own exception tree
        status = getattr(error, "status_code", None)
        raise RuntimeError(f"Model {'HTTP ' + str(status) if status else 'gateway error'}: {str(error)[:1500]}") from error
    data = response.model_dump()
    usage = data.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    if not details.get("cached_tokens") and usage.get("cache_read_input_tokens"):
        usage["prompt_tokens_details"] = {**details, "cached_tokens": usage["cache_read_input_tokens"]}
    return data
