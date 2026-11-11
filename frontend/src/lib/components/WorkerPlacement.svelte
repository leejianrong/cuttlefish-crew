<script lang="ts">
  import { FleetApiError, type FleetClient, type ProjectSummary, type WorkerInfo } from "../api";
  import { whereItRuns } from "../placement";
  import { pickerNote } from "../workerhealth";

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
  /** A move waiting for the person to say they have read the unpushed-work warning. */
  let confirming = $state<{ workerId: string; warning: string; restart: boolean } | null>(null);

  const where = $derived(whereItRuns(project));
  const placed = $derived(project.worker ?? null);

  // The machines to choose from. Without a relay there are none, and the project simply runs here.
  $effect(() => {
    void placed?.online;
    client
      .listWorkersIfRelay()
      .then((result) => (workers = result.workers))
      .catch(() => (workers = []));
  });

  async function place(workerId: string, acknowledge = false, wasRunning = project.running) {
    working = true;
    error = null;
    confirming = null;
    const target = workerId === "" ? null : workerId;
    try {
      // A running team is carried over (stopped, started again there); an idle project is just placed.
      if (wasRunning) await client.moveProject(project.id, target, acknowledge, true);
      else await client.placeProject(project.id, target, acknowledge);
      onChanged();
    } catch (failure) {
      if (failure instanceof FleetApiError && failure.unpushed) {
        confirming = { workerId, warning: failure.message, restart: wasRunning };
        onChanged();
        return;
      }
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
        disabled={working}
        onchange={(event) => place((event.currentTarget as HTMLSelectElement).value)}
      >
        <option value="">This PC</option>
        {#if placed && placed.name === null}
          <option value={placed.id}>A worker that is no longer paired</option>
        {/if}
        {#each workers as worker (worker.id)}
          <option value={worker.id}>{worker.name}{pickerNote(worker)}</option>
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
      <p class="body-small muted">
        Choosing another machine stops the team and starts it again there, from the project's remote:
        the round in progress starts over.
      </p>
    {/if}
    {#if confirming}
      <div class="warning" role="alert">
        <p class="body-small">{confirming.warning}</p>
        <div class="actions">
          <button type="button" onclick={() => place(confirming!.workerId, true, confirming!.restart)}>Move anyway</button>
          <button type="button" class="quiet" onclick={() => (confirming = null)}>Cancel</button>
        </div>
      </div>
    {:else if project.worker_handover}
      <p class="body-small problem" role="status">{project.worker_handover}</p>
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

  .warning {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.6rem 0.8rem;
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
    border-radius: var(--md-sys-shape-corner-small);
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .actions button {
    min-height: 2.5rem;
    padding: 0.4rem 1rem;
    border: 1px solid currentColor;
    border-radius: var(--md-sys-shape-corner-full);
    background: transparent;
    color: inherit;
    cursor: pointer;
  }

  .actions .quiet {
    border-color: transparent;
  }

  .problem {
    color: var(--md-sys-color-error);
  }
</style>
