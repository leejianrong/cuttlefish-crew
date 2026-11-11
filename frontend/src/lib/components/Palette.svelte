<script lang="ts">
  // The jump palette (CUT-294). A modal combobox: the input keeps focus (so Tab cannot leave it),
  // the arrow keys move the highlighted option, Enter goes, Escape closes and gives focus back.
  import type { NeedsYouRequest } from "../api";
  import { moveActive, paletteResults, type PaletteProject } from "../palette";
  import type { Route } from "../route";
  import Icon from "./Icon.svelte";

  let {
    projects,
    requests,
    current,
    onGo,
    onClose,
  }: {
    projects: PaletteProject[];
    requests: NeedsYouRequest[];
    current: Route | null;
    onGo: (route: Route) => void;
    onClose: () => void;
  } = $props();

  let query = $state("");
  let active = $state(0);
  let input: HTMLInputElement | undefined = $state();
  let list: HTMLUListElement | undefined = $state();
  const opener = document.activeElement as HTMLElement | null;
  const results = $derived(paletteResults(query, projects, requests, current));

  $effect(() => {
    input?.focus();
  });
  // A new query starts at the best match.
  $effect(() => {
    query;
    active = 0;
  });
  $effect(() => {
    list?.querySelector(`#opt-${active}`)?.scrollIntoView({ block: "nearest" });
  });

  function close() {
    onClose();
    opener?.focus?.();
  }

  function go(index: number) {
    const entry = results[index];
    if (!entry) return;
    onClose();
    onGo(entry.route);
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      active = moveActive(active, event.key === "ArrowDown" ? 1 : -1, results.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(active);
    } else if (event.key === "Tab") {
      // The input is the only stop: focus stays in the dialog.
      event.preventDefault();
    }
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="scrim" onclick={close}>
  <div
    class="dialog"
    role="dialog"
    aria-modal="true"
    aria-label="Jump to"
    tabindex="-1"
    onclick={(e) => e.stopPropagation()}
    onkeydown={onKeydown}
  >
    <div class="search">
      <Icon name="search" size={20} />
      <input
        bind:this={input}
        bind:value={query}
        role="combobox"
        aria-expanded="true"
        aria-controls="palette-list"
        aria-activedescendant={results.length > 0 ? `opt-${active}` : undefined}
        aria-autocomplete="list"
        aria-label="Jump to a project, tab or screen"
        placeholder="Jump to a project, tab or screen"
        autocomplete="off"
        spellcheck="false"
      />
      <kbd class="esc label-small">Esc</kbd>
    </div>
    <ul id="palette-list" role="listbox" aria-label="Results" bind:this={list}>
      {#each results as entry, i (entry.id)}
        {#if i === 0 || results[i - 1].group !== entry.group}
          <li class="group label-medium" role="presentation">{entry.group}</li>
        {/if}
        <li
          id={`opt-${i}`}
          role="option"
          aria-selected={i === active}
          class="option body-medium"
          class:active={i === active}
          onpointermove={() => (active = i)}
          onclick={() => go(i)}
        >
          <span class="label">{entry.label}</span>
          {#if entry.hint}<span class="hint body-small">{entry.hint}</span>{/if}
        </li>
      {/each}
      {#if results.length === 0}
        <li class="none body-medium" role="presentation">Nothing matches "{query.trim()}".</li>
      {/if}
    </ul>
    <p class="keys body-small" aria-hidden="true">Up and down to move, Enter to go, Esc to close</p>
  </div>
</div>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    padding: 12vh 12px 12px;
    background: color-mix(in srgb, var(--md-sys-color-scrim, #000) 40%, transparent);
  }

  .dialog {
    width: 100%;
    max-width: 36rem;
    max-height: 70vh;
    display: flex;
    flex-direction: column;
    border-radius: var(--md-sys-shape-corner-extra-large, 28px);
    background: var(--md-sys-color-surface-container-high, var(--md-sys-color-surface-container));
    color: var(--md-sys-color-on-surface);
    box-shadow: var(--md-sys-elevation-level3, var(--md-sys-elevation-level2));
    overflow: hidden;
  }

  .dialog:focus,
  .dialog:focus-visible {
    outline: none;
  }

  .search {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    height: 56px;
    border-bottom: 1px solid var(--md-sys-color-outline-variant);
    color: var(--md-sys-color-on-surface-variant);
  }

  input {
    flex: 1;
    min-width: 0;
    background: none;
    border: none;
    outline: none;
    color: var(--md-sys-color-on-surface);
    font: inherit;
    font-size: 1rem;
  }

  .esc {
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: 6px;
    padding: 2px 6px;
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 4px 8px;
    overflow-y: auto;
    flex: 1;
  }

  .group {
    padding: 12px 8px 4px;
    color: var(--md-sys-color-on-surface-variant);
  }

  .option {
    display: flex;
    align-items: baseline;
    gap: 12px;
    min-height: 44px;
    padding: 0 12px;
    align-items: center;
    border-radius: var(--md-sys-shape-corner-medium);
    cursor: pointer;
  }

  .option.active {
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .hint {
    color: var(--md-sys-color-on-surface-variant);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .option.active .hint {
    color: inherit;
  }

  .none {
    padding: 16px 12px;
    color: var(--md-sys-color-on-surface-variant);
  }

  .keys {
    margin: 0;
    padding: 8px 16px;
    color: var(--md-sys-color-on-surface-variant);
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  @media (max-width: 640px) {
    .scrim {
      padding-top: 8px;
    }
    .keys,
    .esc {
      display: none;
    }
  }
</style>
