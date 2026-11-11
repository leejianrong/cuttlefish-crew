<script lang="ts">
  // A box that sends a message to the project's builder, on a person's button and never on its own
  // (CUT-291, CUT-293). Where it goes: a running team's role (a steer), else the builder's session
  // (chat), which only a running Claude Code builder takes. When it cannot go, the box says why
  // and offers a copy for the Terminal tab, so it never implies a live prompt that is not there.
  import type { FleetClient, ProjectSummary } from "../api";
  import { tooLong } from "../annotate";
  import { composerState, type Composer } from "../chat";
  import { COPIED_NOTE, deliver, failureText } from "../sendtobuilder";

  let {
    client,
    projectId,
    project,
    initial = "",
    compose = (text: string) => text,
    placeholder = "What should the builder do?",
    rows = 3,
    check = null,
    onSent,
    onCancel,
  }: {
    client: FleetClient;
    projectId: string;
    project: ProjectSummary;
    /** Text to start the box with, which the person can edit before sending. */
    initial?: string;
    /** Turns the box's text into the message (a note quotes the line it is about). */
    compose?: (draft: string) => string;
    placeholder?: string;
    rows?: number;
    /** Run before sending; a sentence back is a warning, and the button becomes "Send anyway". */
    check?: (() => Promise<string | null>) | null;
    onSent: (said: string) => void;
    onCancel: () => void;
  } = $props();

  // Read once on purpose: the box starts with `initial`; after that it is the person's text.
  // svelte-ignore state_referenced_locally
  let draft = $state(initial);
  // svelte-ignore state_referenced_locally
  let role = $state(project.roles[0]?.name ?? "");
  let composer = $state<Composer | null>(null);
  let sending = $state(false);
  let warning = $state<string | null>(null);
  let error = $state<string | null>(null);
  let copied = $state<string | null>(null);
  const team = $derived(project.running);

  $effect(() => {
    if (team) return;
    let stale = false;
    client
      .getChat(projectId, null)
      .catch(() => null)
      .then((response) => {
        if (!stale) composer = composerState(response);
      });
    return () => (stale = true);
  });

  const canSend = $derived(team ? role !== "" : composer?.enabled === true);
  const message = $derived(compose(draft));

  async function send(force = false) {
    if (sending || draft.trim() === "") return;
    const long = tooLong(message);
    if (long) {
      error = long;
      return;
    }
    sending = true;
    error = null;
    try {
      if (!force && check) {
        warning = await check();
        if (warning) return;
      }
      onSent(await deliver(client, projectId, { team, role }, message));
    } catch (e) {
      error = failureText(e);
    } finally {
      sending = false;
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(message);
      copied = COPIED_NOTE;
    } catch {
      error = "Could not copy from this page.";
    }
  }
</script>

<form
  class="send"
  onsubmit={(event) => {
    event.preventDefault();
    void send();
  }}
>
  <textarea bind:value={draft} {rows} aria-label="Message" {placeholder}></textarea>
  {#if team}
    <label class="body-small">
      Send to role
      <select bind:value={role}>
        {#each project.roles as r (r.name)}<option value={r.name}>{r.name}</option>{/each}
      </select>
    </label>
  {:else if composer}
    <p class="body-small muted" role="status">{composer.hint}</p>
  {:else}
    <p class="body-small muted" role="status">
      The chat can only type to a Claude Code builder that is running, so this cannot be sent from here.
      Copy it and paste it into the Terminal tab.
    </p>
  {/if}
  {#if warning}<p class="body-small warn" role="alert">{warning}</p>{/if}
  {#if error}<p class="body-small warn" role="alert">{error}</p>{/if}
  {#if copied}<p class="body-small" role="status">{copied}</p>{/if}
  <div class="actions">
    {#if warning}
      <button type="button" class="btn btn-filled" disabled={sending} onclick={() => send(true)}>Send anyway</button>
    {:else}
      <button type="submit" class="btn btn-filled" disabled={sending || !canSend || draft.trim() === ""}>
        {sending ? "Sending…" : team ? "Send to the role" : "Send to the builder"}
      </button>
    {/if}
    <button type="button" class="btn btn-tonal" onclick={copy} disabled={draft.trim() === ""}>Copy</button>
    <button type="button" class="btn btn-text" onclick={onCancel}>Cancel</button>
  </div>
</form>

<style>
  .send {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .send p {
    margin: 0;
  }

  textarea {
    min-height: 4.5rem;
    padding: 0.5rem;
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-small, 8px);
    background: transparent;
    color: inherit;
    font: inherit;
  }

  .warn {
    color: var(--md-sys-color-error);
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
</style>
