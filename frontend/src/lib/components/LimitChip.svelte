<script lang="ts">
  // The usage-limit wait as one chip (CUT-311): when Autopilot continues and a countdown, or, when no
  // reset time is known, that the builder is waiting for a person. The daemon holds the time.
  import { limitChip, waitingChip, type AutopilotStatus } from "../autopilot";
  import Icon from "./Icon.svelte";

  let { status }: { status: AutopilotStatus } = $props();

  // The countdown only moves while a wait is shown.
  let now = $state(new Date());
  const chip = $derived(limitChip(status, now));
  // A wait on a person (CUT-312) shares the chip: same place, same coral when only a person can end it.
  const waiting = $derived(chip === null ? waitingChip(status, now) : null);
  const shown = $derived(chip !== null || waiting !== null);
  $effect(() => {
    if (!shown) return;
    now = new Date();
    const timer = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(timer);
  });
</script>

{#if chip}
  <p class="limit-chip label-large" class:needs={chip.needsYou} role="status">
    <Icon name={chip.needsYou ? "bell" : "clock"} size={18} />
    <span>{chip.text}{#if chip.countdown}<span class="countdown">{" "}({chip.countdown})</span>{/if}</span>
  </p>
{:else if waiting}
  <p class="limit-chip label-large needs" role="status">
    <Icon name="bell" size={18} />
    <span>{waiting.text}<span class="countdown">{" "}({waiting.age})</span></span>
  </p>
  {#if waiting.hint}<p class="hint body-small muted">{waiting.hint}</p>{/if}
{/if}

<style>
  .limit-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    align-self: flex-start;
    max-width: 100%;
    margin: 0;
    padding: 0.4rem 0.8rem;
    border-radius: 8px;
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
    font-weight: 600;
  }

  .limit-chip.needs {
    background: var(--md-sys-color-attention-container);
    color: var(--md-sys-color-on-attention-container);
  }

  .hint {
    margin: 0.25rem 0 0;
  }

  .countdown {
    font-weight: 400;
  }
</style>
