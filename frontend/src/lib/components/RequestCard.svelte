<script lang="ts">
  import { FleetApiError, type FleetClient, type NeedsYouRequest, type RequestAnswer } from "../api";
  import {
    answerProblem,
    formatRemaining,
    isStartOfCommand,
    landsLabel,
    parseRule,
    ruleText,
    secondsLeft,
  } from "../requests";
  import Icon from "./Icon.svelte";

  let {
    client,
    request,
    fetchedAt,
    onAnswered,
    showProject = false,
  }: {
    client: FleetClient;
    request: NeedsYouRequest;
    /** `Date.now()` when the daemon sent this request, so the countdown can run between polls. */
    fetchedAt: number;
    onAnswered: () => void;
    showProject?: boolean;
  } = $props();

  let now = $state(Date.now());
  // The editable draft starts from the daemon's suggestion; polls replace `request` but must not
  // overwrite what the person is typing.
  // svelte-ignore state_referenced_locally
  let rule = $state(ruleText(request.suggested_rule));
  let busy = $state<RequestAnswer | null>(null);
  let problem = $state<string | null>(null);

  $effect(() => {
    const tick = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(tick);
  });

  const left = $derived(secondsLeft(request, fetchedAt, now));
  const expired = $derived(left <= 0);
  const urgent = $derived(left > 0 && left <= 30);
  const canAlways = $derived(request.answers.includes("allow_always"));
  const ruleWords = $derived(parseRule(rule));
  const ruleOk = $derived(isStartOfCommand(ruleWords, request.detail));
  const live = $derived(request.lands === "now");
  const blocked = $derived(request.kind === "blocked");
  const question = $derived(request.kind === "question");
  let reply = $state("");
  const replyOk = $derived(reply.trim().length > 0);

  const options = $derived(request.options ?? []);

  async function answer(choice: RequestAnswer, text?: string) {
    busy = choice;
    problem = null;
    try {
      await client.answerRequest(
        request.project_id,
        request.id,
        choice,
        choice === "allow_always" ? ruleWords : undefined,
        choice === "answer" ? (text ?? reply) : undefined,
      );
      onAnswered();
    } catch (error) {
      problem =
        error instanceof FleetApiError
          ? answerProblem(error.status, error.message)
          : answerProblem(0, "");
      // A 409 or 404 means it ended without us: the list will drop it on the next refresh.
      if (error instanceof FleetApiError && (error.status === 409 || error.status === 404)) {
        onAnswered();
      }
    } finally {
      busy = null;
    }
  }
</script>

