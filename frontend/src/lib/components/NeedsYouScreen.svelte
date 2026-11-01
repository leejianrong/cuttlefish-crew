<script lang="ts">
  import type { FleetClient, NeedsYouRequest } from "../api";
  import { queueOrder } from "../requests";
  import RequestCard from "./RequestCard.svelte";

  let {
    client,
    requests,
    fetchedAt,
    unreachable,
    onAnswered,
    onOpenProject,
  }: {
    client: FleetClient;
    requests: NeedsYouRequest[];
    fetchedAt: number;
    unreachable: boolean;
    onAnswered: () => void;
    onOpenProject: (id: string) => void;
  } = $props();

  const queue = $derived(queueOrder(requests));
</script>

<div class="page">
  <header>
    <h1 class="headline-small">Needs you</h1>
    <p class="body-medium muted">
      Every agent that is paused waiting for an answer, across all your projects. A request that
      isn't answered in time is denied (a question is left unanswered) and the agent carries on. A Stuck card has nothing to answer: fix what it says is wrong, then steer that role.
    </p>
  </header>

  {#if unreachable}
    <p class="warning" role="status">Lost connection to the daemon. Retrying…</p>
  {/if}

  {#if requests.length === 0}
    <div class="card filled empty">
      <p class="title-medium">Nothing is waiting on you.</p>
      <p class="body-medium muted">
        When an agent asks to run a command that isn't allowed, or has a question for you (kopicode
        and Claude Code), it appears here and pauses until you answer.
      </p>
    </div>
  {/if}

  {#if queue.length > 0}
    <ol class="queue" aria-label="Waiting on you: agents paused now first, oldest first">
      {#each queue as request (request.project_id + request.id)}
        <li>
          <RequestCard {client} {request} {fetchedAt} {onAnswered} offline={unreachable} showProject />
          <button class="btn btn-text" onclick={() => onOpenProject(request.project_id)}>
            Open {request.project_name}
          </button>
        </li>
      {/each}
    </ol>
  {/if}
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-width: 72rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }

  header p {
    margin: 4px 0 0;
  }

  .queue {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .queue li {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }

  .queue li :global(.request) {
    align-self: stretch;
  }

  .empty {
    padding: 20px;
  }

  .empty p {
    margin: 0 0 4px;
  }

  .warning {
    margin: 0;
    padding: 8px 12px;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--status-blocked-bg);
    color: var(--status-blocked-fg);
  }
</style>
