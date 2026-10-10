<script lang="ts">
  import { secretFailure, secretNameProblem, secretValueProblem } from "../secrets";

  export interface SecretDialogState {
    kind: "add" | "replace" | "remove";
    name: string;
  }

  let {
    dialog,
    existing,
    suggestions,
    nameHelp = "Capital letters, digits and underscores, like GITHUB_TOKEN.",
    removeNote,
    onSave,
    onRemove,
    onClose,
  }: {
    dialog: SecretDialogState;
    /** Names already set in the scope being edited, so saving one can say it replaces it. */
    existing: readonly string[];
    suggestions: readonly string[];
    nameHelp?: string;
    /** What removing it does, in the owner's words. */
    removeNote: string;
    /** Saves; throws to say it failed (the dialog shows why and stays open). */
    onSave: (name: string, value: string) => Promise<void>;
    onRemove: (name: string) => Promise<void>;
    /** The dialog is gone: the owner drops its state. */
    onClose: () => void;
  } = $props();

  // Mounted fresh for every opening, so what was typed last time is never still here. The value
  // lives only in this component and goes with it.
  // svelte-ignore state_referenced_locally
  let nameInput = $state(dialog.name);
  let valueInput = $state("");
  let nameProblem = $state<string | null>(null);
  let valueProblem = $state<string | null>(null);
  let saveProblem = $state<string | null>(null);
  let busy = $state(false);
  let el = $state<HTMLDialogElement | null>(null);

  $effect(() => {
    if (el && !el.open) el.showModal();
  });

  function close() {
    el?.close();
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (busy) return;
    const name = dialog.kind === "replace" ? dialog.name : nameInput.trim();
    nameProblem = dialog.kind === "replace" ? null : secretNameProblem(name);
    valueProblem = secretValueProblem(valueInput);
    saveProblem = null;
    if (nameProblem || valueProblem) return;
    busy = true;
    try {
      await onSave(name, valueInput);
      close();
    } catch (error) {
      saveProblem = secretFailure(error);
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (busy) return;
    busy = true;
    saveProblem = null;
    try {
      await onRemove(dialog.name);
      close();
    } catch (error) {
      saveProblem = secretFailure(error);
    } finally {
      busy = false;
    }
  }

  const replaces = $derived(dialog.kind === "add" && existing.includes(nameInput.trim()));
</script>

<dialog bind:this={el} onclose={onClose} aria-labelledby="secret-dialog-title">
  {#if dialog.kind === "remove"}
    <h3 id="secret-dialog-title" class="headline-small">Remove {dialog.name}?</h3>
    <p class="body-medium muted">{removeNote}</p>
    {#if saveProblem}<p class="body-small error" role="alert">{saveProblem}</p>{/if}
    <div class="actions">
      <button class="btn btn-text" type="button" onclick={close}>Keep it</button>
      <button class="btn btn-danger" type="button" disabled={busy} onclick={remove}>
        {busy ? "Removing…" : "Remove secret"}
      </button>
    </div>
  {:else}
    <h3 id="secret-dialog-title" class="headline-small">
      {dialog.kind === "replace" ? `Replace ${dialog.name}` : "Add a secret"}
    </h3>
    <form onsubmit={save} novalidate>
      <label class="field">
        <span class="field-label">Name</span>
        <input
          class="mono"
          bind:value={nameInput}
          readonly={dialog.kind === "replace"}
          autocomplete="off"
          spellcheck="false"
          aria-invalid={nameProblem ? "true" : undefined}
          aria-describedby="secret-name-help"
        />
      </label>
      <p id="secret-name-help" class="body-small" class:error={nameProblem} class:muted={!nameProblem}>
        {nameProblem ??
          (replaces ? `Saving replaces the value already set for ${nameInput.trim()}.` : nameHelp)}
      </p>
      {#if dialog.kind === "add" && suggestions.length > 0 && nameInput === ""}
        <div class="suggest">
          <span class="label-medium muted">Suggested</span>
          {#each suggestions as name (name)}
            <button class="chip" type="button" onclick={() => (nameInput = name)}>
              <span class="mono">{name}</span>
            </button>
          {/each}
        </div>
      {/if}
      <label class="field value">
        <span class="field-label">Value</span>
        <input
          type="password"
          bind:value={valueInput}
          autocomplete="new-password"
          spellcheck="false"
          aria-invalid={valueProblem ? "true" : undefined}
          aria-describedby="secret-value-help"
        />
      </label>
      <p id="secret-value-help" class="body-small" class:error={valueProblem} class:muted={!valueProblem}>
        {valueProblem ??
          (dialog.kind === "replace"
            ? "The current value cannot be shown. Saving replaces it."
            : "Pasted here, encrypted on save, never shown again.")}
      </p>
      {#if saveProblem}<p class="body-small error" role="alert">{saveProblem}</p>{/if}
      <div class="actions">
        <button class="btn btn-text" type="button" onclick={close}>Cancel</button>
        <button class="btn btn-filled" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save secret"}
        </button>
      </div>
    </form>
  {/if}
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
    width: min(30rem, calc(100vw - 32px));
    box-shadow: var(--md-sys-elevation-level2);
  }

  /* The outlined field's label cuts the border; it must match the dialog, not the page. */
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

  .suggest {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .value {
    margin-top: 4px;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
</style>
