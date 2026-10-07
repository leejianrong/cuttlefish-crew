<script lang="ts">
  import type { FleetClient, NeedsYouRequest, ProjectSummary } from "../api";
  import { commandText, formatWhen } from "../events";
  import { backendLabel, outcomeLabel } from "../requests";
  import RequestCard from "./RequestCard.svelte";

  let {
    client,
    project,
    pending,
    resolved,
    fetchedAt,
    onAnswered,
  }: {
    client: FleetClient;
    project: ProjectSummary;
    pending: NeedsYouRequest[];
    resolved: NeedsYouRequest[];
    fetchedAt: number;
    onAnswered: () => void;
  } = $props();

  // Roles on an explicit non-kopicode backend cannot pause to ask, so the page says so rather
  // than imply every agent can.
  const otherBackends = $derived(
    [
      ...new Set(
        project.roles
          .map((role) => role.backend ?? project.backend)
          .filter((backend): backend is string => !!backend && backend !== "kopicode"),
      ),
    ].sort(),
  );
</script>

<section class="stack" aria-labelledby="needs-heading">
  <h2 id="needs-heading" class="title-large">Waiting on you</h2>
  <p class="body-medium muted">
    A kopicode agent that wants to run a command it isn't allowed to pauses here until you answer.
    An agent that kept failing on the project's environment shows as Stuck: it was stopped, so there is nothing to
    answer.
    {#if otherBackends.length > 0}
      {otherBackends.map(backendLabel).join(" and ")} can't pause mid-run, so a command they need is refused; you'll
      find that in Recent activity.
    {:else}
      Claude Code and Codex can't pause mid-run, so there a command is refused instead.
    {/if}
  </p>

  {#if pending.length === 0}
    <div class="card filled empty">
      <p class="title-medium">Nothing is waiting on you.</p>
      <p class="body-medium muted">
        When an agent asks to run something outside the allowed commands, it shows up here and the
        agent waits.
      </p>
    </div>
  {:else}
    {#each pending as request (request.id)}
      <RequestCard {client} {request} {fetchedAt} {onAnswered} />
    {/each}
  {/if}

  {#if resolved.length > 0}
    <h2 class="title-medium history">Answered recently</h2>
    <ul class="rows">
      {#each resolved as request (request.id)}
        <li class="row">
          <span class="outcome label-large" class:no={request.state !== "allowed_once" && request.state !== "allowed_always"}>
            {outcomeLabel(request)}
          </span>
          <code class="mono">{commandText(request.detail)}</code>
          <span class="body-small muted">
            {request.role ?? "An agent"}{request.resolved_at ? ` · ${formatWhen(request.resolved_at)}` : ""}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .stack > p,
  .stack > h2 {
    margin: 0;
  }

  .empty {
    padding: 20px;
  }

  .empty p {
    margin: 0 0 4px;
  }

  .history {
    margin-top: 16px;
  }

  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--md-sys-color-outline-variant);
  }

  .outcome {
    min-width: 12rem;
    color: var(--md-sys-color-success);
  }

  .outcome.no {
    color: var(--md-sys-color-on-surface-variant);
  }

  code {
    flex: 1 1 14rem;
    overflow-wrap: anywhere;
    font-size: 0.8125rem;
  }
</style>
