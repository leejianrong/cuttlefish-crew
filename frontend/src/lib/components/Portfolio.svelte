<script lang="ts">
  import { keepIfSame } from "../same";
  import { FleetApiError, refusal, type FleetClient, type ProjectSummary } from "../api";
  import { loadPins, rankProjects, savePins } from "../rank";
  import {
    filterProjects,
    groupProjects,
    isFiltering,
    limitGroups,
    loadCollapsed,
    loadGroupBy,
    NO_FILTER,
    saveCollapsed,
    saveGroupBy,
    stateCounts,
    tagCounts,
    type Filter,
    type GroupBy,
  } from "../grouping";
  import ProjectCard from "./ProjectCard.svelte";
  import ProjectFilters from "./ProjectFilters.svelte";
  import Icon from "./Icon.svelte";

  let {
    client,
    onOpenProject,
    onAddProject,
  }: {
    client: FleetClient;
    onOpenProject: (id: string) => void;
    onAddProject: () => void;
  } = $props();

  let projects = $state<ProjectSummary[]>([]);
  let waitingBy = $state<Record<string, number>>({});
  let pins = $state<Set<string>>(loadPins());
  // Ranked on every refresh from the same inputs, so the order only moves when a project's state does.
  const ranked = $derived(rankProjects(projects, waitingBy, pins));

  let filter = $state<Filter>(NO_FILTER);
  let groupBy = $state<GroupBy>(loadGroupBy());
  let collapsed = $state<Set<string>>(loadCollapsed());
  const visible = $derived(filterProjects(ranked, filter, waitingBy));
  const groups = $derived(groupProjects(visible, groupBy, waitingBy));
  // A fleet of 100 is drawn a chunk at a time, so the first screen does not wait for the last card.
  const CHUNK = 20;
  let drawn = $state(CHUNK);
  const drawing = $derived(limitGroups(groups, drawn));
  $effect(() => {
    if (drawn >= visible.length) return;
    const timer = setTimeout(() => (drawn += CHUNK), 30);
    return () => clearTimeout(timer);
  });
  const states = $derived(stateCounts(projects, waitingBy));
  const tags = $derived(tagCounts(projects));

  function toggleGroup(key: string) {
    const next = new Set(collapsed);
    if (!next.delete(key)) next.add(key);
    collapsed = next;
    saveCollapsed(next);
  }

  async function setTags(id: string, next: string[]) {
    actionError = null;
    try {
      await client.setTags(id, next);
    } catch (error) {
      // A 400 says which rule the tags broke ("a tag is at most 24 characters").
      actionError =
        refusal(error) ??
        (error instanceof FleetApiError && error.status === 400
          ? error.message
          : "Couldn't save those tags. Check that the daemon is still running.");
    }
    await refresh();
  }

  function togglePin(id: string) {
    const next = new Set(pins);
    if (!next.delete(id)) next.add(id);
    pins = next;
    savePins(next);
  }
  let loaded = $state(false);
  let unreachable = $state(false);

  async function refresh() {
    try {
      const result = await client.listProjects();
      projects = keepIfSame(projects, result.projects);
      waitingBy = await waitingCounts();
      unreachable = false;
      loaded = true;
    } catch {
      unreachable = true;
    }
  }

  /** How many requests each project has waiting; a failure here must not hide the projects. */
  async function waitingCounts(): Promise<Record<string, number>> {
    try {
      const counts: Record<string, number> = {};
      for (const request of (await client.listRequests()).requests) {
        counts[request.project_id] = (counts[request.project_id] ?? 0) + 1;
      }
      return counts;
    } catch {
      return waitingBy;
    }
  }

  $effect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  });

  let actionError = $state<string | null>(null);

  async function stop(id: string) {
    actionError = null;
    try {
      await client.stopProject(id);
    } catch (error) {
      actionError = refusal(error) ?? "Couldn't stop that team. Check that the daemon is still running.";
    }
    await refresh();
  }

  async function remove(id: string) {
    actionError = null;
    try {
      await client.deregisterProject(id);
    } catch (error) {
      actionError =
        refusal(error) ?? "Couldn't remove that project. Check that the daemon is still running.";
    }
    await refresh();
  }
