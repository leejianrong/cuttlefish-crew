<script lang="ts">
  import { tick } from "svelte";
  import {
    FleetApiError,
    type EpisodicEventView,
    type FleetClient,
    type NeedsYouRequest,
    type PrepareInfo,
    type ProjectSummary,
  } from "../api";
  import { installProgress } from "../environment";
  import { latestRound } from "../events";
  import { lastRun, runFigures, runHeading } from "../lastrun";
  import type { ProjectTab } from "../route";
  import { keepIfSame } from "../same";
  import { modeLabel, startFailure } from "../team";
  import EnvironmentCard from "./EnvironmentCard.svelte";
  import EventLog from "./EventLog.svelte";
  import Icon from "./Icon.svelte";
  import NeedsYouTab from "./NeedsYouTab.svelte";
  import PrepareConfirm from "./PrepareConfirm.svelte";
  import { awaitingDecision, isFinishing } from "../roles";
  import OfficeScene from "./OfficeScene.svelte";
  import PermissionsTab from "./PermissionsTab.svelte";
  import RoleSteerCard from "./RoleSteerCard.svelte";
  import Tabs from "./Tabs.svelte";
  import TeamTab from "./TeamTab.svelte";

  let {
    client,
    projectId,
    tab,
    onTabChange,
    onBack,
  }: {
    client: FleetClient;
    projectId: string;
    /** Which tab is open: part of the URL, so a reload and Back keep it. */
    tab: ProjectTab;
    onTabChange: (tab: ProjectTab) => void;
    onBack: () => void;
  } = $props();

  let project = $state<ProjectSummary | null>(null);
  let events = $state<EpisodicEventView[]>([]);
  let pending = $state<NeedsYouRequest[]>([]);
  let resolved = $state<NeedsYouRequest[]>([]);
  let requestsFetchedAt = $state(Date.now());
  let unreachable = $state(false);
  // The daemon answered 404: no project has this id (a stale link, or it was removed).
  let missing = $state(false);
  let starting = $state(false);
  let startError = $state<string | null>(null);
  let taskTexts = $state<Record<string, string>>({});
  let requireApproval = $state(false);
  // V5-E3: asked before installing dependencies, when the project's setting is "ask".
  let confirmingInstall = $state<PrepareInfo | null>(null);
  const installing = $derived(installProgress(events));
  let startButton = $state<HTMLButtonElement | null>(null);
  // The Environment card reads again when an install may have changed what it shows.
  const environmentKey = $derived(
    `${project?.env_prepare}|${project?.running}|${events.filter((e) => e.event_type.startsWith("Environment")).length}`,
  );

  async function cancelInstall() {
    confirmingInstall = null;
    await tick();
    startButton?.focus();
  }
  let confirmingStop = $state(false);
  let stopping = $state(false);
  let permissionsDirty = $state(false);
  let teamDirty = $state(false);
  // The scene's roles. A role finishing well is not asking for anything: no attention mark on its sprite.
  const sceneRoles = $derived.by(() => {
    const current = project;
    if (!current) return [];
    return Object.entries(current.status).map(([name, status]) => ({
          name,
          status: isFinishing(
            status,
            latestRound(events, name),
            pending.some((r) => r.kind === "blocked" && r.role === name),
            awaitingDecision(
              current.require_approval,
              current.usage[name] ?? { tokens: 0, cost_usd: null },
              current.budget,
            ),
          )
            ? ("done" as const)
            : status,
        }));
  });

  // Once nothing is running, say how the last run ended instead of looking like it never started.
  const ended = $derived(
    project && !project.running ? lastRun(events, project.status, project.usage) : null,
  );

  const TABS = $derived([
    { id: "overview", label: "Overview" },
    { id: "needs-you", label: "Needs you", badge: pending.length },
    { id: "permissions", label: "Permissions" },
    { id: "team", label: "Team" },
  ]);

  // A starting point for a blank task box; picking one fills the first role's task.
  const EXAMPLES = [
    "Run the tests and fix anything that fails.",
    "Review the most recent commits for bugs.",
    "Find the most under-tested module and add tests for it.",
  ];

  // Continuity, made visible rather than just trusted: a per-role timeline of handover
  // checkpoints (RoleSteerCard renders each role's own slice), and every resume marker this
  // team's journal holds, surfaced as a banner above the role cards, not buried in the log.
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
  const summaryLine = $derived(
    project
      ? `${modeLabel(project.mode)} mode · ${
          project.roles.map((role) => role.name).join(", ") || "no roles"
        }`
      : "",
  );
  const noTaskYet = $derived(
    project !== null && project.roles.every((role) => !(taskTexts[role.name] ?? "").trim()),
  );

  async function refresh() {
    try {
      // Only what changed is taken: a poll that finds the same data must not re-render the page.
      project = keepIfSame(project, await client.getProject(projectId));
      events = keepIfSame(events, (await client.getEvents(projectId)).events);
      const requests = await client.listProjectRequests(projectId);
      pending = keepIfSame(pending, requests.pending);
      resolved = keepIfSame(resolved, requests.resolved);
      requestsFetchedAt = Date.now();
      unreachable = false;
      missing = false;
    } catch (error) {
      if (error instanceof FleetApiError && error.status === 404) missing = true;
      else unreachable = true;
    }
  }

  $effect(() => {
    refresh();
    // A project that is not there is not worth asking about again every 2.5 seconds.
    const interval = setInterval(() => {
      if (!missing) refresh();
    }, 2500);
    return () => clearInterval(interval);
  });

  // Unsaved Permissions and Team edits are what a stray reload would lose (leaving the project
  // keeps them: lib/drafts.ts).
  $effect(() => {
    if (!permissionsDirty && !teamDirty) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  });

  async function start(prepare?: "yes" | "skip") {
    if (!project) return;
    const roles = project.roles
      .map((role) => ({ name: role.name, text: (taskTexts[role.name] ?? "").trim() }))
      .filter((role) => role.text.length > 0);
    if (roles.length === 0) {
      startError = "Give at least one role something to do.";
      return;
    }
    starting = true;
    startError = null;
    try {
      if (prepare === undefined) {
        // Ask before installing: the project's install scripts run project code, so unless the
        // person already said "automatically" (or "never"), say what will run and let them choose.
        const environment = await client.getEnvironment(projectId).catch(() => null);
        if (
          environment !== null &&
          environment.prepare.setting === "ask" &&
          environment.prepare.steps.length > 0
        ) {
          confirmingInstall = environment.prepare;
          return;
        }
      }
      confirmingInstall = null;
      await client.startProject(projectId, roles, requireApproval, prepare);
      taskTexts = {};
      await refresh();
    } catch (error) {
      startError = startFailure(error);
    } finally {
      starting = false;
    }
  }

  async function chooseInstall(choice: "once" | "always" | "skip") {
    if (choice === "always") {
      try {
        await client.updateEnvironmentPrepare(projectId, "auto");
      } catch (error) {
        startError = startFailure(error);
        return;
      }
    }
    await start(choice === "skip" ? "skip" : "yes");
  }

  async function stop() {
    stopping = true;
    try {
      await client.stopProject(projectId);
      confirmingStop = false;
      await refresh();
    } catch {
      startError = "Couldn't stop the team. Check that the daemon is still running.";
    } finally {
      stopping = false;
    }
  }

  function useExample(text: string) {
    const first = project?.roles[0];
    if (first) taskTexts[first.name] = text;
  }
