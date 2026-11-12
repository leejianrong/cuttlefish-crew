<script lang="ts">
  // What a goal's own worktree gets from the checkout (CUT-305, API CUT-296). The lists live in
  // cuttlefish's registry, not in the repository, so an agent in the worktree cannot widen them.
  import type { FleetClient, ProjectSummary } from "../api";
  import { parsePaths } from "../worktreeshare";

  let {
    client,
    projectId,
    project,
    onChanged,
  }: {
    client: FleetClient;
    projectId: string;
    project: ProjectSummary;
    onChanged: () => void;
  } = $props();

  const saved = $derived(project.worktree_share ?? { dirs: [], files: [] });
  // Seeded from the project; Save writes them back.
  // svelte-ignore state_referenced_locally
  let dirsText = $state(saved.dirs.join("\n"));
  // svelte-ignore state_referenced_locally
  let filesText = $state(saved.files.join("\n"));
  let busy = $state(false);
  let error = $state<string | null>(null);
  let note = $state<string | null>(null);

  const dirty = $derived(
    parsePaths(dirsText).join("\n") !== saved.dirs.join("\n") ||
      parsePaths(filesText).join("\n") !== saved.files.join("\n"),
  );

  async function save() {
    if (busy || !dirty) return;
    busy = true;
    error = note = null;
    try {
      const next = await client.setWorktreeShare(projectId, parsePaths(dirsText), parsePaths(filesText));
      const share = next.worktree_share ?? { dirs: [], files: [] };
      dirsText = share.dirs.join("\n");
      filesText = share.files.join("\n");
      note = "Saved. The next goal's worktree gets this.";
      onChanged();
    } catch (e) {
      error = e instanceof Error ? e.message : "That didn't work.";
    } finally {
      busy = false;
    }
  }
</script>

<section class="card filled share" aria-labelledby="share-heading" data-testid="worktree-share">
  <h2 id="share-heading" class="title-large">Share with a goal's worktree</h2>
  <p class="body-medium">
    A goal's own branch starts in a clean worktree. List what it should get from your checkout so it does not
    reinstall or go without. Used only when a goal gets its own branch; the next goal picks it up.
  </p>
  <ul class="body-small rules">
    <li>
      <strong>Directories are linked, not copied.</strong> The worktree points at your original, so an install
      or a build run there changes your checkout's copy too. Use it for <code>node_modules</code> or a build
      cache, not for anything a goal edits.
    </li>
    <li>
      <strong>Files are copied.</strong> An edit in the worktree never reaches your original. Up to 5 MB each.
    </li>
    <li>
      <strong>Secrets are never shared.</strong> <code>.env</code>, keys and the like are always left out, and
      so is anything the repository does not ignore. Project secrets stay in the secrets store.
    </li>
  </ul>
  <label class="field body-small">
    Directories to link (one per line)
    <textarea rows="3" bind:value={dirsText} placeholder="node_modules" spellcheck="false"></textarea>
  </label>
  <label class="field body-small">
    Files to copy (one per line)
    <textarea rows="3" bind:value={filesText} placeholder="config/local.json" spellcheck="false"></textarea>
  </label>
  {#if error}<p class="body-small error" role="alert">{error}</p>{/if}
  {#if note}<p class="body-small muted" role="status">{note}</p>{/if}
  <div class="actions">
    <button class="btn btn-filled" disabled={busy || !dirty} onclick={save}>
      {busy ? "Saving…" : "Save"}
    </button>
  </div>
</section>

<style>
  .share {
    padding: 1.5rem;
    margin-bottom: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-width: 56rem;
  }

  .share h2,
  .share p {
    margin: 0;
  }

  .rules {
    margin: 0;
    padding-left: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .field {
    border: 0;
    padding: 0;
    background: none;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  textarea {
    font: inherit;
    font-family: var(--md-ref-typeface-mono, monospace);
    padding: 0.5rem 0.75rem;
    border-radius: var(--md-sys-shape-corner-small);
    border: 1px solid var(--md-sys-color-outline);
    background: transparent;
    color: var(--md-sys-color-on-surface);
    resize: vertical;
  }

  .error {
    color: var(--md-sys-color-error);
  }

  .muted {
    color: var(--md-sys-color-on-surface-variant);
  }
</style>
