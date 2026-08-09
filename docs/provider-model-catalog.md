# Provider and model configuration

SourceLens can route text generation, tool use, visual analysis, embeddings, speech, search, and
media discovery across several provider families. Every provider is optional; enable only the
routes for which you have credentials and suitable data-processing terms.

The Providers screen is the runtime source of truth. It shows model IDs, capability labels,
priority, credential counts, cooldown state, and availability without exposing secret values.
Model catalogs and account quotas change independently of SourceLens releases, so deployments
should verify enabled routes with the opt-in live smoke command.

```powershell
uv run paperlens provider-smoke --live
```

The command performs one bounded text/tool check per enabled provider family. It never prints API
keys. Normal tests and CI do not make provider calls.

## Built-in route families

| Provider | Primary uses | Credential setting |
| --- | --- | --- |
| Google Gemini API | Text, tool use, visual analysis, embeddings, Live audio | `GOOGLE_API_KEYS` |
| GroqCloud | Fast text and tool fallbacks, speech transcription | `GROQ_CHAT_API_KEYS` |
| Mistral API | Text, tool use, and multimodal fallbacks | `MISTRAL_API_KEYS` |
| NVIDIA NIM | Text reasoning and selected multimodal routes | `NVIDIA_API_KEYS` |
| OCR.Space | Selective OCR for scanned pages | `OCR_SPACE_API_KEYS` |
| Exa and Tavily | Web-source discovery and extraction | `EXA_API_KEYS`, `TAVILY_API_KEYS` |
| Pexels | Licensed photo and video discovery | `PEXELS_API_KEYS` |
| Jamendo | Optional noncommercial music discovery | `JAMENDO_CLIENT_IDS` |

Custom OpenAI-compatible providers can be added from the UI or `providers.json`. Configuration
stores endpoint and model metadata only; credentials are resolved from the named environment
variable.

## Default model order

The built-in catalog currently includes the following families, ordered by configured quality and
capability rather than by provider quota:

- Gemini 3.6/3.5/3 Flash, Gemini 2.5 Flash, Gemini 3.5/3.1 Flash-Lite, and hosted Gemma 4 routes;
- Groq GPT-OSS, Qwen, and Llama fallbacks;
- Mistral Large, Medium, Small, and Ministral fallbacks;
- NVIDIA Nemotron and Inkling routes.

The exact IDs are versioned in `src/paperlens/model_routes.py` and displayed in the application.
Disabled or retired routes are skipped without changing the rest of the priority list.

Audio overview uses Gemini Live models configured in the Studio form. One credential is leased per
participant and additional credentials become reserve routes. Context-window compression,
checkpointed turns, and reconnection keep long sessions recoverable without storing session
credentials in artifacts.

## Quotas and failover

Provider quotas usually apply to a project or organization, not independently to every key. Adding
several keys from one quota domain therefore improves credential recovery but may not increase total
capacity. SourceLens reports **credentials configured**, not an assumed request allowance.

Retryable timeouts, rate limits, and temporary server errors enter a bounded cooldown. Authentication
and invalid-model errors fail without repeatedly calling the same route. The next enabled model tier
can then attempt the request. Use the request-flow view for secret-safe attempt timing and status.

Official references:

- [Gemini models and lifecycle](https://ai.google.dev/gemini-api/docs/models)
- [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Groq models and limits](https://console.groq.com/docs/models)
- [Mistral models](https://docs.mistral.ai/getting-started/models/)
- [NVIDIA API catalog](https://build.nvidia.com/models)
