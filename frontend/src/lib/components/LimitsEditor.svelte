<script lang="ts">
  // One block of limit settings, used for a project and, with the project's own values as what a
  // blank box inherits, for a role (V5-limit-settings, ADR-0030). It edits a plain object and says
  // what is in force; the parent decides when to save.
  import type { LimitInfo, LimitValues } from "../api";
  import { describeValue, inForce, parseLimit, withLimit } from "../limits";

  let {
    catalog,
    values,
    inherited,
    inheritedFrom,
    idPrefix,
    onChange,
    onProblem,
  }: {
    catalog: LimitInfo[];
    /** What is set here. A key that is absent inherits. */
    values: LimitValues;
    /** What applies when a key is blank here: the project's own settings, for a role. */
    inherited?: LimitValues;
    /** Where a blank box inherits from, in words: "the project" or "the daemon's settings". */
    inheritedFrom: string;
    idPrefix: string;
    onChange: (next: LimitValues) => void;
    /** Told whether any box holds something that cannot be saved, so the parent can keep its Save
     * button off: otherwise a valid entry followed by an invalid one would save the stale value. */
    onProblem?: (has: boolean) => void;
  } = $props();

  // What is typed, per key, so a half-typed or invalid entry is not rewritten under the cursor.
  // The parent re-creates this component (a keyed block) when the values change from outside.
  let texts = $state<Record<string, string>>({});
  $effect.pre(() => {
    for (const info of catalog) {
      if (texts[info.key] === undefined) {
        texts[info.key] = values[info.key] === undefined ? "" : String(values[info.key]);
      }
    }
  });

  function problem(info: LimitInfo): string | null {
    const parsed = parseLimit(info, texts[info.key] ?? "");
    return parsed.kind === "invalid" ? parsed.problem : null;
  }

  $effect(() => {
    onProblem?.(catalog.some((info) => problem(info) !== null));
  });

  function type(info: LimitInfo, text: string) {
    texts[info.key] = text;
    const parsed = parseLimit(info, text);
    if (parsed.kind === "inherit") onChange(withLimit(values, info.key, null));
    else if (parsed.kind === "value") onChange(withLimit(values, info.key, parsed.value));
    // An invalid entry is left on screen with its problem; nothing is saved until it is fixed.
  }

  function clear(info: LimitInfo) {
    texts[info.key] = "";
    onChange(withLimit(values, info.key, null));
  }
</script>

<div class="limits">
  {#each catalog as info (info.key)}
    {@const id = `${idPrefix}-${info.key}`}
    {@const set = values[info.key] !== undefined}
    {@const fallback = inForce(info, inherited)}
    <div class="row">
      <div class="what">
        <label for={id} class="title-small">{info.title}</label>
        <span class="body-small muted">{info.summary}</span>
        <span class="body-small muted now" id="{id}-now">
          {#if set}
            Set here: {describeValue(info, values[info.key])}.
          {:else}
            Inherits {describeValue(info, fallback)} from {inheritedFrom}.
          {/if}
        </span>
      </div>
      <div class="input">
        <label class="field">
          <span class="field-label">{info.unit}</span>
          <input
            {id}
            inputmode="numeric"
            autocomplete="off"
            spellcheck="false"
            value={texts[info.key] ?? ""}
            placeholder={String(fallback)}
            aria-invalid={problem(info) !== null}
            aria-describedby={problem(info) ? `${id}-problem ${id}-now` : `${id}-now`}
            oninput={(event) => type(info, event.currentTarget.value)}
          />
        </label>
        {#if problem(info)}
          <span class="body-small error" id="{id}-problem" role="alert">{problem(info)}</span>
        {:else if info.zero_means}
          <span class="body-small muted">0 means {info.zero_means}.</span>
        {/if}
        <button type="button" class="btn btn-text sm" disabled={!set} onclick={() => clear(info)}>
          Use {inheritedFrom === "the project" ? "the project's" : "the default"}
        </button>
      </div>
    </div>
  {/each}
</div>

<style>
  .limits {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }

  .row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 14rem;
    gap: 1rem;
    align-items: start;
  }

  .what {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .what label {
    margin: 0;
  }

  .now {
    color: var(--md-sys-color-on-surface);
  }

  .input {
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: flex-start;
  }

  .input .field {
    width: 100%;
    box-sizing: border-box;
  }

  .error {
    color: var(--md-sys-color-error);
  }

  @media (max-width: 700px) {
    .row {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
