<script lang="ts">
  import type { EpisodicEventView } from "../api";

  let { events }: { events: EpisodicEventView[] } = $props();

  function summarize(event: EpisodicEventView): string {
    const p = event.payload;
    switch (event.event_type) {
      case "TaskSubmitted":
        return `submitted: ${p.text}`;
      case "DelegationStarted":
        return `round started: ${p.task_text}`;
      case "DelegationCompleted":
        return `round completed: ${p.summary}`;
      case "DelegationRefused":
        return `refused: ${p.reason}`;
      case "DelegationFailed":
        return `failed: ${p.reason}`;
      case "SteeringMessage":
        return `operator: ${p.text}`;
      case "HandoverWritten":
        // The checkpoint's own text, not just the fact one was written -- this
        // is the load-bearing content a continuity chain carries forward
        // (ADR-0010/KAN-1705), worth reading at a glance, not just trusting.
        return `checkpoint (covers seq ${p.covers_seq_from}–${p.covers_seq_to}): ${p.summary}`;
      case "TaskCompleted":
        return `done: ${p.result}`;
      case "TaskFailed":
        return `failed: ${p.error}`;
      case "TeamResumed":
        return `a daemon restart found this run still in progress and resumed it -- the journal picks back up from seq ${p.resumed_from_seq}`;
      default:
        return JSON.stringify(p);
    }
  }
</script>

<div class="log">
  {#if events.length === 0}
    <p class="empty">no events yet</p>
  {/if}
  {#each events as event (event.seq)}
    {#if event.event_type === "TeamResumed"}
      <div class="row resumed">
        <span class="ts mono">{new Date(event.ts).toLocaleTimeString()}</span>
        <span class="resumed-icon">&#8635;</span>
        <span class="text">{summarize(event)}</span>
      </div>
    {:else}
      <div class="row" class:handover={event.event_type === "HandoverWritten"}>
        <span class="ts mono">{new Date(event.ts).toLocaleTimeString()}</span>
        {#if event.payload.role}
          <span class="role mono">{event.payload.role}</span>
        {/if}
        <span class="type">{event.event_type}</span>
        <span class="text">{summarize(event)}</span>
      </div>
    {/if}
  {/each}
</div>

<style>
  .log {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    max-height: 22rem;
    overflow-y: auto;
    font-size: 0.82rem;
  }

  .empty {
    color: var(--text-faint);
    font-style: italic;
  }

  .row {
    display: grid;
    grid-template-columns: 5.5rem 5.5rem 9rem 1fr;
    gap: 0.6rem;
    align-items: baseline;
    padding: 0.3rem 0;
    border-bottom: 1px solid var(--border);
  }

  .ts {
    color: var(--text-faint);
    font-size: 0.75rem;
  }

  .role {
    color: var(--accent);
    font-size: 0.75rem;
  }

  .type {
    color: var(--text-muted);
    font-weight: 600;
    font-size: 0.75rem;
  }

  .text {
    color: var(--text);
    overflow-wrap: anywhere;
  }

  /* A checkpoint's own row reads like the rest of the log (still one role's own
     thread among others), just tinted so it's easy to spot while scanning for
     "is continuity actually checkpointing here" (ADR-0010/KAN-1705). */
  .row.handover {
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    border-radius: 6px;
    padding-left: 0.4rem;
  }

  .row.handover .type {
    color: var(--accent);
  }

  /* Distinct from ordinary progress on purpose -- a full-width banner, not just
     another log line, so a daemon-restart resume is unmistakable rather than
     something an operator has to notice buried in the type column. */
  .row.resumed {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    grid-template-columns: unset;
    background: var(--status-blocked-bg);
    color: var(--status-blocked-fg);
    border: 1px solid var(--status-blocked-fg);
    border-radius: 8px;
    padding: 0.5rem 0.75rem;
  }

  .row.resumed .ts {
    color: inherit;
    opacity: 0.75;
  }

  .row.resumed .resumed-icon {
    font-size: 1rem;
    line-height: 1;
  }

  .row.resumed .text {
    color: inherit;
    font-weight: 600;
  }
</style>
