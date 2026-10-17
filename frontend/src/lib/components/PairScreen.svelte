<script lang="ts">
  import {
    PairingFailed,
    formatCode,
    pairWithDaemon,
    saveIdentity,
    type DeviceIdentity,
    type PairingLink,
  } from "../relay";
  import { deviceName, identityStore } from "../relayconnect";

  let {
    link,
    onPaired,
    onCancel,
  }: { link: PairingLink; onPaired: (identity: DeviceIdentity) => void; onCancel: () => void } =
    $props();

  let name = $state(deviceName());
  let phase = $state<"ready" | "connecting" | "waiting" | "failed">("ready");
  let code = $state("");
  let error = $state<string | null>(null);

  async function pair(event: SubmitEvent) {
    event.preventDefault();
    const store = identityStore();
    if (!store) {
      error = "This browser will not keep a pairing (its storage is blocked), so pairing would not last.";
      phase = "failed";
      return;
    }
    phase = "connecting";
    error = null;
    try {
      const identity = await pairWithDaemon(link, name.trim() || deviceName(), (shown) => {
        code = shown;
        phase = "waiting";
      });
      await saveIdentity(store, identity);
      onPaired(identity);
    } catch (failure) {
      error =
        failure instanceof PairingFailed || failure instanceof Error
          ? failure.message
          : "Pairing failed.";
      phase = "failed";
    }
  }
</script>

<div class="wrap">
  <form class="card elevated" onsubmit={pair}>
    <h1 class="headline-small">Pair this device</h1>

    {#if phase === "ready" || phase === "connecting"}
      <p class="hint">
        This links this browser to your PC through the relay, end to end encrypted. You then
        confirm it on the PC.
      </p>
      <label>
        Name this device
        <input type="text" bind:value={name} maxlength="40" required disabled={phase === "connecting"} />
      </label>
      <button type="submit" class="btn btn-filled" disabled={phase === "connecting"}>
        {phase === "connecting" ? "Connecting…" : "Pair"}
      </button>
    {:else if phase === "waiting"}
      <p class="hint">Confirm on the PC only if its screen shows this same code.</p>
      <p class="code" aria-live="polite" aria-label="Pairing code {code.split('').join(' ')}">
        {formatCode(code)}
      </p>
      <p class="hint">Waiting for you to confirm on the PC. Devices, then Confirm.</p>
    {:else}
      <p class="error" role="alert">{error}</p>
    {/if}

    <button type="button" class="btn btn-text" onclick={onCancel}>
      {phase === "failed" ? "Back" : "Cancel"}
    </button>
  </form>
</div>

<style>
  .wrap {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
  }

  form {
    width: 100%;
    max-width: 26rem;
    padding: 2rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  h1,
  p {
    margin: 0;
  }

  .hint {
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.5;
  }

  .code {
    font: 700 2.5rem/1 var(--md-ref-typeface-mono);
    letter-spacing: 0.12em;
    text-align: center;
    padding: 1rem 0;
  }

  label {
    display: block;
    font-size: 0.82rem;
    color: var(--text-muted);
  }

  input {
    display: block;
    width: 100%;
    margin-top: 0.35rem;
    padding: 0.55rem 0.7rem;
    background: transparent;
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-extra-small);
    color: var(--text);
  }

  input:focus {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: -1px;
  }

  .error {
    color: var(--md-sys-color-error);
    font-size: 0.9rem;
    line-height: 1.5;
  }
</style>
