<script lang="ts">
  import type { ProjectSummary } from "../api";
  import RoleSprite from "./RoleSprite.svelte";

  let {
    project,
    onOpen,
    onStop,
    onRemove,
  }: {
    project: ProjectSummary;
    onOpen: () => void;
    onStop: () => void;
    onRemove: () => void;
  } = $props();

  const roleEntries = $derived(Object.entries(project.status));
  let confirming = $state<"stop" | "remove" | null>(null);
</script>

<article class="card filled project">
  <header>
    <button class="title title-medium" onclick={onOpen}>{project.name}</button>
    {#if project.running}
      <span class="running-dot" title="a team is running"></span>
    {/if}
  </header>
  <p class="root mono">{project.root}</p>

  {#if roleEntries.length > 0}
    <div class="roles">
      {#each roleEntries as [role, status] (role)}
        <div class="role-row">
          <RoleSprite {status} size={2.5} />
          <span class="role-name">{role}</span>
        </div>
      {/each}
    </div>
  {:else}
    <p class="empty">no roles registered yet</p>
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

  .running-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 999px;
    background: var(--status-working-fg);
    animation: pulse 1.4s ease-in-out infinite;
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.35;
    }
  }

  .root {
    color: var(--text-faint);
    font-size: 0.78rem;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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
