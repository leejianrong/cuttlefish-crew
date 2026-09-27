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
      error = err instanceof FleetUnreachableError ? err.message : "something went wrong";
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
        error = "something went wrong connecting";
      }
    } finally {
      connecting = false;
    }
  }

  function credentialRejectedMessage(): string {
    return mode === "password" ? "that password was rejected" : "that token was rejected";
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
    <form onsubmit={checkBaseUrl}>
      <h1>cuttlefish-crew</h1>
      <p class="hint">
        Connect to a running <code>cuttlefish serve</code> -- its base URL is printed to that
        process's own stdout at startup.
      </p>

      <label>
        Base URL
        <input type="text" bind:value={baseUrl} placeholder="http://127.0.0.1:8420" required />
      </label>

      {#if error}
        <p class="error">{error}</p>
      {/if}

      <button type="submit" disabled={connecting}>
        {connecting ? "Checking…" : "Continue"}
      </button>

      <button type="button" class="gallery-link" onclick={onShowGallery}>
        No daemon running yet? See the sprites first &rarr;
      </button>
    </form>
  {:else}
    <form onsubmit={connect}>
      <h1>cuttlefish-crew</h1>
      {#if mode === "password"}
        <p class="hint">
          This daemon is bound non-loopback and needs its own login (ADR-0011) -- the password is
          whatever <code>CUTTLEFISH_SERVE_PASSWORD</code> was set to when it started.
        </p>
        <label>
          Password
          <input
            type="password"
            bind:value={credential}
            placeholder="CUTTLEFISH_SERVE_PASSWORD"
            required
          />
        </label>
      {:else}
        <p class="hint">
          Its token is printed to that process's own stdout at startup.
        </p>
        <label>
          Token
          <input type="password" bind:value={credential} placeholder="x-cuttlefish-token" required />
        </label>
      {/if}

      {#if error}
        <p class="error">{error}</p>
      {/if}

      <button type="submit" disabled={connecting}>
        {connecting ? "Connecting…" : "Connect"}
      </button>

      <button type="button" class="gallery-link" onclick={backToUrl}>
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
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 2rem;
  }

  h1 {
    margin: 0 0 0.5rem;
    font-size: 1.3rem;
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
    background: var(--bg-inset);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
  }

  input:focus {
    outline: 2px solid var(--accent);
    outline-offset: -1px;
  }

  button {
    width: 100%;
    padding: 0.65rem;
    border: none;
    border-radius: 8px;
    background: var(--accent);
    color: var(--accent-text);
    font-weight: 600;
  }

  button:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .error {
    color: var(--danger);
    font-size: 0.85rem;
    margin: -0.4rem 0 1rem;
  }

  .gallery-link {
    width: auto;
    background: none;
    color: var(--text-muted);
    font-weight: 500;
    font-size: 0.8rem;
    padding: 0.6rem 0;
    margin: 0 auto;
    display: block;
  }

  .gallery-link:hover {
    color: var(--accent);
  }
</style>
