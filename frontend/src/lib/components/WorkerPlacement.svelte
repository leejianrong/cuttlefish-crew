<script lang="ts">
  import { FleetApiError, type FleetClient, type ProjectSummary, type WorkerInfo } from "../api";
  import { whereItRuns } from "../placement";

  let {
    client,
    project,
    onChanged,
  }: {
    client: FleetClient;
    project: ProjectSummary;
    onChanged: () => void;
  } = $props();

  let workers = $state<WorkerInfo[]>([]);
  let working = $state(false);
  let error = $state<string | null>(null);

  const where = $derived(whereItRuns(project));
  const placed = $derived(project.worker ?? null);

  // The machines to choose from. Without a relay there are none, and the project simply runs here.
  $effect(() => {
    void placed?.online;
    client
      .listWorkers()
      .then((result) => (workers = result.workers))
      .catch(() => (workers = []));
  });

  async function place(workerId: string) {
    working = true;
    error = null;
    try {
      await client.placeProject(project.id, workerId === "" ? null : workerId);
      onChanged();
    } catch (failure) {
      error = failure instanceof FleetApiError || failure instanceof Error ? failure.message : "That did not work.";
    } finally {
      working = false;
    }
  }
</script>

{#if workers.length > 0 || placed}
  <div class="where">
    <label>
      <span class="label-medium muted">Runs on</span>
      <select
        value={placed?.id ?? ""}
        disabled={working || project.running}
        onchange={(event) => place((event.currentTarget as HTMLSelectElement).value)}
      >
        <option value="">This PC</option>
        {#if placed && placed.name === null}
          <option value={placed.id}>A worker that is no longer paired</option>
        {/if}
        {#each workers as worker (worker.id)}
          <option value={worker.id}>{worker.name}{worker.online ? "" : " (offline)"}</option>
        {/each}
      </select>
    </label>
    {#if placed}
      <p class="body-small" class:problem={where.problem} role="status">
        {#if where.problem}
          {where.text} cannot be reached, so a team will not start until it is back or the project is moved.
        {:else}
          Teams run on {where.text}, in its own copy of the project. The builder chat is not available on a
          worker yet; use Start team.
        {/if}
      </p>
    {/if}
    {#if project.running}
      <p class="body-small muted">Stop the team to move the project.</p>
    {/if}
    {#if error}<p class="body-small problem" role="status">{error}</p>{/if}
  </div>
{/if}

<style>
  .where {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    margin: 0.5rem 0;
  }

  label {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem;
  }

  select {
    padding: 0.4rem 0.6rem;
    background: transparent;
    color: var(--text);
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-extra-small);
  }

  p {
    margin: 0;
  }

  .muted {
    color: var(--text-muted);
  }

  .problem {
    color: var(--md-sys-color-error);
  }
</style>
