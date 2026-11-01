<script lang="ts">
  import type { Filter, GroupBy } from "../grouping";
  import { isFiltering, NO_FILTER } from "../grouping";
  import { STATE_LABEL, type ProjectState } from "../projectstate";

  let {
    filter,
    groupBy,
    stateCount,
    tags,
    onFilter,
    onGroupBy,
  }: {
    filter: Filter;
    groupBy: GroupBy;
    stateCount: Record<ProjectState, number>;
    tags: { tag: string; count: number }[];
    onFilter: (next: Filter) => void;
    onGroupBy: (next: GroupBy) => void;
  } = $props();

  const STATES: ProjectState[] = ["waiting", "stuck", "working", "done", "idle"];

  function toggle<T>(set: ReadonlySet<T>, value: T): Set<T> {
    const next = new Set(set);
    if (!next.delete(value)) next.add(value);
    return next;
  }
</script>

<div class="filters" role="group" aria-label="Filter and group projects">
  <div class="row scroll" role="group" aria-label="State">
    {#each STATES as state (state)}
      <button
        class="chip"
        class:selected={filter.states.has(state)}
        aria-pressed={filter.states.has(state)}
        disabled={stateCount[state] === 0 && !filter.states.has(state)}
        onclick={() => onFilter({ ...filter, states: toggle(filter.states, state) })}
      >
        {STATE_LABEL[state]} <span class="count">{stateCount[state]}</span>
      </button>
    {/each}
  </div>

  {#if tags.length > 0}
    <div class="row scroll" role="group" aria-label="Tags">
      {#each tags as { tag, count } (tag)}
        <button
          class="chip"
          class:selected={filter.tags.has(tag)}
          aria-pressed={filter.tags.has(tag)}
          onclick={() => onFilter({ ...filter, tags: toggle(filter.tags, tag) })}
        >
          {tag} <span class="count">{count}</span>
        </button>
      {/each}
    </div>
  {/if}

  <div class="row tools">
    <label class="group-by body-small">
      Group by
      <select value={groupBy} onchange={(e) => onGroupBy(e.currentTarget.value as GroupBy)}>
        <option value="none">Nothing</option>
        <option value="tag">First tag</option>
        <option value="state">State</option>
      </select>
    </label>
    {#if isFiltering(filter)}
      <button class="btn btn-text sm" onclick={() => onFilter(NO_FILTER)}>Clear filters</button>
    {/if}
  </div>
</div>

<style>
  .filters {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    margin-bottom: 1.25rem;
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
  }

  /* Twenty tags must not push the projects off a phone: this row scrolls sideways instead. */
  .scroll {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 0.25rem;
  }

  .chip {
    cursor: pointer;
    flex-shrink: 0;
  }

  .chip:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .count {
    font-variant-numeric: tabular-nums;
    opacity: 0.7;
  }

  .tools {
    justify-content: space-between;
  }

  .group-by {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--text-muted);
  }

  select {
    min-height: 32px;
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-small);
    background: transparent;
    color: var(--text);
    padding: 0 0.5rem;
  }

  @media (max-width: 640px) {
    .chip,
    select {
      height: auto;
      min-height: 44px;
    }
  }
</style>
