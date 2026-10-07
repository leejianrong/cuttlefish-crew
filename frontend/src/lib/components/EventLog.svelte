<script lang="ts">
  import type { EpisodicEventView } from "../api";
  import { eventLabelFor, formatWhen, installOutput, isRefusedCommand, summarize } from "../events";

  let {
    events,
    onOpenPermissions,
  }: { events: EpisodicEventView[]; onOpenPermissions?: () => void } = $props();
</script>

<div class="log">
  {#if events.length === 0}
    <p class="empty">Nothing has happened yet.</p>
  {/if}
  {#each events as event (event.seq)}
    {#if event.event_type === "TeamResumed"}
      <div class="row resumed">
        <span class="ts mono" title={event.ts}>{formatWhen(event.ts)}</span>
        <span class="resumed-icon">&#8635;</span>
        <span class="text">{summarize(event)}</span>
      </div>
    {:else}
      <div
        class="row"
        class:handover={event.event_type === "HandoverWritten"}
        class:tool-denied={(event.event_type === "ToolCallRecorded" &&
          event.payload.status === "denied") ||
          (event.event_type === "ConsentDecided" && event.payload.answer === "deny")}
        class:tool-error={event.event_type === "ToolCallRecorded" &&
          event.payload.status === "error"}
      >
        <span class="ts mono" title={event.ts}>{formatWhen(event.ts)}</span>
        <!-- Always a cell, so a row with no role keeps the label and text in their own columns. -->
        <span class="role mono">{event.payload.role ?? ""}</span>
        <span class="type" title="{event.event_type}, #{event.seq}">{eventLabelFor(event)}</span>
        <span class="text">
          {summarize(event)}
          {#if onOpenPermissions && isRefusedCommand(event)}
            <button type="button" class="link" onclick={onOpenPermissions}>Change permissions</button>
          {/if}
          {#if installOutput(event)}
            <details class="output">
              <summary>{event.event_type === "DelegationFailed" ? "Last failing command" : "Output"}</summary>
              <pre class="mono">{installOutput(event)}</pre>
            </details>
          {/if}
          {#if event.event_type === "DelegationFailed" && event.payload.record}
            <span class="record mono">Full output: {event.payload.record}</span>
          {/if}
        </span>
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

  .link {
    all: unset;
    cursor: pointer;
    color: var(--md-sys-color-primary);
    text-decoration: underline;
    margin-left: 0.4rem;
  }

  .link:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .output summary {
    cursor: pointer;
    color: var(--text-muted);
    font-size: 0.75rem;
  }

  .output pre {
    margin: 0.25rem 0 0;
    padding: 0.5rem 0.6rem;
    max-height: 12rem;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: var(--md-sys-color-surface-container-highest);
    border-radius: var(--md-sys-shape-corner-small);
    font-size: 0.72rem;
  }

  .record {
    display: block;
    color: var(--text-faint);
    font-size: 0.72rem;
    overflow-wrap: anywhere;
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
    min-width: 0;
  }

  /* The three fixed columns are wider than a phone: keep the time, role and label on one line
     and give the text the full row underneath, so nothing is clipped off the card. */
  @media (max-width: 640px) {
    .row {
      grid-template-columns: auto auto 1fr;
      row-gap: 0.1rem;
    }

    .row .text {
      grid-column: 1 / -1;
    }
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

  /* KAN-1714/ADR-0019: a per-call trace row, tinted by its own outcome so a
     denied/errored call is spottable while scanning, the same subtle-tint
     precedent `.row.handover` already sets rather than a louder banner. */
  .row.tool-denied .type {
    color: var(--status-blocked-fg);
  }

  .row.tool-error .type {
    color: var(--status-failed-fg);
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
