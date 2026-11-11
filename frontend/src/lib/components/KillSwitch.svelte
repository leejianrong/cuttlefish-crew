<script lang="ts">
  // The kill switch (CUT-186): stop every running agent of one project, or of all of them. Two
  // taps on purpose, so a thumb cannot press it by accident, and nothing to type: it has to work
  // from a phone away from the PC.
  import { refusal, type FleetClient } from "../api";
  import { killResultText } from "../killswitch";
  import Icon from "./Icon.svelte";

  let {
    client,
    projectId = null,
    onDone,
    quiet = false,
  }: {
    client: FleetClient;
    projectId?: string | null;
    onDone?: () => void;
    /** An outlined button, red only on hover and in the confirm step (CUT-283). */
    quiet?: boolean;
  } = $props();

  let confirming = $state(false);
  let busy = $state(false);
  let result = $state<string | null>(null);
  let problem = $state<string | null>(null);

  const everything = $derived(projectId === null);
  const label = $derived(everything ? "Stop all agents" : "Stop everything here");

  async function press() {
    busy = true;
    problem = null;
    try {
      const stopped = projectId === null ? await client.killAll() : await client.killProject(projectId);
      result = killResultText(stopped);
      confirming = false;
      onDone?.();
    } catch (error) {
      problem =
        refusal(error) ?? "Couldn't stop them. Check that the daemon is still running.";
    } finally {
      busy = false;
    }
  }
</script>

<div class="kill" class:wide={everything}>
  {#if confirming}
    <div class="confirm" role="alertdialog" aria-label={label}>
      <span class="body-medium">
        {everything
          ? "Stop every agent on every project now? Builders end at once; a team ends when its current round does."
          : "Stop this project's builder and team now? The builder ends at once; a team ends when its current round does."}
      </span>
      <div class="row">
        <button class="btn btn-danger" disabled={busy} onclick={press}>
          {busy ? "Stopping…" : "Stop now"}
        </button>
        <button class="btn btn-text" disabled={busy} onclick={() => (confirming = false)}>
          Keep them running
        </button>
      </div>
    </div>
  {:else}
    <button
      class={["btn", quiet ? "btn-outlined quiet" : "btn-danger"]}
      onclick={() => {
        result = null;
        confirming = true;
      }}
    >
      <Icon name="stop" size={18} />{label}
    </button>
  {/if}
  {#if result}<p class="body-small result" role="status">{result}</p>{/if}
  {#if problem}<p class="body-small problem" role="alert">{problem}</p>{/if}
</div>

<style>
  .kill {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }

  .btn {
    min-height: 44px;
  }

  .quiet {
    color: var(--md-sys-color-on-surface-variant);
    border-color: var(--md-sys-color-outline-variant);
  }

  .quiet:hover,
  .quiet:active,
  .quiet:focus-visible {
    color: var(--md-sys-color-error);
    border-color: var(--md-sys-color-error);
  }

  .confirm {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 28rem;
    padding: 12px 16px;
    border-radius: var(--md-sys-shape-corner-medium);
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
  }

  .confirm .btn-danger {
    background: var(--md-sys-color-error);
    color: var(--md-sys-color-on-error);
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .result,
  .problem {
    margin: 0;
  }

  .problem {
    color: var(--md-sys-color-error);
  }
</style>
