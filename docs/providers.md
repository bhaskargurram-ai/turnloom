# Connecting a model

```sh
turnloom providers --search groq
turnloom models openrouter --tier free --open-weights
turnloom connect openrouter --tier free
turnloom chat
```

`turnloom connect` shows up to twelve choices at a time. Enter a number, type part of a name to filter, or use `/next` and `/all`. Selection only reads catalogs; inference starts when you send a task. In Studio the same flow is the model control in the top bar.

| You want | Command | Setup |
|---|---|---|
| Hosted free or paid models behind one account | `turnloom connect openrouter` | Sign in through the browser (a local callback with PKCE) or paste a key. Free routes are subject to OpenRouter's quotas. |
| A specific hosted open model | `turnloom connect huggingface --search qwen` | Supply a Hugging Face token. The selection includes the serving provider when the router advertises routes. |
| A model on your machine | `turnloom connect ollama` or `turnloom connect lmstudio` | Start the service on its default port with a tool-capable model installed. No key is needed. |
| Your own model server | `turnloom connect vllm` or `turnloom connect llamacpp` | Run the server on its standard port. For another port or a protected instance, use the environment settings below. |
| Another hosted provider | `turnloom providers`, then `turnloom connect NAME` | Reuse the provider's environment variable or paste its key once. |
| Anthropic, Bedrock, Vertex AI, Azure OpenAI | `turnloom connect litellm:anthropic` and siblings | Needs the `llm` extra. See below. |

## Presets

Turnloom bundles 187 provider presets: a name, a base URL and the environment variable that holds the key. They come from the [Models.dev](https://models.dev) catalog plus common local servers. Presets that need a vendor-specific client or a token exchange were removed.

The presets are routing data, not an endorsement. Turnloom's tests exercise the OpenRouter-style and Ollama-style catalog shapes and a generic OpenAI-compatible server; the remaining presets are not individually verified. Before you enter a key, `turnloom connect` and Studio show the endpoint the key will be sent to. A key is only ever sent to its own provider's endpoint.

## Native provider APIs

Providers that do not offer a plain OpenAI-compatible endpoint are reached through [LiteLLM](https://github.com/BerriAI/litellm), which translates the same request and tool-call format.

```sh
pipx inject turnloom litellm      # or install the llm extra in your own environment
turnloom providers --search litellm
turnloom connect litellm:anthropic
```

A provider that authenticates with one API key asks for it and stores it like any other. Providers that need several settings, such as AWS credentials or an Azure endpoint and API version, read them from your environment; `turnloom connect` names the variables that are missing. Model lists and prices for these providers come from LiteLLM's public price map, so a model your account cannot use may still be listed. Requests to Claude models mark the system prompt for prompt caching. Replies through LiteLLM arrive whole rather than streamed.

## Model discovery, prices and context size

Turnloom asks the chosen endpoint for its model list. When an endpoint has no list route (HTTP 404 or 405), it uses the public Models.dev catalog, cached for a day in your configuration folder, and labels the source.

Prices, the cached-input price and the context window come from the endpoint when it reports them. Models.dev fills gaps, and with the `llm` extra installed Studio also consults LiteLLM's public price map, using its prices only when the entry is for the same provider. The saved selection supplies these numbers to `run`, `chat`, `team`, Studio and the evaluation runner. Override them with `--context`, `--input-rate` and `--output-rate`. When no window is known, Turnloom assumes 128,000 tokens.

"Free" means a route the provider reports as free, or zero advertised OpenRouter charges. Unreported pricing stays "unknown" and budgets then need explicit rates. `--open-weights` filters by Models.dev metadata; each model keeps its own license. Models with unreported tool support may not call tools; try a small task first.

Rates are estimates taken at connection time. Reconnect to refresh them. Turnloom never switches model or route by itself.

## Credentials

Keys you enter are stored in Windows Credential Manager, macOS Keychain or a Linux secret service. The profile file holds only the provider, model and catalog metadata. On a machine without a credential store, set the provider's environment variable instead; Studio will also hold a pasted key in memory until it closes and tells you so.

`turnloom connection` shows the saved selection. `turnloom disconnect` clears it, and `--forget-key` also removes the stored key. Revoke a key at the provider to disable it there. Set `TURNLOOM_CONFIG_DIR` to keep separate profiles.

## Custom endpoints

Set `TURNLOOM_ENDPOINT` and `TURNLOOM_MODEL` together to use any OpenAI-compatible server. The pair takes precedence over the saved profile, and `TURNLOOM_API_KEY` supplies its credential. The older `HARNESS_ENDPOINT`, `HARNESS_MODEL` and `HARNESS_API_KEY` names still work.

```sh
TURNLOOM_ENDPOINT=http://127.0.0.1:4000/v1 TURNLOOM_MODEL=my-model turnloom chat
```

Sources: [Models.dev](https://models.dev), [catalog license](../turnloom/data/LICENSE.models-dev), [OpenRouter model discovery](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties), [OpenRouter authorization](https://openrouter.ai/docs/guides/overview/auth/oauth), [Hugging Face Inference Providers](https://huggingface.co/docs/inference-providers/en/index), [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility), [LiteLLM providers](https://docs.litellm.ai/docs/providers).
