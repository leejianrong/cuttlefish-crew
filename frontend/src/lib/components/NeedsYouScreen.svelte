<script lang="ts">
  import type { FleetClient, NeedsYouRequest } from "../api";
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

  const byProject = $derived.by(() => {
    const groups = new Map<string, { name: string; items: NeedsYouRequest[] }>();
    for (const request of requests) {
      const group = groups.get(request.project_id) ?? { name: request.project_name, items: [] };
      group.items.push(request);
      groups.set(request.project_id, group);
    }
    return [...groups.entries()].map(([id, group]) => ({ id, ...group }));
  });
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

  {#each byProject as group (group.id)}
    <section class="group" aria-labelledby="group-{group.id}">
      <div class="group-head">
        <h2 id="group-{group.id}" class="title-large">{group.name}</h2>
        <button class="btn btn-text" onclick={() => onOpenProject(group.id)}>Open project</button>
      </div>
      {#each group.items as request (request.id)}
        <RequestCard {client} {request} {fetchedAt} {onAnswered} />
      {/each}
    </section>
  {/each}
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

  .group {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .group-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
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
