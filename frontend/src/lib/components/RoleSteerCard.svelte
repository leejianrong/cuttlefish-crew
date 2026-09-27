<script lang="ts">
  import type { EpisodicEventView, FleetClient, RoleStatus } from "../api";
  import StatusChip from "./StatusChip.svelte";

  let {
    client,
    projectId,
    role,
    status,
    handovers = [],
  }: {
    client: FleetClient;
    projectId: string;
    role: string;
    status: RoleStatus;
    /** This role's own `HandoverWritten` checkpoints, oldest first (ADR-0010/KAN-1705) --
     * continuity made visible, not just trusted. */
    handovers?: EpisodicEventView[];
  } = $props();

  let message = $state("");
  let sending = $state(false);
  let sent = $state(false);

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
</script>

<div class="role-card">
  <div class="role-head">
    <span class="role-name">{role}</span>
    <StatusChip {status} />
  </div>
  <div class="steer">
    <textarea
      bind:value={message}
      onkeydown={onKeydown}
      placeholder="Redirect {role}'s work… (takes effect at the next round boundary)"
      rows="2"
    ></textarea>
    <button onclick={send} disabled={sending || !message.trim()}>
      {sent ? "Sent" : sending ? "Sending…" : "Send"}
    </button>
  </div>

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
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 12px;
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

  .steer {
    display: flex;
    gap: 0.6rem;
    align-items: flex-end;
  }

  textarea {
    flex: 1;
    padding: 0.5rem 0.65rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    resize: vertical;
  }

  button {
    padding: 0.5rem 0.9rem;
    border: none;
    border-radius: 8px;
    background: var(--accent);
    color: var(--accent-text);
    font-weight: 600;
    white-space: nowrap;
  }

  button:disabled {
    opacity: 0.5;
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