<article class="card filled request" aria-labelledby="req-{request.id}">
  <div class="meta">
    <span class="tag attn">{blocked ? "Stuck" : question ? "Question" : "Permission"}</span>
    {#if request.role}<span class="label-large who">{request.role}</span>{/if}
    {#if request.backend}<span class="tag">{request.backend}</span>{/if}
    {#if showProject}<span class="label-medium muted">{request.project_name}</span>{/if}
    <span class="label-medium lands" class:live>
      {blocked ? "Waits for your message, no time limit" : landsLabel(request.lands)}
    </span>
  </div>

  <h3 id="req-{request.id}" class="title-medium">{request.title}</h3>
  {#if question}
    <p class="asked body-large" aria-label="The question">{request.detail}</p>
    <p class="body-medium muted why">{request.why}</p>
  {:else}
    <p class="body-medium muted why">{request.why}</p>
    <pre class="command mono" aria-label={blocked ? "Its last failing command" : "Command"}>{request.detail}</pre>
  {/if}

  {#if question}
    {#if options.length > 0}
      <div class="choices" role="group" aria-label="Choices">
        {#each options as option (option)}
          <button
            class="btn btn-tonal"
            disabled={busy !== null || expired}
            onclick={() => answer("answer", option)}
          >
            {option}
          </button>
        {/each}
      </div>
    {/if}
    <label class="field">
      <span class="field-label">{options.length > 0 ? "Or write your own answer" : "Your answer"}</span>
      <textarea
        bind:value={reply}
        rows="3"
        maxlength="4000"
        disabled={busy !== null || expired}
      ></textarea>
    </label>
    <div class="actions">
      <button
        class="btn btn-filled"
        disabled={busy !== null || expired || !replyOk}
        onclick={() => answer("answer")}
      >
        {busy === "answer" ? "Sending…" : "Send answer"}
      </button>
      <button class="btn btn-outlined" disabled={busy !== null || expired} onclick={() => answer("decline")}>
        {busy === "decline" ? "Declining…" : "Decline"}
      </button>
      <span class="timer label-medium" class:urgent aria-hidden="true">
        <Icon name="clock" size={16} />
        {expired ? "Moving on…" : `Goes unanswered in ${formatRemaining(left)}`}
      </span>
    </div>
    <p class="body-small muted">
      The agent is paused on this question. Declining, or no answer in time, tells it nobody is
      here and it carries on.
    </p>
  {:else if blocked}
    <p class="body-small muted">
      Nothing is waiting for an answer: this agent was stopped. Read why above and fix what is
      wrong, then steer {request.role ?? "it"} from the project page to give it another round.
    </p>
  {:else}
  <div class="actions">
    <button class="btn btn-filled" disabled={busy !== null || expired} onclick={() => answer("allow_once")}>
      {busy === "allow_once" ? "Allowing…" : "Allow once"}
    </button>
    <button class="btn btn-outlined" disabled={busy !== null || expired} onclick={() => answer("deny")}>
      {busy === "deny" ? "Denying…" : "Deny"}
    </button>
    <span class="timer label-medium" class:urgent aria-hidden="true">
      <Icon name="clock" size={16} />
      {expired ? "Denying…" : `Denies on its own in ${formatRemaining(left)}`}
    </span>
  </div>

  {#if canAlways}
    <div class="always">
      <label class="field rule">
        <span class="field-label">Always allow commands that start with</span>
        <input class="mono" bind:value={rule} spellcheck="false" autocomplete="off" />
      </label>
      <button
        class="btn btn-tonal"
        disabled={busy !== null || expired || !ruleOk}
        onclick={() => answer("allow_always")}
      >
        {busy === "allow_always" ? "Saving…" : "Always allow"}
      </button>
    </div>
    <p class="body-small muted">
      Applies to this team now and is saved to the project's commands, so it stays after a restart.
      {#if !ruleOk}<span class="bad">The rule must be the start of this command.</span>{/if}
    </p>
  {:else}
    <p class="body-small muted">
      This command chains or quotes other commands, so it can only be allowed once.
    </p>
  {/if}

  {/if}

  {#if problem}<p class="error" role="alert">{problem}</p>{/if}
</article>

<style>
  .request {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 16px 20px;
    border-left: 4px solid var(--md-sys-color-attention);
  }

  .meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .who {
    color: var(--md-sys-color-on-surface);
  }

  .lands {
    margin-left: auto;
    color: var(--md-sys-color-on-surface-variant);
  }

  .lands.live {
    color: var(--md-sys-color-attention);
  }

  .why {
    margin: 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .command {
    margin: 4px 0 0;
    padding: 12px 16px;
    white-space: pre-wrap;
    word-break: break-word;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-surface-container-highest);
    color: var(--md-sys-color-on-surface);
    font-size: 0.8125rem;
    line-height: 1.25rem;
  }

  .asked {
    margin: 4px 0 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
  }

  .choices {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 4px;
  }

  .timer {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: auto;
    color: var(--md-sys-color-on-surface-variant);
  }

  .timer.urgent {
    color: var(--md-sys-color-attention);
    font-weight: 700;
  }

  .always {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 12px;
    margin-top: 8px;
  }

  .rule {
    flex: 1 1 260px;
  }

  .bad {
    color: var(--md-sys-color-error);
  }

  .error {
    margin: 0;
    color: var(--md-sys-color-error);
  }

  @media (max-width: 640px) {
    .lands {
      margin-left: 0;
      flex-basis: 100%;
    }
  }
</style>
