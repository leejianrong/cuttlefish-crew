<script lang="ts">
  import type { RoleStatus } from "../api";

  let { status }: { status: RoleStatus } = $props();

  const labels: Record<RoleStatus, string> = {
    queued: "queued",
    working: "working",
    blocked: "blocked",
    done: "done",
    failed: "failed",
    stopped: "stopped",
  };
</script>

<span class="chip chip-{status}">
  <span class="dot"></span>{labels[status]}
</span>

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4em;
    padding: 0.2em 0.65em;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .dot {
    width: 0.5em;
    height: 0.5em;
    border-radius: 999px;
    background: currentColor;
    flex-shrink: 0;
  }

  .chip-queued {
    background: var(--status-queued-bg);
    color: var(--status-queued-fg);
  }
  .chip-working {
    background: var(--status-working-bg);
    color: var(--status-working-fg);
  }
  .chip-working .dot {
    animation: pulse 1.4s ease-in-out infinite;
  }
  .chip-blocked {
    background: var(--status-blocked-bg);
    color: var(--status-blocked-fg);
  }
  .chip-done {
    background: var(--status-done-bg);
    color: var(--status-done-fg);
  }
  .chip-stopped {
    background: var(--status-queued-bg);
    color: var(--status-queued-fg);
  }
  .chip-failed {
    background: var(--status-failed-bg);
    color: var(--status-failed-fg);
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.35;
    }
  }
</style>
