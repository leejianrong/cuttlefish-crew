<script lang="ts">
  import type { EpisodicEventView, FleetClient, ProjectBudget, RoleStatus, RoleUsage } from "../api";
  import StatusChip from "./StatusChip.svelte";

  let {
    client,
    projectId,
    role,
    status,
    handovers = [],
    usage = { tokens: 0, cost_usd: null },
    budget = { max_tokens: null, max_cost_usd: null },
  }: {
    client: FleetClient;
    projectId: string;
    role: string;
    status: RoleStatus;
    /** This role's own `HandoverWritten` checkpoints, oldest first (ADR-0010/KAN-1705) --
     * continuity made visible, not just trusted. */
    handovers?: EpisodicEventView[];
    /** This role's own cumulative usage so far (KAN-1712/ADR-0017). */
    usage?: RoleUsage;
    /** The project's own configured ceiling, if any (KAN-1712/ADR-0017) -- checked
     * independently per role, so every role card compares against the same one. */
    budget?: ProjectBudget;
  } = $props();

  const tokensOverBudget = $derived(
    budget.max_tokens !== null && usage.tokens >= budget.max_tokens,
  );
  const costOverBudget = $derived(
    budget.max_cost_usd !== null &&
      usage.cost_usd !== null &&
      usage.cost_usd >= budget.max_cost_usd,
  );

  let message = $state("");
  let sending = $state(false);
  let sent = $state(false);

  // KAN-1711: a comment-less "Approve" always works; "Reject" needs one (the
  // daemon's own /approve route enforces this too -- mirrored here just to
  // disable the button rather than round-trip a 400 for an empty comment).
  let approvalComment = $state("");
  let deciding = $state(false);
  let decided = $state<"approved" | "rejected" | null>(null);

  async function send() {
    if (!message.trim()) return;
    sending = true;
    sent = false;
    try {
      await client.steerProject(projectId, role, message.trim());
      message = "";
      sent = true;
      setTimeout(() => (sent = false), 2000);
    } finally {
      sending = false;
    }
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      send();
    }
  }

  async function decide(approved: boolean) {
    if (!approved && !approvalComment.trim()) return;
    deciding = true;
    decided = null;
    try {
      await client.approveProject(projectId, role, approved, approvalComment.trim() || undefined);
      approvalComment = "";
      decided = approved ? "approved" : "rejected";
      setTimeout(() => (decided = null), 2000);
    } finally {
      deciding = false;
    }
  }
</script>

<div class="card filled role-card">
  <div class="role-head">
    <span class="role-name">{role}</span>
    <StatusChip {status} />
  </div>
  <p class="usage" class:over={tokensOverBudget || costOverBudget}>
    {usage.tokens.toLocaleString()} tokens{budget.max_tokens !== null
      ? ` / ${budget.max_tokens.toLocaleString()}`
      : ""}
    {#if usage.cost_usd !== null}
      &middot; ${usage.cost_usd.toFixed(4)}{budget.max_cost_usd !== null
        ? ` / $${budget.max_cost_usd.toFixed(2)}`
        : ""}
    {:else}
      &middot; cost unknown
    {/if}
  </p>
  <div class="steer">
    <textarea
      bind:value={message}
      onkeydown={onKeydown}
      placeholder="Redirect {role}'s work… (takes effect at the next round boundary)"
      rows="2"
    ></textarea>
    <button class="btn btn-filled" onclick={send} disabled={sending || !message.trim()}>
      {sent ? "Sent" : sending ? "Sending…" : "Send"}
    </button>
  </div>

  {#if status === "blocked"}
    <div class="approval">
      <p class="hint">
        {role} is blocked. That can mean it is waiting for your review, it reached its token or
        cost limit, or an action was refused. If it is waiting on you, approve or reject here;
        rejecting needs a comment saying what to change.
      </p>
      <textarea
        bind:value={approvalComment}
        placeholder="Comment (required to reject, optional to approve)"
        rows="2"
      ></textarea>
      <div class="approval-actions">
        <button class="btn btn-tonal" onclick={() => decide(true)} disabled={deciding}>
          {decided === "approved" ? "Approved" : "Approve"}
        </button>
        <button
          class="btn btn-danger"
          onclick={() => decide(false)}
          disabled={deciding || !approvalComment.trim()}
        >
          {decided === "rejected" ? "Rejected" : "Reject"}
        </button>
      </div>
    </div>
  {/if}

  {#if handovers.length > 0}
    <details class="continuity">
      <summary>
        {handovers.length} checkpoint{handovers.length === 1 ? "" : "s"} · last: {(handovers[
          handovers.length - 1
        ].payload.summary as string).slice(0, 80)}
      </summary>
      <ol class="timeline">
        {#each handovers as handover (handover.seq)}
          <li>
            <span class="ts mono">{new Date(handover.ts).toLocaleTimeString()}</span>
            <span class="range mono"
              >seq {handover.payload.covers_seq_from}&ndash;{handover.payload.covers_seq_to}</span
            >
            <span class="summary">{handover.payload.summary}</span>
          </li>
        {/each}
      </ol>
    </details>
  {/if}
</div>

<style>
  .role-card {
    padding: 1rem 1.15rem;
  }

  .role-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.75rem;
  }

  .role-name {
    font-weight: 600;
  }

  .usage {
    margin: 0 0 0.75rem;
    font-size: 0.78rem;
    color: var(--text-faint);
    font-variant-numeric: tabular-nums;
  }

  .usage.over {
    color: var(--status-failed-fg);
    font-weight: 600;
  }

  .steer {
    display: flex;
    gap: 0.6rem;
    align-items: flex-end;
  }

  textarea {
    flex: 1;
    padding: 0.5rem 0.65rem;
    background: var(--bg-inset);
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-extra-small);
    color: var(--text);
    resize: vertical;
  }

  .approval {
    margin-top: 0.75rem;
    padding-top: 0.75rem;
    border-top: 1px dashed var(--border);
  }

  .approval .hint {
    color: var(--text-faint);
    font-size: 0.78rem;
    margin: 0 0 0.5rem;
  }

  .approval textarea {
    width: 100%;
    margin-bottom: 0.5rem;
  }

  .approval-actions {
    display: flex;
    gap: 0.6rem;
  }

  .continuity {
    margin-top: 0.75rem;
    font-size: 0.8rem;
  }

  .continuity summary {
    cursor: pointer;
    color: var(--text-muted);
    list-style: none;
  }

  .continuity summary::-webkit-details-marker {
    display: none;
  }

  .continuity summary::before {
    content: "\25B8";
    display: inline-block;
    margin-right: 0.4em;
    color: var(--accent);
  }

  .continuity[open] summary::before {
    content: "\25BE";
  }

  .timeline {
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }

  .timeline li {
    display: grid;
    grid-template-columns: 4.5rem 6rem 1fr;
    gap: 0.5rem;
    align-items: baseline;
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    border-radius: 6px;
    padding: 0.35rem 0.5rem;
  }

  .timeline .ts,
  .timeline .range {
    color: var(--text-faint);
    font-size: 0.72rem;
  }

  .timeline .summary {
    color: var(--text);
    overflow-wrap: anywhere;
  }
</style>
