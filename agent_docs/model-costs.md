# Model choices and what they cost

A snapshot of OpenRouter's public model list (`GET https://openrouter.ai/api/v1/models`, no key needed) on 2026-10-08.
Prices are US dollars per million tokens, input / output. They move; re-pull the list before deciding anything.

## Where the money goes

- **The coding rounds**, not the summaries. A kopicode round resends its whole history every turn, so about 95 per cent of
  a round's tokens are prompt, most of them cached (4.78M of 5.0M in the 100-turn run). The 100-turn run cost about $0.71, a
  12-turn round about $0.01, a 16-round team about $0.12 (`qwen/qwen3-coder-next`, $0.12 / $0.80).
- **cuttlefish's own summaries** (one per continuation): `openrouter/auto` picked `z-ai/glm-5.3-flash` ($0.15 / $0.50), a
  reasoning model that twice spent its whole 4096-token output cap and answered nothing. Now pinned
  (`CUTTLEFISH_LLM_MODEL`, default `qwen/qwen3-30b-a3b-instruct-2507`, $0.048 / $0.193, no reasoning).
- **Our own live tests and exploratory runs.** The usual way to hit a daily or weekly key limit.

## Summariser candidates (non-reasoning instruct, tool support not needed)

| Model | in / out | Context | Note |
| --- | --- | --- | --- |
| `qwen/qwen3-30b-a3b-instruct-2507` | 0.048 / 0.193 | 262k | the default |
| `mistralai/mistral-small-3.2-24b-instruct` | 0.094 / 0.250 | 256k | steady instruction following |
| `google/gemma-3-12b-it` | 0.050 / 0.150 | 131k | smaller |
| `mistralai/mistral-nemo` | 0.019 / 0.030 | 131k | cheapest that writes a decent report |
| `openai/gpt-oss-20b` | 0.018 / 0.090 | 131k | reasoning: watch for empty answers |
| `*:free` models | 0 | varies | rate-limited, and free tiers may log prompts: do not send a private repository's tool calls |

## Coding-agent candidates for kopicode (need tool calling; none measured here except the first)

| Model | in / out | Context | Note |
| --- | --- | --- | --- |
| `qwen/qwen3-coder-next` | 0.12 / 0.80 | 262k | kopicode's default and measured model |
| `qwen/qwen3-coder-30b-a3b-instruct` | 0.070 / 0.280 | 262k | same family, smaller; the first thing to try |
| `deepseek/deepseek-v4-flash` | 0.017 / 1.280 | 1M | very cheap input, dearer output; reasoning |
| `openai/gpt-oss-120b` | 0.037 / 0.170 | 131k | reasoning; tool use is good, long histories less so |
| `z-ai/glm-4.7-flash` | 0.061 / 0.400 | 200k | reasoning |
| `mistralai/devstral-2512` | 0.40 / 2.00 | 262k | coding-tuned, dearer |

kopicode reaches only OpenRouter today (its base URL is a test hook, not a flag), so Ollama or a self-hosted server needs a
small kopicode change first, and a CPU-sized model is unlikely to be a reliable tool-calling coder.

## Cheaper without changing the model

- Fewer real runs: the full handover check (12 turns a round, 16 rounds) once per handover change; everything else with the
  scripted fake kopicode and `CUTTLEFISH_LLM_PROVIDER=replay`.
- A lower turn cap per round (the project's own limits, ADR-0030) bounds one round's spend; the token budget counts resent
  history, so it is the better ceiling for money.
- `max_cost_usd` on a project now trips on kopicode's reported cost.
