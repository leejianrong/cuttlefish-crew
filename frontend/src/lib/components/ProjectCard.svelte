<script lang="ts">
  import type { ProjectSummary, RoleStatus } from "../api";
  import { agoIso } from "../artifacts";
  import { projectState, STATE_LABEL, type ProjectState } from "../projectstate";
  import RoleSprite from "./RoleSprite.svelte";
  import StatusChip from "./StatusChip.svelte";

  let {
    project,
    onOpen,
    onStop,
    onRemove,
    waiting = 0,
    pinned = false,
    onPin,
  }: {
    project: ProjectSummary;
    /** Requests of this project waiting for a person (the Needs-you list). */
    waiting?: number;
    onOpen: () => void;
    onStop: () => void;
    onRemove: () => void;
    /** CUT-189: kept at the top of the list; the choice lives in this browser. */
    pinned?: boolean;
    onPin?: () => void;
  } = $props();

  const roleEntries = $derived(Object.entries(project.status));
  // Some role has started: a "queued" one beside it, in a team that is no longer running, never will.
  const hasRun = $derived(roleEntries.some(([, status]) => status !== "queued"));
  // The pose alone says nothing to a screen reader, or to someone who cannot tell the colours apart.
  const statusWord = (status: RoleStatus): string =>
    status === "queued" && !project.running && hasRun ? "not run" : status;
  let confirming = $state<"stop" | "remove" | null>(null);

  // The card's one line of "what is going on", from what the summary already carries.
  const tokens = $derived(Object.values(project.usage ?? {}).reduce((sum, u) => sum + u.tokens, 0));
  let now = $state(Date.now() / 1000);
  $effect(() => {
    const tick = setInterval(() => (now = Date.now() / 1000), 5000);
    return () => clearInterval(tick);
  });
  const active = $derived(agoIso(project.last_event_at, now));
  // Each state in the role chip's own palette: waiting reads as blocked, stuck as failed.
  const TONE: Record<ProjectState, RoleStatus> = {
    working: "working",
    waiting: "blocked",
    done: "done",
    stuck: "failed",
    idle: "queued",
  };
  const line = $derived(projectState(project, waiting, now * 1000));
</script>

<article class="card filled project">
  <header>
    <button class="title title-medium" onclick={onOpen}>{project.name}</button>
    {#if pinned}<span class="pinned label-small">Pinned</span>{/if}
  </header>
  <p class="root mono" title={project.root}><bdi>{project.root}</bdi></p>

  <p class="state body-small" data-state={line.state}>
    <span class="chip-slot"><StatusChip status="queued" tone={TONE[line.state]} label={STATE_LABEL[line.state]} /></span>
    <span class="reason">{line.reason}</span>
  </p>

  {#if roleEntries.length > 0}
    <div class="roles">
      {#each roleEntries as [role, status] (role)}
        <div class="role-row">
          <RoleSprite {status} size={2.5} label={statusWord(status)} />
          <span class="role-name">{role}</span>
          <span class="role-status">{statusWord(status)}</span>
        </div>
      {/each}
    </div>
  {:else}
    <p class="empty">no roles registered yet</p>
  {/if}

  {#if active || tokens > 0}
    <p class="facts body-small">
      {#if active}{project.running ? "Active" : "Last active"} {active}{/if}
      {#if active && tokens > 0} &middot; {/if}
      {#if tokens > 0}{tokens.toLocaleString("en-GB")} tokens{/if}
    </p>
  {/if}

  <footer>
    {#if confirming === "stop"}
      <span class="confirm-text body-small" role="alert">Stop this team? Starting again begins a new run.</span>
      <button class="btn btn-danger sm" onclick={onStop}>Stop</button>
      <button class="btn btn-text sm" onclick={() => (confirming = null)}>Keep running</button>
    {:else if confirming === "remove"}
      <span class="confirm-text body-small" role="alert">Remove this project from cuttlefish? Your folder is not touched.</span>
      <button class="btn btn-danger sm" onclick={onRemove}>Remove</button>
      <button class="btn btn-text sm" onclick={() => (confirming = null)}>Keep it</button>
    {:else}
      {#if project.stopping}
        <button class="btn btn-danger sm" disabled>Stopping…</button>
      {:else if project.running}
        <button class="btn btn-danger sm" onclick={() => (confirming = "stop")}>Stop</button>
      {:else}
        <button class="btn btn-filled sm" onclick={onOpen}>Open</button>
      {/if}
      {#if onPin}
        <button class="btn btn-text sm" aria-pressed={pinned} onclick={onPin}>
          {pinned ? "Unpin" : "Pin to top"}
        </button>
      {/if}
      <button class="btn btn-text sm remove" onclick={() => (confirming = "remove")}>Remove</button>
    {/if}
  </footer>
</article>

<style>
  .project {
    padding: 1.1rem 1.2rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .title {
    background: none;
    border: none;
    padding: 0;
    color: var(--text);
    text-align: left;
  }

  .title:hover {
    color: var(--accent);
  }

  /* A thumb needs 44px (CUT-98): the name is the way into the project. */
  @media (max-width: 640px) {
    .title {
      min-height: 44px;
    }
  }

  .pinned {
    margin-left: auto;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .root {
    color: var(--text-faint);
    font-size: 0.78rem;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    /* A long folder path loses its start, not the project name at its end; <bdi> keeps the text
     * itself left to right. */
    direction: rtl;
    text-align: left;
  }

  .roles {
    display: flex;
    flex-wrap: wrap;
    gap: 0.7rem 1rem;
    margin: 0.3rem 0;
    padding: 0.5rem 0.6rem;
    background: var(--bg-inset);
    border-radius: var(--md-sys-shape-corner-small);
  }

  .role-row {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.3rem;
  }

  .role-name {
    font-size: 0.7rem;
    font-family: var(--font-mono);
    color: var(--text-muted);
  }

  .role-status {
    font-size: 0.7rem;
    color: var(--text-faint);
  }

  .state {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin: 0;
    min-width: 0;
  }

  .chip-slot {
    flex-shrink: 0;
  }

  .reason {
    color: var(--text-muted);
    min-width: 0;
  }

  .facts {
    margin: 0;
    color: var(--text-muted);
  }

  .empty {
    color: var(--text-faint);
    font-size: 0.82rem;
    font-style: italic;
    margin: 0.2rem 0;
  }

  footer {
    flex-wrap: wrap;
    display: flex;
    gap: 0.5rem;
    margin-top: 0.4rem;
  }

  .confirm-text {
    flex-basis: 100%;
  }

  .remove {
    margin-left: auto;
  }
</style>
