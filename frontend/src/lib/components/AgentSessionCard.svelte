<script lang="ts">
  import type { AgentSession, FleetClient } from "../api";
  import { backendLabel, explanation, lastActionText, stateText } from "../agentsession";

  let { client, projectId }: { client: FleetClient; projectId: string } = $props();

  let session = $state<AgentSession | null>(null);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let failed = $state(false);
  let shownFor = "";

  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      session = null;
      error = null;
      shownFor = id;
    }
    const read = () =>
      client
        .getAgentSession(id)
        .then((next) => {
          if (id === projectId) {
            session = next;
            failed = false;
          }
        })
        .catch(() => {
          if (id === projectId) failed = true;
        });
    void read();
    // tmux can end under the page (a reboot, a kill), so the card reads again now and then.
    const timer = setInterval(read, 5000);
    return () => clearInterval(timer);
  });

  async function act(action: "start" | "stop") {
    if (busy) return;
    busy = true;
    error = null;
    try {
      session =
        action === "start"
          ? await client.startAgentSession(projectId)
          : await client.stopAgentSession(projectId);
    } catch (e) {
      error = e instanceof Error ? e.message : "That didn't work.";
    } finally {
      busy = false;
    }
  }
</script>

<section class="card filled agent" aria-labelledby="agent-heading">
  <h2 id="agent-heading" class="title-medium">Agent session</h2>
  {#if failed && session === null}
    <p class="body-medium muted">Couldn't read the agent session.</p>
  {:else if session === null}
    <p class="body-medium muted">Reading…</p>
  {:else}
    <div class="head">
      <span class="state title-small" class:up={session.state === "running"}>
        {stateText(session)}
        {#if session.backend}<span class="muted body-medium"> · {backendLabel(session.backend)}</span>{/if}
      </span>
      {#if session.state === "running"}
        <button class="btn btn-tonal" disabled={busy} onclick={() => act("stop")}>
          Stop session
        </button>
      {:else}
        <button class="btn btn-filled" disabled={busy} onclick={() => act("start")}>
          {session.state === "stopped" ? "Resume session" : "Start session"}
        </button>
      {/if}
    </div>
    {#if lastActionText(session)}
      <p class="body-medium action" data-testid="agent-session-action">{lastActionText(session)}</p>
    {/if}
    <p class="body-small muted">{explanation(session)}</p>
    {#if error}<p class="body-small error" role="alert">{error}</p>{/if}
  {/if}
</section>

<style>
  .agent {
    padding: 1.1rem 1.25rem;
    margin: 1rem 0;
    max-width: 56rem;
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin: 0.5rem 0;
  }

  .state.up {
    color: var(--md-sys-color-success);
  }

  .action {
    margin: 0.25rem 0;
  }

  .error {
    color: var(--md-sys-color-error);
  }

  p {
    margin: 0.25rem 0;
  }
</style>
