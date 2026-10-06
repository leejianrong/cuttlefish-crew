<script lang="ts">
  import {
    FleetClient,
    FleetApiError,
    FleetUnreachableError,
    fetchAuthMode,
    login as loginRequest,
    type AuthMode,
  } from "../api";

  let {
    onConnected,
    onShowGallery,
  }: { onConnected: (client: FleetClient) => void; onShowGallery: () => void } = $props();

  // ADR-0011: a daemon's own `/api/auth-mode` says whether it wants the classic
  // loopback-mode static token or a non-loopback bind's password login -- checked
  // once the operator supplies a base URL, before either credential field renders,
  // so the form never has to guess which one to ask for.
  let step = $state<"url" | "credential">("url");
  let mode = $state<AuthMode | null>(null);
  let baseUrl = $state("http://127.0.0.1:8420");
  let credential = $state("");
  let connecting = $state(false);
  let error = $state<string | null>(null);

  async function checkBaseUrl(event: SubmitEvent) {
    event.preventDefault();
    connecting = true;
    error = null;
    try {
      mode = await fetchAuthMode(baseUrl.replace(/\/$/, ""));
      step = "credential";
    } catch (err) {
      error = err instanceof FleetUnreachableError ? err.message : "Something went wrong. Check the address and try again.";
    } finally {
      connecting = false;
    }
  }

  async function connect(event: SubmitEvent) {
    event.preventDefault();
    connecting = true;
    error = null;
    const trimmedBaseUrl = baseUrl.replace(/\/$/, "");
    try {
      const token =
        mode === "password" ? await loginRequest(trimmedBaseUrl, credential) : credential.trim();
      const client = new FleetClient(trimmedBaseUrl, token);
      await client.listProjects();
      onConnected(client);
    } catch (err) {
      if (err instanceof FleetUnreachableError) {
        error = err.message;
      } else if (err instanceof FleetApiError) {
        error = err.status === 401 ? credentialRejectedMessage() : err.message;
      } else {
        error = "Something went wrong connecting. Check the address and try again.";
      }
    } finally {
      connecting = false;
    }
  }

  function credentialRejectedMessage(): string {
    return mode === "password" ? "That password was rejected." : "That token was rejected.";
  }

  function backToUrl() {
    step = "url";
    mode = null;
    credential = "";
    error = null;
  }
</script>

<div class="wrap">
  {#if step === "url"}
    <form class="card elevated" onsubmit={checkBaseUrl}>
      <h1 class="headline-small">cuttlefish-crew</h1>
      <p class="hint">
        Connect to a running <code>cuttlefish serve</code>. Its address is printed in the
        terminal where you started it.
      </p>

      <label>
        Base URL
        <input type="text" bind:value={baseUrl} placeholder="http://127.0.0.1:8420" required />
      </label>

      {#if error}
        <p class="error" role="alert">{error}</p>
      {/if}

      <button type="submit" class="btn btn-filled submit" disabled={connecting}>
        {connecting ? "Checking…" : "Continue"}
      </button>

      <button type="button" class="btn btn-text gallery-link" onclick={onShowGallery}>
        No daemon running yet? See the sprites first &rarr;
      </button>
    </form>
  {:else}
    <form class="card elevated" onsubmit={connect}>
      <h1 class="headline-small">cuttlefish-crew</h1>
      {#if mode === "password"}
        <p class="hint">
          This daemon is reachable from other machines, so it asks for a password: the one you set
          in <code>CUTTLEFISH_SERVE_PASSWORD</code> when you started it.
        </p>
        <label>
          Password
          <input
            type="password"
            bind:value={credential}
            placeholder="Password"
            required
          />
        </label>
      {:else}
        <p class="hint">
          Its token is printed in that same terminal, after the address.
        </p>
        <label>
          Token
          <input type="password" bind:value={credential} placeholder="Token" required />
        </label>
      {/if}

      {#if error}
        <p class="error" role="alert">{error}</p>
      {/if}

      <button type="submit" class="btn btn-filled submit" disabled={connecting}>
        {connecting ? "Connecting…" : "Connect"}
      </button>

      <button type="button" class="btn btn-text gallery-link" onclick={backToUrl}>
        &larr; back
      </button>
    </form>
  {/if}
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
  }

  h1 {
    margin: 0 0 0.5rem;
  }

  .hint {
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.5;
    margin: 0 0 1.5rem;
  }

  .hint code {
    color: var(--text);
  }

  label {
    display: block;
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-bottom: 1rem;
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
    border-color: var(--md-sys-color-primary);
  }

  .submit {
    width: 100%;
  }

  .error {
    color: var(--danger);
    font-size: 0.85rem;
    margin: -0.4rem 0 1rem;
  }

  .gallery-link {
    display: flex;
    margin: 0.5rem auto 0;
    height: auto;
    min-height: 40px;
    padding: 0.5rem 0.75rem;
    white-space: normal;
    text-align: center;
  }
</style>
