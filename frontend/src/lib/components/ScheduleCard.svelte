<script lang="ts">
  // Scheduled goals (CUT-246, ADR-0051): ask the project's builder for a goal on a recurrence.
  // The daemon starts it unattended; it never interrupts a builder that is still on its last goal.
  import type { FleetClient, Schedule } from "../api";
  import {
    KINDS,
    buildRecurrence,
    canAdd,
    describeRecurrence,
    nextLine,
    resultLine,
    type RecurrenceKind,
  } from "../schedule";

  let {
    client,
    projectId,
    manager = false,
  }: { client: FleetClient; projectId: string; manager?: boolean } = $props();

  let schedules = $state<Schedule[]>([]);
  let goal = $state("");
  let kind = $state<RecurrenceKind>("every-h");
  let every = $state(1);
  let time = $state("06:00");
  let busy = $state(false);
  let confirming = $state<string | null>(null);
  let error = $state<string | null>(null);
  let now = $state(new Date());
  let shownFor = "";

  const recurrence = $derived(buildRecurrence(kind, Number(every), time));

  async function read(id: string) {
    try {
      const next = await client.getSchedules(id);
      if (id === projectId) schedules = next;
    } catch {
      // The page's own connection banner says so; this card keeps what it last read.
    }
  }

  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      schedules = [];
      error = null;
      shownFor = id;
    }
    void read(id);
    const timer = setInterval(() => {
      now = new Date();
      void read(id);
    }, 5000);
    return () => clearInterval(timer);
  });

  async function act(work: () => Promise<unknown>) {
    error = null;
    try {
      await work();
      await read(projectId);
    } catch (e) {
      error = e instanceof Error ? e.message : "That didn't work.";
    }
  }

  async function add() {
    if (busy || !canAdd(goal, recurrence) || recurrence === null) return;
    busy = true;
    await act(async () => {
      await client.addSchedule(projectId, goal.trim(), recurrence);
      goal = "";
    });
    busy = false;
  }
</script>

<section class="card filled schedules" aria-labelledby="schedules-heading">
  <h2 id="schedules-heading" class="title-large">
    {manager ? "Scheduled check-ins" : "Scheduled goals"}
  </h2>
  {#if manager}
    <p class="body-medium muted">
      The daemon types this message into the manager's session on a schedule, so it looks over your
      projects while you are away. Claude Code only. A run is skipped, and says so here, while the
      manager is busy or waiting on a permission. Times are UTC.
    </p>
  {:else}
    <p class="body-medium muted">
      The daemon gives the builder a goal on a schedule, with Autopilot on, while you are away.
      Each run waits at the plan for your approval under Needs you. It never interrupts a builder that
      is still on its last goal: that run is skipped and says so here. Times are UTC.
    </p>
  {/if}

  {#if schedules.length}
    <ul class="list" data-testid="schedule-list">
      {#each schedules as s (s.id)}
        <li class="row" data-testid="schedule-row">
          <div class="what">
            <p class="body-large goal-text">{s.goal}</p>
            <p class="body-medium">{describeRecurrence(s.recurrence)}. Next: {nextLine(s, now)}</p>
            {#if resultLine(s)}
              <p class="body-small muted" data-testid="schedule-result">{resultLine(s)}</p>
            {/if}
          </div>
          <div class="actions">
            <button
              class="btn btn-tonal"
              onclick={() => act(() => client.setScheduleEnabled(s.id, !s.enabled))}
            >
              {s.enabled ? "Pause" : "Resume"}
            </button>
            {#if confirming === s.id}
              <button
                class="btn btn-text"
                onclick={() => {
                  confirming = null;
                  void act(() => client.deleteSchedule(s.id));
                }}
              >
                Yes, remove it
              </button>
              <button class="btn btn-text" onclick={() => (confirming = null)}>Keep it</button>
            {:else}
              <button class="btn btn-text" onclick={() => (confirming = s.id)}>Remove</button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {:else}
    <p class="body-medium muted" data-testid="no-schedules">No schedules yet.</p>
  {/if}

  <form
    class="form"
    onsubmit={(event) => {
      event.preventDefault();
      void add();
    }}
  >
    <label class="field">
      <span class="field-label">{manager ? "Check-in message" : "Goal for each run"}</span>
      <textarea
        bind:value={goal}
        rows="2"
        placeholder={manager ? "What should the manager look at?" : "What should the builder do?"}
      ></textarea>
    </label>
    <div class="when">
      <label class="field">
        <span class="field-label">Repeat</span>
        <select bind:value={kind}>
          {#each KINDS as k (k.id)}<option value={k.id}>{k.label}</option>{/each}
        </select>
      </label>
      {#if kind === "daily"}
        <label class="field">
          <span class="field-label">Time (UTC)</span>
          <input type="time" bind:value={time} />
        </label>
      {:else}
        <label class="field">
          <span class="field-label">N</span>
          <input type="number" min="1" max={kind === "every-m" ? 43200 : 720} step="1" bind:value={every} />
        </label>
      {/if}
    </div>
    {#if error}<p class="body-small error" role="alert">{error}</p>{/if}
    <button class="btn btn-filled" type="submit" disabled={busy || !canAdd(goal, recurrence)}>
      Add schedule
    </button>
  </form>
</section>

<style>
  .schedules {
    padding: 1.5rem;
    margin-bottom: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
    max-width: 56rem;
  }

  .schedules h2,
  .schedules p {
    margin: 0;
  }

  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.75rem 1rem;
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: var(--md-sys-color-surface-container);
  }

  .what {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    min-width: 0;
    flex: 1 1 16rem;
  }

  .goal-text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
  }

  .form {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    align-items: flex-start;
  }

  .form .field {
    align-self: stretch;
  }

  .when {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    align-self: stretch;
  }

  .when .field {
    flex: 1 1 10rem;
    align-self: auto;
  }

  .schedules :global(.field-label) {
    background: var(--md-sys-color-surface-container-low);
  }

  .error {
    color: var(--md-sys-color-error);
  }
</style>
