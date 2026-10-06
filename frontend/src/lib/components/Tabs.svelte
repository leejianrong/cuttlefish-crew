<script lang="ts">
  export interface TabItem {
    id: string;
    label: string;
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
    queueMicrotask(() => document.getElementById(`tab-${tabs[next].id}`)?.focus());
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
