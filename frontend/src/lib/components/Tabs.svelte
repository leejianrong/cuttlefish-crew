<script lang="ts">
  export interface TabItem {
    id: string;
    label: string;
    /** Waiting items, shown beside the label in the attention colour; nothing at zero. */
    badge?: number;
  }

  let {
    tabs,
    active,
    onSelect,
    label,
  }: { tabs: TabItem[]; active: string; onSelect: (id: string) => void; label: string } = $props();

  function onKeydown(event: KeyboardEvent, index: number) {
    const last = tabs.length - 1;
    let next = index;
    if (event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else return;
    event.preventDefault();
    onSelect(tabs[next].id);
    queueMicrotask(() => {
      const tab = document.getElementById(`tab-${tabs[next].id}`);
      tab?.focus();
      // A tab past the edge of a narrow bar must scroll into view, not just take the focus ring.
      tab?.scrollIntoView({ inline: "nearest", block: "nearest" });
    });
  }
</script>

<div class="tabs" role="tablist" aria-label={label}>
  {#each tabs as tab, index (tab.id)}
    <button
      id="tab-{tab.id}"
      class="tab"
      class:active={tab.id === active}
      role="tab"
      aria-selected={tab.id === active}
      aria-controls="panel-{tab.id}"
      tabindex={tab.id === active ? 0 : -1}
      onclick={() => onSelect(tab.id)}
      onkeydown={(event) => onKeydown(event, index)}
    >
      {tab.label}
      {#if tab.badge}<span class="count" aria-label="{tab.badge} waiting">{tab.badge}</span>{/if}
    </button>
  {/each}
</div>

<style>
  .tabs {
    display: flex;
    gap: 8px;
    border-bottom: 1px solid var(--md-sys-color-outline-variant);
    margin-bottom: 1.5rem;
    overflow-x: auto;
  }

  .tab {
    all: unset;
    box-sizing: border-box;
    position: relative;
    padding: 12px 16px;
    cursor: pointer;
    white-space: nowrap;
    color: var(--md-sys-color-on-surface-variant);
    font: 500 0.875rem/1.25rem var(--md-ref-typeface-plain);
    letter-spacing: 0.00625rem;
    border-bottom: 3px solid transparent;
    margin-bottom: -1px;
    transition: background var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard);
  }

  /* Four tabs and a badge are wider than a phone: tighten them so Team is not cut off, and
     keep the bar scrollable (above) for the case the badge still tips it over. */
  @media (max-width: 480px) {
    .tabs {
      gap: 2px;
    }

    .tab {
      padding: 12px 8px;
      font-size: 0.8125rem;
    }

    .count {
      margin-left: 4px;
      padding: 0 5px;
    }
  }

  /* At 360px and below the four labels cannot share one line: let them shrink and wrap
     ("Needs you" onto two lines) rather than scroll the row. */
  @media (max-width: 400px) {
    .tabs {
      gap: 0;
    }

    .tab {
      flex: 1 1 auto;
      min-width: 0;
      padding: 8px 4px;
      text-align: center;
      white-space: normal;
    }
  }

  .count {
    display: inline-grid;
    place-items: center;
    min-width: 20px;
    height: 20px;
    margin-left: 8px;
    padding: 0 6px;
    box-sizing: border-box;
    border-radius: 10px;
    background: var(--md-sys-color-attention);
    color: var(--md-sys-color-on-attention);
    font: 700 0.75rem/1 var(--md-ref-typeface-plain);
  }

  .tab:hover {
    background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent);
  }

  .tab:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: -2px;
  }

  .tab.active {
    color: var(--md-sys-color-primary);
    border-bottom-color: var(--md-sys-color-primary);
    font-weight: 700;
  }
</style>
