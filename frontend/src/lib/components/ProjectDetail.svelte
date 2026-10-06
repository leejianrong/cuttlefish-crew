<script lang="ts">
  import type { EpisodicEventView, FleetClient, ProjectSummary } from "../api";
  import EventLog from "./EventLog.svelte";
  import Icon from "./Icon.svelte";
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
  <button class="btn btn-text back" onclick={onBack}><Icon name="back" size={18} />Projects</button>

  {#if project}
    <header>
      <h1 class="headline-small">{project.name}</h1>
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
            usage={project.usage[role]}
            budget={project.budget}
          />
        {/each}
      </section>
      <button class="btn btn-danger stop" onclick={stop}>Stop team</button>
    {:else}
      <section class="card filled start-form">
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
              {#if role.backend ?? project.backend}
                <span class="persona">[{role.backend ?? project.backend}]</span>
              {/if}
              {#if role.persona}
                <span class="persona persona-text" title={role.persona}>{role.persona}</span>
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
          <button class="btn btn-filled" onclick={start} disabled={starting}>
            {starting ? "Starting…" : "Start team"}
          </button>
        {/if}
      </section>
    {/if}

    <section class="card filled events">
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
    margin: 0 0 0.75rem -12px;
    padding: 0 16px 0 12px;
  }

  h1 {
    margin: 0;
  }

  .root {
    color: var(--text-faint);
    font-size: 0.8rem;
    margin: 0.3rem 0 1.75rem;
  }

  h2 {
    font: 500 1rem/1.5rem var(--md-ref-typeface-plain);
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
    border-radius: var(--md-sys-shape-corner-small);
    padding: 0.6rem 0.9rem;
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 1rem;
  }

  .stop {
    margin-bottom: 2rem;
  }

  .start-form {
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

  /* A built-in role's prompt runs to several lines; show two and keep the rest in the title. */
  .persona-text {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    margin: 0.15rem 0 0.1rem;
  }

  textarea {
    display: block;
    width: 100%;
    margin-top: 0.4rem;
    padding: 0.5rem 0.65rem;
    background: var(--bg-inset);
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-extra-small);
    color: var(--text);
    resize: vertical;
  }

  textarea:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 0;
    border-color: var(--md-sys-color-primary);
  }

  .error {
    color: var(--danger);
    font-size: 0.82rem;
  }

  .events {
    padding: 1.1rem 1.25rem;
  }
</style>
