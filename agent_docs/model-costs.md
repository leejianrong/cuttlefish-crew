# Model choices and what they cost

A snapshot of OpenRouter's public model list (`GET https://openrouter.ai/api/v1/models`, no key needed) on 2026-10-08.
Prices are US dollars per million tokens, input / output. They move; re-pull the list before deciding anything.

## Where the money goes

- **The coding rounds**, not the summaries. A kopicode round resends its whole history every turn, so about 95 per cent of
  a round's tokens are prompt, most of them cached (4.78M of 5.0M in the 100-turn run). The 100-turn run cost about $0.71, a
  12-turn round about $0.01, a 16-round team about $0.12 (`qwen/qwen3-coder-next`, $0.12 / $0.80).
- **cuttlefish's own summaries** (one per continuation): `openrouter/auto` picked `z-ai/glm-5.3-flash` ($0.15 / $0.50), a
  reasoning model that twice spent its whole 4096-token output cap and answered nothing. Now pinned
  (`CUTTLEFISH_LLM_MODEL`, default `deepseek/deepseek-v4.1-flash`, $0.065 / $0.144, low reasoning effort; changed from `qwen/qwen3-30b-a3b-instruct-2507` in CUT-129, which the account's guardrail blocks).
- **Our own live tests and exploratory runs.** The usual way to hit a daily or weekly key limit.

## Summariser candidates (non-reasoning instruct, tool support not needed)

| Model | in / out | Context | Note |
| --- | --- | --- | --- |
| `deepseek/deepseek-v4.1-flash` | 0.065 / 0.144 | 1M | the default since CUT-129; reasoning-capable, so watch for empty answers |
| `qwen/qwen3-30b-a3b-instruct-2507` | 0.048 / 0.193 | 262k | the former default (blocked by this owner's guardrail) |
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

kopicode reaches OpenRouter by default; `--provider-url` (kopicode ADR-0027, used here through `CUTTLEFISH_KOPICODE_PROVIDER_URL` and
`CUTTLEFISH_KOPICODE_MODEL`) points it at any OpenAI-compatible endpoint, so Ollama or a self-hosted server needs no kopicode change. A
CPU-sized model is unlikely to be a reliable tool-calling coder.

### What was measured, 2026-10-18 (CUT-75, and the first half of CUT-74)

The task: a nine-module Python package (`textkit`), five unittest tests per module (45 in all), one module at a time with a commit after
each, `max_turns=12` per round so rounds hand over, `--mode auto`, kopicode v0.5.0+14 (`5b8f4af`), run through `cuttlefish run-team`. One
run per arm, and every arm was cut short when the OpenRouter key's weekly limit ($1.00, resets Monday 00:00 UTC) was reached, so these
are partial runs, not finished ones. Each arm used the custom-URL path (unpinned), so all three are like for like; none anchors a benchmark.

| Arm | Rounds | Tokens | Cost | Modules committed | Tests on disk |
| --- | --- | --- | --- | --- | --- |
| `qwen/qwen3-coder-next` (default), pinned path | 11 | 1.43M | $0.131 | 3 of 9 | 104 (8 failing, 1 error) |
| `qwen/qwen3-coder-next` through the URL | 8 | 1.09M | $0.101 | 7 of 9 | 139 (6 failing, 1 error) |
| `deepseek/deepseek-v4.1-flash` ($0.065 / $0.144) | 6 | 0.63M | $0.038 | 9 of 9 | 45 (all passing) |

What it says, and what it does not: on this one task the cheaper model finished the work in about a third of the spend, and followed the
"five tests, one module at a time, commit each" instruction where the default did not (it wrote far more tests than asked and committed
late). That is one run per arm and a cap-truncated one, so it is a lead, not a ranking. **Recommendation: use `deepseek/deepseek-v4.1-flash`
as the cheap kopicode model for cuttlefish rounds, set through the URL path, and repeat the task three times when the key has room.** The
account's OpenRouter guardrail only allows a few models (see below), and `deepseek/deepseek-v4.1-flash` is on that list.

### This key's guardrail, and its weekly limit

- The account's OpenRouter guardrail (set to stop an expensive model being used by accident) blocks most models. Of 18 tried, these
  answered: `qwen/qwen3-coder-next`, `deepseek/deepseek-v3.2`, `deepseek/deepseek-v4.1-flash`. Blocked: the pinned summariser
  `qwen/qwen3-30b-a3b-instruct-2507`, `qwen/qwen3-coder-30b-a3b-instruct`, `deepseek/deepseek-v4-flash`, `openai/gpt-oss-120b`,
  `minimax/minimax-m2`, `z-ai/glm-5.2` and others. A blocked model is a 404 ("0 endpoints ... matching your guardrail restrictions").
  With the pinned summariser blocked, a handover silently falls back to carrying the previous summary, so a run against this key must set
  `CUTTLEFISH_LLM_MODEL` to an allowed model or it tests nothing.
- The key has a $1.00 weekly limit. A full 16-round run is about $0.13, so two or three runs in one week use it up; the third arm of this
  session ended on it. A rerun needs the limit raised or the reset.

## Cheaper without changing the model

- Fewer real runs: the full handover check (12 turns a round, 16 rounds) once per handover change; everything else with the
  scripted fake kopicode and `CUTTLEFISH_LLM_PROVIDER=replay`.
- A lower turn cap per round (the project's own limits, ADR-0030) bounds one round's spend; the token budget counts resent
  history, so it is the better ceiling for money.
- `max_cost_usd` on a project now trips on kopicode's reported cost.

## Why the summariser was a GLM model, and how to keep a model choice from happening silently

cuttlefish's summariser asked OpenRouter for `openrouter/auto` (the default since the first OpenRouter provider commit, #8). That
is a router: OpenRouter picks a model per request, and it served `z-ai/glm-5.3-flash`, a reasoning model that spent its output cap
thinking and returned nothing. Nothing in cuttlefish chose GLM, and nothing recorded or said so until the summariser's calls were
journaled (the `model` on `LlmCallCompleted` is the served model). The coding model is separate (kopicode's own default,
`qwen/qwen3-coder-next`).

What keeps it from recurring:

- The default is pinned (`CUTTLEFISH_LLM_MODEL`), and a test fails if the default is `openrouter/auto`.
- The served model is on every summary row of the activity log, and the provider logs a line when the served model differs from the
  requested one: INFO for a router (with how to pin), WARNING for a pinned model that was swapped.
- On OpenRouter's side (your account, not ours): give each key a credit limit (the weekly limit on this key is what stopped the
  last run, and it worked as a guard), restrict the workspace's allowed models or providers to the ones you accept, and
  review the Activity page for the models actually billed. Use a separate key for tests and runs so one cannot drain the other.