</script>

<div class="page">
  <header class="topbar">
    <h1 class="headline-medium">Projects</h1>
    <span class="endpoint mono">{client.baseUrl}</span>
    <button class="btn btn-filled add" onclick={onAddProject}>
      <Icon name="plus" size={18} />Add project
    </button>
  </header>

  {#if actionError}
    <p class="warning" role="alert">{actionError}</p>
  {/if}

  {#if unreachable}
    <p class="warning">Lost connection to the daemon. Retrying…</p>
  {/if}

  {#if projects.length > 1}
    <ProjectFilters
      {filter}
      {groupBy}
      stateCount={states}
      {tags}
      onFilter={(next) => (filter = next)}
      onGroupBy={(next) => {
        groupBy = next;
        saveGroupBy(next);
      }}
    />
  {/if}

  {#each drawing as group (group.key)}
    {@const open = groupBy === "none" || !collapsed.has(group.key)}
    {#if groupBy !== "none"}
      <h2 class="group-head title-medium">
        <button aria-expanded={open} onclick={() => toggleGroup(group.key)}>
          <Icon name={open ? "chevron-down" : "chevron-right"} size={18} />
          {group.label}
          <span class="group-count body-small">{group.projects.length}</span>
        </button>
      </h2>
    {/if}
    {#if open}
      <div class="grid">
        {#each group.shown as project (project.id)}
          <ProjectCard
            {project}
            waiting={waitingBy[project.id] ?? 0}
            pinned={pins.has(project.id)}
            onPin={() => togglePin(project.id)}
            onTags={(next) => setTags(project.id, next)}
            onOpen={() => onOpenProject(project.id)}
            onStop={() => stop(project.id)}
            onRemove={() => remove(project.id)}
          />
        {/each}
      </div>
    {/if}
  {/each}

  {#if projects.length > 0 && visible.length === 0 && isFiltering(filter)}
    <div class="card filled empty-state">
      <h2 class="title-large">No project matches</h2>
      <p class="body-medium muted">None of your {projects.length} projects fits this filter.</p>
      <button class="btn btn-tonal" onclick={() => (filter = NO_FILTER)}>Clear filters</button>
    </div>
  {/if}

  {#if loaded && !unreachable && projects.length === 0}
    <div class="card filled empty-state">
      <h2 class="title-large">No projects yet</h2>
      <p class="body-medium muted">
        A project is a folder with a team of agents working in it. Pick a folder and a team and
        you can start a first task in a couple of minutes.
      </p>
      <button class="btn btn-filled" onclick={onAddProject}>
        <Icon name="plus" size={18} />Add your first project
      </button>
    </div>
  {/if}
</div>

<style>
  .page {
    max-width: 72rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }

  .topbar {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
    margin-bottom: 1.75rem;
  }

  h1 {
    margin: 0;
  }

  .endpoint {
    color: var(--text-faint);
    font-size: 0.78rem;
  }

  .add {
    margin-left: auto;
    padding-left: 16px;
  }

  .warning {
    background: var(--status-blocked-bg);
    color: var(--status-blocked-fg);
    padding: 0.6rem 0.9rem;
    border-radius: var(--md-sys-shape-corner-small);
    font-size: 0.85rem;
    margin-bottom: 1.25rem;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
    gap: 1rem;
  }

  .group-head {
    margin: 1.5rem 0 0.75rem;
  }

  .group-head button {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: none;
    border: none;
    padding: 0;
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }

  .group-count {
    color: var(--text-muted);
  }

  /* A thumb needs 44px (CUT-98). */
  @media (max-width: 640px) {
    .group-head button {
      min-height: 44px;
    }
  }

  .empty-state {
    margin-top: 1.5rem;
    padding: 1.5rem;
    max-width: 32rem;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.75rem;
  }

  .empty-state h2,
  .empty-state p {
    margin: 0;
  }
</style>
