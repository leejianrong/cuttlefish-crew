<script lang="ts">
  import type { EpisodicEventView, FleetClient, ProjectSummary } from "../api";
  import EventLog from "./EventLog.svelte";
  import OfficeScene from "./OfficeScene.svelte";
  import RoleSteerCard from "./RoleSteerCard.svelte";

  let {
    client,
    projectId,
    onBack,
  }: { client: FleetClient; projectId: string; onBack: () => void } = $props();

  let project = $state<ProjectSummary | null>(null);
  let events = $state<EpisodicEventView[]>([]);
  let starting = $state(false);
  let startError = $state<string | null>(null);
  let taskTexts = $state<Record<string, string>>({});
  let requireApproval = $state(false);

  // ADR-0010/KAN-1705: continuity, made visible rather than just trusted -- a
  // per-role timeline of handover checkpoints (RoleSteerCard renders each
  // role's own slice), and every TeamResumed marker this team's journal holds,
  // surfaced as its own banner above the role cards, not buried in the log.
  const handoversByRole = $derived.by(() => {
    const grouped: Record<string, EpisodicEventView[]> = {};
    for (const event of events) {
      if (event.event_type !== "HandoverWritten") continue;
      const role = (event.payload.role as string | null) ?? "";
      (grouped[role] ??= []).push(event);
    }
    return grouped;
  });
  const resumedEvents = $derived(events.filter((event) => event.event_type === "TeamResumed"));

  async function refresh() {
    project = await client.getProject(projectId);
    events = (await client.getEvents(projectId)).events;
  }

  $effect(() => {
    refresh();
    const interval = setInterval(refresh, 2500);
    return () => clearInterval(interval);
  });

  async function start() {
    if (!project) return;
    const roles = project.roles
      .map((role) => ({ name: role.name, text: (taskTexts[role.name] ?? "").trim() }))
      .filter((role) => role.text.length > 0);
    if (roles.length === 0) {
      startError = "give at least one role something to do";
      return;
    }
    starting = true;
    startError = null;
    try {
      await client.startProject(projectId, roles, requireApproval);
      taskTexts = {};
      await refresh();
    } catch {
      startError = "couldn't start that team -- check the daemon's own log";
    } finally {
      starting = false;
    }
  }

  async function stop() {
    await client.stopProject(projectId);
    await refresh();
  }
</script>

<div class="page">
  <button class="back" onclick={onBack}>&larr; Fleet</button>

  {#if project}
    <header>
      <h1>{project.name}</h1>
      <p class="root mono">{project.root}</p>
    </header>

    {#if resumedEvents.length > 0}
      <p class="resumed-banner">
        &#8635; this project's team was resumed after a restart {resumedEvents.length > 1
          ? `(${resumedEvents.length} times)`
          : ""} -- see "Recent activity" below for exactly where each pickup happened.
      </p>
    {/if}

    {#if project.running}
      <OfficeScene
        roles={Object.entries(project.status).map(([name, status]) => ({ name, status }))}
      />
      <section class="roles">
        {#each Object.entries(project.status) as [role, status] (role)}
          <RoleSteerCard
            {client}
            {projectId}
            {role}
            {status}
            handovers={handoversByRole[role] ?? []}
          />
        {/each}
      </section>
      <button class="stop" onclick={stop}>Stop team</button>
    {:else}
      <section class="start-form">
        <h2>Start a team</h2>
        {#if project.roles.length === 0}
          <p class="hint">
            This project has no registered roles yet. Register some from the fleet view
            first, or start it from the CLI: <code
              >cuttlefish run-team --root {project.root} --role NAME:TASK</code
            >.
          </p>
        {:else}
          {#each project.roles as role (role.name)}
            <label>
              <span class="role-label">{role.name}</span>
              {#if role.persona}
                <span class="persona">{role.persona}</span>
              {/if}
              <textarea
                bind:value={taskTexts[role.name]}
                placeholder="What should {role.name} do?"
                rows="2"
              ></textarea>
            </label>
          {/each}
          <label class="approval-toggle">
            <input type="checkbox" bind:checked={requireApproval} />
            Require my approval before each round finishes (KAN-1711)
          </label>
          {#if startError}
            <p class="error">{startError}</p>
          {/if}
          <button class="primary" onclick={start} disabled={starting}>
            {starting ? "Starting…" : "Start team"}
          </button>
        {/if}
      </section>
    {/if}

    <section class="events">
      <h2>Recent activity</h2>
      <EventLog {events} />
    </section>
  {/if}
</div>

<style>
  .page {
    max-width: 56rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }

  .back {
    background: none;
    border: none;
    color: var(--text-muted);
    padding: 0 0 1.25rem;
    font-size: 0.85rem;
  }

  .back:hover {
    color: var(--accent);
  }

  h1 {
    margin: 0;
    font-size: 1.4rem;
  }

  .root {
    color: var(--text-faint);
    font-size: 0.8rem;
    margin: 0.3rem 0 1.75rem;
  }

  h2 {
    font-size: 0.95rem;
    color: var(--text-muted);
    margin: 0 0 0.9rem;
  }

  .roles {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    margin: 0.9rem 0 1.25rem;
  }

  .resumed-banner {
    background: var(--status-blocked-bg);
    color: var(--status-blocked-fg);
    border: 1px solid var(--status-blocked-fg);
    border-radius: 8px;
    padding: 0.6rem 0.9rem;
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 1rem;
  }

  .stop {
    padding: 0.5rem 1rem;
    border: none;
    border-radius: 8px;
    background: var(--status-failed-bg);
    color: var(--status-failed-fg);
    font-weight: 600;
    margin-bottom: 2rem;
  }

  .start-form {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem;
    margin-bottom: 2rem;
  }

  .hint {
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.6;
  }

  .hint code {
    color: var(--text);
  }

  label {
    display: block;
    margin-bottom: 1rem;
  }

  .approval-toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
    color: var(--text-muted);
    font-weight: normal;
  }

  .approval-toggle input {
    width: auto;
  }

  .role-label {
    font-weight: 600;
    margin-right: 0.5rem;
  }

  .persona {
    color: var(--text-faint);
    font-size: 0.8rem;
  }

  textarea {
    display: block;
    width: 100%;
    margin-top: 0.4rem;
    padding: 0.5rem 0.65rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    resize: vertical;
  }

  button.primary {
    padding: 0.55rem 1.1rem;
    border: none;
    border-radius: 8px;
    background: var(--accent);
    color: var(--accent-text);
    font-weight: 600;
  }

  button.primary:disabled {
    opacity: 0.6;
  }

  .error {
    color: var(--danger);
    font-size: 0.82rem;
  }

  .events {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.1rem 1.25rem;
  }
</style>
