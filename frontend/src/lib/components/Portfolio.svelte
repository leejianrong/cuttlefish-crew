<script lang="ts">
  import { keepIfSame } from "../same";
  import type { FleetClient, ProjectSummary } from "../api";
  import ProjectCard from "./ProjectCard.svelte";
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
  let loaded = $state(false);
  let unreachable = $state(false);

  async function refresh() {
    try {
      const result = await client.listProjects();
      projects = keepIfSame(projects, result.projects);
      unreachable = false;
    } catch {
      unreachable = true;
    } finally {
      loaded = true;
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
    } catch {
      actionError = "Couldn't stop that team. Check that the daemon is still running.";
    }
    await refresh();
  }

  async function remove(id: string) {
    actionError = null;
    try {
      await client.deregisterProject(id);
    } catch {
      actionError = "Couldn't remove that project. Check that the daemon is still running.";
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

  <div class="grid">
    {#each projects as project (project.id)}
      <ProjectCard
        {project}
        onOpen={() => onOpenProject(project.id)}
        onStop={() => stop(project.id)}
        onRemove={() => remove(project.id)}
      />
    {/each}
  </div>

  {#if loaded && projects.length === 0}
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
