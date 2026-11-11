<script lang="ts">
  // The goal's pull request, and what to do about it (CUT-293): the failing checks and the review
  // comments can each be sent to the builder with a button. Never automatic: a person presses it,
  // sees the message first and may edit it. Review text is read only when asked for (it is other
  // people's words, Approve tier), the state from the machine's own gh. Nothing is merged here.
  import type { FleetClient, PrCommentsResult, PrStatusResult, ProjectSummary } from "../api";
  import { commentsMessage, failingChecks, failingChecksMessage } from "../prfeedback";
  import SendToBuilder from "./SendToBuilder.svelte";

  let {
    client,
    projectId,
    project,
  }: { client: FleetClient; projectId: string; project: ProjectSummary } = $props();

  let pr = $state<PrStatusResult | null>(null);
  let comments = $state<PrCommentsResult | null>(null);
  let loading = $state(false);
  let failed = $state<string | null>(null);
  let composing = $state<{ text: string } | null>(null);
  let sentNote = $state<string | null>(null);
  let shownFor = "";

  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      pr = comments = composing = sentNote = null;
      shownFor = id;
    }
    if (project.worker) return;
    const read = async () => {
      const next = await client.getPrStatus(id).catch(() => null);
      if (id === projectId) pr = next;
    };
    void read();
    const timer = setInterval(read, 15000);
    return () => clearInterval(timer);
  });

  const failing = $derived(pr?.ok ? failingChecks(pr.checks) : []);

  async function loadComments() {
    loading = true;
    failed = null;
    try {
      const next = await client.getPrComments(projectId);
      comments = next;
      if (!next.ok) failed = next.reason ?? "Could not read the comments.";
    } catch (e) {
      failed = e instanceof Error ? e.message : "Could not read the comments.";
    } finally {
      loading = false;
    }
  }
</script>

{#if pr?.ok && pr.state === "open"}
  <section class="card filled feedback" aria-labelledby="feedback-heading" data-testid="pr-feedback">
    <h2 id="feedback-heading" class="title-large">Pull request feedback</h2>
    <p class="body-medium">
      {#if pr.url}<a href={pr.url} target="_blank" rel="noopener noreferrer">#{pr.number} {pr.title}</a>{:else}#{pr.number} {pr.title}{/if}
    </p>

    {#if failing.length > 0}
      <div class="block" data-testid="failing-checks">
        <p class="label-large warn">{failing.length} failing check{failing.length === 1 ? "" : "s"}</p>
        <ul>
          {#each failing as check (check.name)}
            <li class="body-medium">
              {#if check.url}<a href={check.url} target="_blank" rel="noopener noreferrer">{check.name}</a>{:else}{check.name}{/if}
            </li>
          {/each}
        </ul>
        <button
          class="btn btn-tonal"
          onclick={() => {
            composing = { text: failingChecksMessage(pr?.number, failing) };
            sentNote = null;
          }}>Send the failing checks to the builder</button
        >
      </div>
    {:else}
      <p class="body-small muted">
        {pr.checks_state === "pending" ? "Checks are still running." : pr.checks_state === "passing" ? "Checks are passing." : "No checks reported."}
      </p>
    {/if}

    <div class="block">
      {#if comments === null || !comments.ok}
        <button class="btn btn-text" disabled={loading} onclick={loadComments}>
          {loading ? "Reading…" : "Show review comments"}
        </button>
        {#if failed}<p class="body-small warn" role="alert">{failed}</p>{/if}
      {:else if comments.comments.length === 0}
        <p class="body-small muted">No review comments yet.</p>
      {:else}
        <p class="label-large">{comments.comments.length} review comment{comments.comments.length === 1 ? "" : "s"}</p>
        <ul class="comments">
          {#each comments.comments as c, i (i)}
            <li class="body-small">
              <strong>{c.author || "someone"}</strong>
              {#if c.kind === "inline"}on <code>{c.path}{c.line ? `:${c.line}` : ""}</code>{:else if c.kind === "review" && c.state}({c.state.replace(/_/g, " ")}){/if}:
              <span class="body">{c.body}</span>
            </li>
          {/each}
        </ul>
        <div class="row-actions">
          <button
            class="btn btn-tonal"
            onclick={() => {
              composing = { text: commentsMessage(comments?.number, comments?.comments ?? [], comments?.omitted ?? 0) };
              sentNote = null;
            }}>Send the review comments to the builder</button
          >
          <button class="btn btn-text" disabled={loading} onclick={loadComments}>Refresh</button>
        </div>
      {/if}
    </div>

    {#if composing}
      {#key composing.text}
        <div class="compose" data-testid="feedback-compose">
          <p class="body-small muted">
            This is what the builder will read. Edit it if you like; nothing is sent until you press the button.
          </p>
          <SendToBuilder
            {client}
            {projectId}
            {project}
            initial={composing.text}
            rows={8}
            onSent={(said) => {
              sentNote = said;
              composing = null;
            }}
            onCancel={() => (composing = null)}
          />
        </div>
      {/key}
    {/if}
    {#if sentNote}<p class="body-small" role="status" data-testid="feedback-sent">{sentNote}</p>{/if}
  </section>
{/if}

<style>
  .feedback {
    padding: 1.5rem;
    margin-bottom: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-width: 56rem;
  }

  .feedback h2,
  .feedback p {
    margin: 0;
  }

  /* The long button labels wrap on a phone instead of running out of the card. */
  .feedback :global(.btn) {
    max-width: 100%;
    height: auto;
    min-height: 40px;
    padding-block: 8px;
    white-space: normal;
  }

  .block {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    align-items: flex-start;
  }

  .block ul {
    margin: 0;
    padding-left: 1.25rem;
  }

  .comments li {
    margin-bottom: 0.5rem;
  }

  .body {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .warn {
    color: var(--md-sys-color-error);
  }

  .compose {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding-top: 0.5rem;
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  a {
    color: var(--md-sys-color-primary);
    overflow-wrap: anywhere;
  }
</style>
