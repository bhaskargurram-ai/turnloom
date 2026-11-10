# Connecting inference

```sh
turnloom providers --search groq
turnloom models openrouter --tier free --open-weights
turnloom connect openrouter --tier free
turnloom chat
```

The terminal shows up to twelve choices at a time. Enter a number, type part of a name to search, or use `/next` and `/all`. Selection only reads catalogs. Inference begins when you submit a task. OpenRouter offers a browser grant with a local callback and PKCE. A provider API key or environment variable also works. A host without an OS credential store can use environment variables.

| Want to use | Command | Setup |
|---|---|---|
| Hosted free or paid models | `turnloom connect openrouter` | Sign in to OpenRouter, authorize the app and choose a model. Free routes remain subject to provider quotas. |
| A specific hosted open model | `turnloom connect huggingface --search qwen` | Supply a Hugging Face inference token. A selection includes the serving provider when the router advertises routes. |
| Installed local weights | `turnloom connect ollama` or `turnloom connect lmstudio` | Start the local service on its default port with a tool-capable model installed. |
| A local model server | `turnloom connect vllm` or `turnloom connect llamacpp` | Run the server on its standard port. A protected instance can use the custom endpoint environment settings below. |
| Direct hosted inference | `turnloom providers`, then `turnloom connect` | Choose the account you want to bill. Reuse its provider-specific environment variable or enter its key once. |

## Coverage and costs

Version 0.3 bundles 200 provider presets. The catalog includes fixed HTTPS endpoints with a single credential variable and tool-capable model entries from the November 10 Models.dev snapshot, plus common direct and local endpoints. Native-only APIs, cloud deployments with tenant-specific URLs, and services requiring custom signing need a compatible gateway or deployment configuration. Presets describe routing; availability depends on the account, region, service version and selected model.

Turnloom queries the chosen endpoint's model list. For services without a list route (HTTP 404 or 405), it uses the public Models.dev catalog and labels the source. Pricing and capability data come from the endpoint when available, with public metadata filling gaps. Free means a reported free route or zero advertised OpenRouter charges. Unreported pricing stays unknown. Open weights is a metadata filter on matching IDs, and individual models retain their own licenses. Models with unreported tool support should be checked on a small task.

The saved selection supplies the model and available token rates for run, chat, team and evaluation. Rates are estimates at connection time. Reconnect to refresh them or override them with explicit rate flags. No automatic switch between paid and free routes occurs in Turnloom. A provider may route internally.

## Credentials and overrides

`turnloom connection` shows the saved selection. `turnloom disconnect` clears it. Add `--forget-key` to remove the active provider's local vault entry. Revoke an issued key through the provider to disable it there. Entered keys use Windows Credential Manager, macOS Keychain or a supported Linux secret service. The JSON profile contains the provider, model and pricing metadata. For isolated profiles set `TURNLOOM_CONFIG_DIR`.

For a custom service or an existing script, set `HARNESS_ENDPOINT` and `HARNESS_MODEL` together. The pair takes precedence over the saved profile. `HARNESS_API_KEY` supplies that custom connection's credential. Provider vault keys are scoped to their preset.

Sources: [Models.dev](https://models.dev), [catalog license](../turnloom/data/LICENSE.models-dev), [OpenRouter model discovery](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties), [OpenRouter authorization](https://openrouter.ai/docs/guides/overview/auth/oauth), [Hugging Face Inference Providers](https://huggingface.co/docs/inference-providers/en/index), [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility).
