<script lang="ts">
  import type { FleetClient, NeedsYouRequest, ProjectSummary } from "../api";
  import { waitingChip, type AutopilotStatus } from "../autopilot";
  import { commandText, formatWhen } from "../events";
  import { outcomeLabel } from "../requests";
  import RequestCard from "./RequestCard.svelte";

  let {
    client,
    project,
    pending,
    resolved,
    fetchedAt,
    onAnswered,
    offline = false,
  }: {
    client: FleetClient;
    project: ProjectSummary;
    pending: NeedsYouRequest[];
    resolved: NeedsYouRequest[];
    fetchedAt: number;
    onAnswered: () => void;
    offline?: boolean;
  } = $props();

  // What Autopilot is waiting on that has no card (CUT-312): a plan to approve, or a dialog only the
  // session's own terminal can answer. A decision has its card below, with its own countdown.
  let autopilot = $state<AutopilotStatus | null>(null);
  let now = $state(new Date());
  $effect(() => {
    void fetchedAt;
    now = new Date();
    client
      .getAutopilot(project.id)
      .then((status) => (autopilot = status))
      .catch(() => (autopilot = null));
  });
  $effect(() => {
    if (!autopilot?.waiting) return;
    const timer = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(timer);
  });
  const wait = $derived(autopilot ? waitingChip(autopilot, now, ["plan", "blocked"]) : null);
</script>

<section class="stack" aria-labelledby="needs-heading">
  <h2 id="needs-heading" class="title-large">Waiting on you</h2>
  <p class="body-medium muted">
    An agent that wants to run a command it isn't allowed to pauses here until you answer, and so does a kopicode
    question for you. A Claude Code question is answered in its terminal (Auto refuses it), and a Codex question is not
    asked live yet.
    An agent that kept failing on the project's environment shows as Stuck: it was stopped, so there is nothing to
    answer.
  </p>

  {#if wait}
    <div class="card filled wait" role="status">
      <p class="title-medium">{wait.text}</p>
      <p class="body-medium muted">{wait.age}{wait.hint ? `. ${wait.hint}` : ""}</p>
    </div>
  {/if}

  {#if pending.length === 0}
    <div class="card filled empty">
      <p class="title-medium">Nothing is waiting on you.</p>
      <p class="body-medium muted">
        When an agent asks to run something outside the allowed commands, or asks you a question, it
        shows up here and the agent waits.
      </p>
    </div>
  {:else}
    {#each pending as request (request.id)}
      <RequestCard {client} {request} {fetchedAt} {onAnswered} {offline} />
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

  .empty,
  .wait {
    padding: 20px;
  }

  .wait {
    border-left: 4px solid var(--md-sys-color-attention);
  }

  .wait p {
    margin: 0 0 4px;
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
