<script lang="ts">
  // The lead group of the Projects screen (CUT-284): every project waiting on a person, one
  // compact row each with the reason, as herdr's Inbox leads with Needs you. The cards below are
  // the rest.
  import type { NeedsYouRequest, ProjectSummary } from "../api";
  import { projectState } from "../projectstate";
  import { needsReason } from "../requests";
  import Icon from "./Icon.svelte";

  let {
    projects,
    requestsBy,
    onOpen,
  }: {
    projects: ProjectSummary[];
    requestsBy: Record<string, NeedsYouRequest[]>;
    onOpen: (id: string) => void;
  } = $props();

  // Five rows is about a screen on a phone; the rest are one tap away.
  const LIMIT = 5;
  let all = $state(false);
  const shown = $derived(all ? projects : projects.slice(0, LIMIT));
  const reason = (p: ProjectSummary) => {
    const asks = requestsBy[p.id] ?? [];
    const text = needsReason(asks, projectState(p, asks.length).reason);
    return text.charAt(0).toUpperCase() + text.slice(1);
  };
</script>

<section class="needs" aria-labelledby="needs-you-head">
  <h2 id="needs-you-head" class="title-medium">
    Needs you <span class="count body-small">{projects.length}</span>
  </h2>
  <ul>
    {#each shown as project (project.id)}
      <li>
        <button class="row" onclick={() => onOpen(project.id)}>
          <span class="name title-small">{project.name}</span>
          <span class="why body-small">{reason(project)}</span>
          <Icon name="chevron-right" size={18} />
        </button>
      </li>
    {/each}
  </ul>
  {#if projects.length > LIMIT}
    <button class="btn btn-text more" aria-expanded={all} onclick={() => (all = !all)}>
      {all ? "Show fewer" : `Show all ${projects.length}`}
    </button>
  {/if}
</section>

<style>
  .needs {
    margin-bottom: 1.5rem;
  }

  h2 {
    margin: 0 0 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .count {
    background: var(--md-sys-color-tertiary-container);
    color: var(--md-sys-color-on-tertiary-container);
    border-radius: var(--md-sys-shape-corner-full);
    padding: 0 0.5rem;
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    background: var(--md-sys-color-surface-container);
    border-radius: var(--md-sys-shape-corner-large);
    overflow: hidden;
  }

  li + li {
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  .row {
    display: grid;
    grid-template-columns: minmax(8rem, 14rem) 1fr auto;
    align-items: center;
    gap: 0.75rem;
    width: 100%;
    min-height: 48px;
    padding: 0.5rem 1rem;
    background: none;
    border: none;
    color: var(--md-sys-color-on-surface);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  .row:hover {
    background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent);
  }

  .name,
  .why {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .why {
    color: var(--md-sys-color-on-surface-variant);
  }

  .more {
    margin-top: 0.25rem;
  }

  @media (max-width: 640px) {
    .row {
      grid-template-columns: 1fr auto;
      row-gap: 0;
    }
    .why {
      grid-column: 1;
      grid-row: 2;
    }
    .row :global(.icon) {
      grid-column: 2;
      grid-row: 1 / span 2;
    }
    .more {
      min-height: 44px;
    }
  }
</style>
