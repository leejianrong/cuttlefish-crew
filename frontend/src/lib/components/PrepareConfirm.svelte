<script lang="ts">
  import type { PrepareInfo } from "../api";
  import { stepLines } from "../environment";

  let {
    prepare,
    root,
    busy,
    onChoose,
    onCancel,
  }: {
    prepare: PrepareInfo;
    root: string;
    busy: boolean;
    onChoose: (choice: "once" | "always" | "skip") => void;
    onCancel: () => void;
  } = $props();

  const lines = $derived(stepLines(prepare));
</script>

<div class="confirm card" role="group" aria-labelledby="prepare-heading">
  <h3 id="prepare-heading" class="title-medium">Install dependencies first?</h3>
  <p class="body-medium">
    Some of this project's dependencies are missing or out of date. cuttlefish can install them
    before the team starts, so the agents don't spend their turns finding out.
  </p>
  <ul class="steps">
    {#each lines as line (line.name)}
      <li>
        <span class="title-small">{line.name}</span>
        <span class="body-small muted">{line.reason}</span>
        {#each line.commands as command (command)}
          <code class="command mono">{command}</code>
        {/each}
      </li>
    {/each}
  </ul>
  <p class="body-small muted">
    This runs the project's own install scripts in <span class="mono">{root}</span>. Nothing else is
    touched; your lockfile is not rewritten.
  </p>
  <div class="actions">
    <button class="btn btn-filled" disabled={busy} onclick={() => onChoose("once")}>
      Install and start
    </button>
    <button class="btn btn-tonal" disabled={busy} onclick={() => onChoose("always")}>
      Install, and do this automatically
    </button>
    <button class="btn btn-tonal" disabled={busy} onclick={() => onChoose("skip")}>
      Start without installing
    </button>
    <button class="btn btn-text" disabled={busy} onclick={onCancel}>Cancel</button>
  </div>
</div>

<style>
  .confirm {
    padding: 1rem 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    background: var(--md-sys-color-surface-container-high);
    border-color: var(--md-sys-color-outline-variant);
  }

  .confirm :global(h3),
  .confirm p {
    margin: 0;
  }

  .steps {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  li {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }

  .command {
    display: block;
    padding: 0.35rem 0.6rem;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-surface-container-highest);
    font-size: 0.8rem;
    overflow-wrap: anywhere;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
</style>