</script>

<div class="page">
  <button class="btn btn-text back" onclick={onBack}><Icon name="back" size={18} />Projects</button>

  {#if missing}
    <p class="body-large" role="status">
      There is no project with this address. It may have been removed, or the link is out of date.
    </p>
    <button class="btn btn-tonal" onclick={onBack}>Back to your projects</button>
  {:else if unreachable}
    <p class="warning" role="status">Lost connection to the daemon. Retrying…</p>
  {/if}

  {#if project}
    <header>
      <h1 class="headline-small">{project.name}</h1>
      <p class="root mono">{project.root}</p>
    </header>

    <Tabs tabs={TABS} active={tab} label="Project" onSelect={(id) => onTabChange(id as ProjectTab)} />

    <div id="panel-overview" role="tabpanel" aria-labelledby="tab-overview" hidden={tab !== "overview"}>
      {#if resumedEvents.length > 0}
        <p class="resumed-banner">
          &#8635; This project's team was resumed after a restart{resumedEvents.length > 1
            ? ` (${resumedEvents.length} times)`
            : ""}. Recent activity below shows where each pickup happened.
        </p>
      {/if}

      {#if project.running && installing}
        <p class="banner" role="status">
          Installing the {installing.name} dependencies ({installing.reason}):
          <code class="mono">{installing.commands}</code>. The team starts when it is done; Stop team
          cancels it.
        </p>
      {/if}

      {#if project.running}
        <OfficeScene
          roles={sceneRoles}
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
              requireApproval={project.require_approval}
              lastRound={latestRound(events, role)}
              held={pending.some((r) => r.kind === "blocked" && r.role === role)}
            />
          {/each}
        </section>
        {#if project.stopping}
          <p class="banner" role="status">
            Stopping. The agent finishes the step it is on first, which can take a minute or two.
            Anything it asks for in the meantime is refused.
          </p>
        {:else if confirmingStop}
          <div class="confirm" role="alert">
            <span class="body-medium">
              Stop the team? Its roles stop, and starting again begins a new run.
            </span>
            <button class="btn btn-danger" disabled={stopping} onclick={stop}>Stop team</button>
            <button class="btn btn-text" onclick={() => (confirmingStop = false)}>Keep running</button>
          </div>
        {:else}
          <button class="btn btn-danger stop" onclick={() => (confirmingStop = true)}>
            Stop team
          </button>
        {/if}
        {#if startError}<p class="error" role="alert">{startError}</p>{/if}
      {:else}
        {#if ended}
          <section class="card filled last-run" class:bad={ended.outcome === "failed"} aria-labelledby="last-run-heading">
            <h2 id="last-run-heading" class="title-medium">{runHeading(ended)}</h2>
            <p class="body-medium muted">{runFigures(ended)}</p>
            {#each ended.failures as failure (failure.role)}
              <p class="body-medium"><strong>{failure.role}</strong>{failure.error ? `: ${failure.error}` : ""}</p>
            {/each}
          </section>
        {/if}
        <section class="card filled start-form" aria-labelledby="start-heading">
          <h2 id="start-heading" class="title-large">Start a team</h2>
          {#if project.roles.length === 0}
            <p class="hint">
              This project has no roles yet. Add some on the Team tab, or start one from the
              command line:
              <code>cuttlefish run-team --root {project.root} --role NAME:TASK</code>.
            </p>
          {:else}
            <p class="body-medium muted summary">{summaryLine}</p>
            {#if noTaskYet}
              <div class="examples">
                <span class="label-medium muted">Try</span>
                {#each EXAMPLES as example (example)}
                  <button type="button" class="chip" onclick={() => useExample(example)}>
                    {example}
                  </button>
                {/each}
              </div>
            {/if}
            {#each project.roles as role (role.name)}
              <div class="task">
                <label class="field">
                  <span class="field-label">
                    {role.name}{role.backend ?? project.backend
                      ? ` · ${role.backend ?? project.backend}`
                      : ""}
                  </span>
                  <textarea
                    bind:value={taskTexts[role.name]}
                    placeholder="What should {role.name} do?"
                    rows="2"
                  ></textarea>
                </label>
                {#if role.persona}
                  <details class="prompt">
                    <summary>What {role.name} is told</summary>
                    <p>{role.persona}</p>
                  </details>
                {/if}
              </div>
            {/each}
            <div class="approval">
              <button
                type="button"
                class="switch"
                role="switch"
                aria-checked={requireApproval}
                aria-labelledby="approval-label"
                onclick={() => (requireApproval = !requireApproval)}
              ></button>
              <span class="approval-text">
                <span id="approval-label" class="title-small">Approve each round before it ends</span>
                <span class="body-small muted">
                  Every role pauses at the end of a round until you approve or reject it.
                </span>
              </span>
            </div>
            {#if confirmingInstall}
              <PrepareConfirm
                prepare={confirmingInstall}
                root={project.root}
                busy={starting}
                onChoose={chooseInstall}
                onCancel={cancelInstall}
              />
            {/if}
            {#if startError}
              <p class="error" role="alert">{startError}</p>
            {/if}
            {#if !confirmingInstall}
              <button
                class="btn btn-filled start"
                bind:this={startButton}
                onclick={() => start()}
                disabled={starting}
              >
                {starting ? "Starting…" : "Start team"}
              </button>
            {/if}
          {/if}
        </section>
      {/if}

      <EnvironmentCard
        {client}
        {projectId}
        refreshKey={environmentKey}
        onSettingChanged={refresh}
      />

      <section class="card filled events" aria-labelledby="activity-heading">
        <h2 id="activity-heading" class="title-medium">Recent activity</h2>
        <EventLog {events} onOpenPermissions={() => onTabChange("permissions")} />
      </section>
    </div>

    <div id="panel-needs-you" role="tabpanel" aria-labelledby="tab-needs-you" hidden={tab !== "needs-you"}>
      <NeedsYouTab
        {client}
        {project}
        {pending}
        {resolved}
        fetchedAt={requestsFetchedAt}
        onAnswered={refresh}
      />
    </div>

    <div id="panel-permissions" role="tabpanel" aria-labelledby="tab-permissions" hidden={tab !== "permissions"}>
      <PermissionsTab
        {client}
        {project}
        onChanged={refresh}
        onOpenTeam={() => onTabChange("team")}
        onDirtyChange={(dirty) => (permissionsDirty = dirty)}
      />
    </div>

    <div id="panel-team" role="tabpanel" aria-labelledby="tab-team" hidden={tab !== "team"}>
      <TeamTab {client} {project} onChanged={refresh} onDirtyChange={(dirty) => (teamDirty = dirty)} />
    </div>
  {:else if !unreachable && !missing}
    <p class="muted" role="status">Loading…</p>
  {/if}
</div>

<style>
  .page {
    max-width: 72rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }

  [role="tabpanel"][hidden] {
    display: none;
  }

  .back {
    margin: 0 0 0.75rem -12px;
    padding: 0 16px 0 12px;
  }

  h1,
  h2 {
    margin: 0;
  }

  .root {
    color: var(--text-faint);
    font-size: 0.8rem;
    margin: 0.3rem 0 1.25rem;
  }

  .warning {
    background: var(--status-blocked-bg);
    color: var(--status-blocked-fg);
    padding: 0.6rem 0.9rem;
    border-radius: var(--md-sys-shape-corner-small);
    font-size: 0.85rem;
    margin-bottom: 1rem;
  }

  .last-run {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 16px 20px;
    margin-bottom: 1rem;
  }

  .last-run p {
    margin: 0;
    overflow-wrap: anywhere;
  }

  .last-run.bad {
    border-left: 4px solid var(--md-sys-color-error);
  }

  .roles {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    margin: 0.9rem 0 1.25rem;
  }

  .resumed-banner {
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
    border-radius: var(--md-sys-shape-corner-small);
    padding: 0.6rem 0.9rem;
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 1rem;
  }

  .stop {
    margin-bottom: 2rem;
  }

  .banner {
    margin: 0 0 2rem;
    padding: 0.75rem 1rem;
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: var(--status-queued-bg);
    color: var(--status-queued-fg);
  }

  .confirm {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-bottom: 2rem;
    padding: 0.75rem 1rem;
    border-radius: var(--md-sys-shape-corner-medium);
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
  }

  .start-form {
    padding: 1.5rem;
    margin-bottom: 2rem;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    max-width: 56rem;
  }

  .summary {
    margin: -0.5rem 0 0;
  }

  .hint {
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.6;
    margin: 0;
  }

  .hint code {
    color: var(--text);
  }

  /* An example sentence can be longer than a phone: wrap it rather than run off the screen. */
  .examples :global(.chip) {
    height: auto;
    min-height: 32px;
    max-width: 100%;
    padding-block: 6px;
    white-space: normal;
    text-align: left;
  }

  .examples {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }

  .task {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .start-form :global(.field-label) {
    background: var(--md-sys-color-surface-container-low);
  }

  .field textarea {
    line-height: 1.5rem;
  }

  .prompt {
    font-size: 0.8rem;
    color: var(--text-muted);
  }

  .prompt summary {
    cursor: pointer;
    width: fit-content;
  }

  .prompt p {
    white-space: pre-wrap;
    margin: 0.5rem 0 0;
    padding: 0.75rem 1rem;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-surface-container);
    line-height: 1.5;
  }

  .approval {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .approval-text {
    display: flex;
    flex-direction: column;
  }

  .start {
    align-self: flex-start;
  }

  .error {
    color: var(--danger);
    font-size: 0.85rem;
    margin: 0;
  }

  .events {
    padding: 1.1rem 1.25rem;
  }

  .events h2 {
    margin-bottom: 0.75rem;
  }
</style>
