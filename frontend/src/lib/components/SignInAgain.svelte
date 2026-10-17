<script lang="ts">
  import {
    FleetApiError,
    FleetClient,
    FleetUnreachableError,
    fetchAuthMode,
    login,
    type AuthMode,
  } from "../api";

  // Shown over the app when the daemon stops accepting the saved sign-in (CUT-112): a daemon that
  // restarted (password sessions are signed with a per-start secret, ADR-0011), a session that ran
  // out, or a loopback token that changed. The page keeps its place, so one sign-in carries on.
  let {
    client,
    onSignedIn,
    onDisconnect,
  }: {
    client: FleetClient;
    onSignedIn: (client: FleetClient) => void;
    onDisconnect: () => void;
  } = $props();

  let mode = $state<AuthMode | null>(null);
  let credential = $state("");
  let busy = $state(false);
  let problem = $state<string | null>(null);
  let el = $state<HTMLDialogElement | null>(null);

  $effect(() => {
    if (el && !el.open) el.showModal();
  });

  // Which credential to ask for comes from the daemon itself, as on the Connect screen.
  $effect(() => {
    let current = true;
    fetchAuthMode(client.baseUrl)
      .then((found) => {
        if (current) mode = found;
      })
      .catch(() => {
        if (current) mode = "password";
      });
    return () => {
      current = false;
    };
  });

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (busy || credential.trim() === "") return;
    busy = true;
    problem = null;
    try {
      const token = mode === "token" ? credential.trim() : await login(client.baseUrl, credential);
      const next = new FleetClient(client.baseUrl, token);
      await next.listProjects();
      onSignedIn(next);
    } catch (error) {
      problem =
        error instanceof FleetUnreachableError
          ? error.message
          : error instanceof FleetApiError && error.status === 401
            ? mode === "token"
              ? "That token was rejected."
              : "That password was rejected."
            : error instanceof FleetApiError && error.status === 429
              ? error.message
              : "Could not sign in. Try again.";
    } finally {
      busy = false;
    }
  }
</script>

<dialog
  bind:this={el}
  aria-labelledby="signin-title"
  oncancel={(event) => event.preventDefault()}
>
  <h3 id="signin-title" class="headline-small">Sign in again</h3>
  <p class="body-medium muted">
    {#if mode === "token"}
      The daemon no longer accepts this token. It was probably restarted with a new one. Paste the
      token printed where <code>cuttlefish serve</code> runs.
    {:else}
      You were signed out. The daemon was probably restarted, or the session ran out. Enter your
      password to carry on where you were.
    {/if}
  </p>
  <form onsubmit={submit}>
    <label class="field">
      <span class="field-label">{mode === "token" ? "Token" : "Password"}</span>
      <input
        type="password"
        bind:value={credential}
        autocomplete={mode === "token" ? "off" : "current-password"}
        spellcheck="false"
        aria-invalid={problem ? "true" : undefined}
        aria-describedby={problem ? "signin-problem" : undefined}
      />
    </label>
    {#if problem}<p id="signin-problem" class="body-small error" role="alert">{problem}</p>{/if}
    <div class="actions">
      <button class="btn btn-text" type="button" onclick={onDisconnect}>Disconnect</button>
      <button class="btn btn-filled" type="submit" disabled={busy || mode === null}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </div>
  </form>
</dialog>

<style>
  h3,
  p {
    margin: 0;
  }

  .error {
    color: var(--md-sys-color-error);
  }

  dialog {
    border: 0;
    border-radius: var(--md-sys-shape-corner-extra-large);
    background: var(--md-sys-color-surface-container-high);
    color: var(--md-sys-color-on-surface);
    padding: 24px;
    width: min(28rem, calc(100vw - 32px));
    box-shadow: var(--md-sys-elevation-level2);
  }

  dialog :global(.field-label) {
    background: var(--md-sys-color-surface-container-high);
  }

  dialog :global(.field:has([aria-invalid="true"])) {
    border-color: var(--md-sys-color-error);
  }

  dialog::backdrop {
    background: color-mix(in srgb, var(--md-sys-color-scrim) 32%, transparent);
  }

  dialog[open],
  form {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
</style>
