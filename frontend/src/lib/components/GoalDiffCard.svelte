<script lang="ts">
  // The last goal's branch diff (CUT-289): files with counts, each opening to its hunks. Read from
  // git on demand by the daemon, never stored. Reading changes nothing. The one thing it can do is
  // send a note on a changed line to the builder (CUT-291): the line number opens a box, and the
  // note goes as a message that quotes the line.
  import { FleetApiError, type FleetClient, type GoalDiffResult, type ProjectSummary } from "../api";
  import { annotationMessage, canAnnotate, diffMoved, lineRef, tooLong, type LineRef } from "../annotate";
  import { composerState, type Composer } from "../chat";
  import { diffSummary, lineNumber, noLinesNote, sourceNote, statusLabel } from "../diff";

  let {
    client,
    projectId,
    project,
    refreshKey = 0,
  }: { client: FleetClient; projectId: string; project: ProjectSummary; refreshKey?: number } =
    $props();

  let diff = $state<GoalDiffResult | null>(null);
  let open = $state<Set<string>>(new Set());
  let busy = $state(false);
  let failed = $state(false);
  let shownFor = "";

  async function load() {
    const id = projectId;
    busy = true;
    try {
      const next = await client.getGoalDiff(id);
      if (id === projectId) {
        diff = next;
        failed = false;
        moved = false;
      }
    } catch {
      failed = true;
    } finally {
      busy = false;
    }
  }

  $effect(() => {
    void refreshKey; // a start or stop of the builder re-reads
    if (shownFor !== projectId) {
      diff = null;
      open = new Set();
      shownFor = projectId;
    }
    void load();
  });

  // One note at a time. Where it goes: a running team's role (steer), else the builder's session
  // (chat), which only some builders take; when it cannot go, the box says why and offers a copy.
  let note = $state<{ ref: LineRef; key: string } | null>(null);
  let draft = $state("");
  let role = $state("");
  let composer = $state<Composer | null>(null);
  let sending = $state(false);
  let moved = $state(false);
  let sentNote = $state<string | null>(null);
  let sendError = $state<string | null>(null);
  const team = $derived(project.running);

  async function openNote(key: string, ref: LineRef) {
    note = { ref, key };
    draft = "";
    moved = false;
    sentNote = sendError = null;
    role = project.roles[0]?.name ?? "";
    if (!team) {
      composer = null;
      composer = composerState(await client.getChat(projectId, null).catch(() => null));
    }
  }

  const canSend = $derived(team ? role !== "" : composer?.enabled === true);

  function message(): string {
    return annotationMessage(note!.ref, draft, diff!);
  }

  async function send(force = false) {
    if (!note || !diff || sending || draft.trim() === "") return;
    const long = tooLong(message());
    if (long) {
      sendError = long;
      return;
    }
    sending = true;
    sendError = null;
    try {
      if (!force) {
        const now = await client.getGoalStat(projectId).catch(() => null);
        if (now?.ok && diffMoved(diff, now)) {
          moved = true;
          return;
        }
      }
      if (team) {
        await client.steerProject(projectId, role, message());
        sentNote = `Sent to ${role}. A running team reads it at its next round; a live Codex role reads it at once.`;
      } else {
        const result = await client.sendChat(projectId, message());
        sentNote = result.sent === "queued" ? "Queued. The builder reads it at its next step." : "Sent to the builder.";
      }
      note = null;
      moved = false;
    } catch (e) {
      sendError =
        e instanceof FleetApiError && e.status === 403
          ? "This device is not allowed to message the builder. Raise it under Devices on the PC."
          : e instanceof Error
            ? e.message
            : "That didn't work.";
    } finally {
      sending = false;
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(message());
      sentNote = "Copied. Paste it into the Terminal tab.";
    } catch {
      sendError = "Could not copy from this page.";
    }
  }

  function toggle(path: string) {
    const next = new Set(open);
    if (!next.delete(path)) next.add(path);
    open = next;
  }

  // Nothing to say until a goal exists.
  const hidden = $derived(diff?.code === "no_goal" || (diff === null && !failed));
</script>

{#if !hidden}
  <section class="card filled changes" aria-labelledby="changes-heading" data-testid="goal-diff">
    <div class="head">
      <h2 id="changes-heading" class="title-large">Changes</h2>
      {#if diff?.ok}
        <span class="label-large summary" data-testid="diff-summary">{diffSummary(diff)}</span>
      {/if}
      <button class="btn btn-text refresh" disabled={busy} onclick={load}>
        {busy ? "Reading…" : "Refresh"}
      </button>
    </div>

    {#if failed && !diff}
      <p class="body-medium muted" role="status">Could not read the diff. Try Refresh.</p>
    {:else if diff && !diff.ok}
      <p class="body-medium muted" role="status" data-testid="diff-refused">{diff.reason}</p>
    {:else if diff}
      <p class="body-small muted">
        <code>{diff.branch}</code>: {sourceNote(diff)}
      </p>
      {#if diff.note}<p class="body-small note" role="status">{diff.note}</p>{/if}
      {#if sentNote}<p class="body-small" role="status" data-testid="note-sent">{sentNote}</p>{/if}
      <ul class="files">
        {#each diff.files ?? [] as file (file.path)}
          {@const expanded = open.has(file.path)}
          <li class="file">
            <button
              class="file-head"
              aria-expanded={expanded}
              aria-controls={`diff-${file.path}`}
              onclick={() => toggle(file.path)}
            >
              <span class="chev" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
              <span class="path mono">
                {#if file.old_path}{file.old_path} &rarr;&nbsp;{/if}{file.path}
              </span>
              {#if statusLabel(file)}<span class="label-small tag">{statusLabel(file)}</span>{/if}
              <span class="counts mono">
                {#if file.withheld}not shown{:else if file.binary}binary{:else}<span class="add">+{file.additions}</span>
                  <span class="del">&minus;{file.deletions}</span>{/if}
              </span>
            </button>
            {#if expanded}
              <div id={`diff-${file.path}`} class="body">
                {#if noLinesNote(file)}
                  <p class="body-small muted pad">{noLinesNote(file)}</p>
                {/if}
                {#each file.hunks as hunk, i (i)}
                  <div class="hunk mono" role="table" aria-label={`Changes in ${file.path}`}>
                    <div class="hunk-head" role="row">
                      @@ -{hunk.old_start} +{hunk.new_start} @@ {hunk.header}
                    </div>
                    {#each hunk.lines as line, n (n)}
                      <div
                        class="row"
                        class:added={line.type === "+"}
                        class:removed={line.type === "-"}
                        role="row"
                      >
                        <span class="num" role="cell">
                          {#if canAnnotate(file, line)}
                            <button
                              class="num-btn"
                              aria-label={`Add a note on ${file.path}, ${line.type === "-" ? "removed" : "added"} line ${lineNumber(line)}`}
                              onclick={() => openNote(`${file.path}:${i}:${n}`, lineRef(file, line))}
                            >{lineNumber(line)}</button>
                          {:else}{lineNumber(line)}{/if}
                        </span>
                        <span class="sign" role="cell" aria-label={line.type === "+" ? "added" : line.type === "-" ? "removed" : "unchanged"}
                          >{line.type}</span
                        >
                        <span class="text" role="cell">{line.text}</span>
                      </div>
                    {/each}
                  </div>
                {/each}
                {#if file.truncated}
                  <p class="body-small note pad">Cut here: this file has more changes than are shown.</p>
                {/if}
              </div>
              {#if note && note.ref.path === file.path}
                <form
                  class="note-form"
                  data-testid="diff-note"
                  onsubmit={(event) => {
                    event.preventDefault();
                    void send();
                  }}
                >
                  <p class="label-medium">
                    Note on {note.ref.side === "old" ? "removed" : "added"} line {note.ref.line}:
                    <code class="quote">{note.ref.quote}</code>
                  </p>
                  <textarea bind:value={draft} rows="3" aria-label="Your note" placeholder="What should change here?"></textarea>
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
                      The chat can only type to a Claude Code builder that is running, so this note cannot be sent
                      from here. Copy it and paste it into the Terminal tab.
                    </p>
                  {/if}
                  {#if moved}
                    <p class="body-small note" role="alert">
                      The branch changed since this diff was read, so the line may have moved. The note quotes the
                      line. Refresh to see the new diff, or send it anyway.
                    </p>
                  {/if}
                  {#if sendError}<p class="body-small note" role="alert">{sendError}</p>{/if}
                  <div class="note-actions">
                    {#if moved}
                      <button type="button" class="btn btn-filled" disabled={sending} onclick={() => send(true)}>Send anyway</button>
                    {:else}
                      <button type="submit" class="btn btn-filled" disabled={sending || !canSend || draft.trim() === ""}>
                        {sending ? "Sending…" : team ? "Send to the role" : "Send to the builder"}
                      </button>
                    {/if}
                    <button type="button" class="btn btn-tonal" onclick={copy} disabled={draft.trim() === ""}>Copy</button>
                    <button type="button" class="btn btn-text" onclick={() => (note = null)}>Cancel</button>
                  </div>
                </form>
              {/if}
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  .changes {
    padding: 1.5rem;
    margin-bottom: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-width: 56rem;
  }

  .changes h2,
  .changes p {
    margin: 0;
  }

  .head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
  }

  .summary {
    color: var(--md-sys-color-on-surface-variant);
  }

  .refresh {
    margin-left: auto;
  }

  .note {
    color: var(--md-sys-color-error);
  }

  .files {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .file {
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    overflow: hidden;
    background: var(--md-sys-color-surface-container-lowest);
  }

  .file-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.25rem 0.5rem;
    width: 100%;
    min-height: 2.75rem;
    padding: 0.25rem 0.75rem;
    border: 0;
    background: transparent;
    color: var(--md-sys-color-on-surface);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  .file-head:hover {
    background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent);
  }

  .file-head:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: -2px;
  }

  .path {
    flex: 1 1 9rem;
    min-width: 0;
    overflow-wrap: anywhere;
    font-size: 0.8125rem;
  }

  .tag {
    padding: 2px 8px;
    border-radius: var(--md-sys-shape-corner-small, 8px);
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
    white-space: nowrap;
  }

  .counts {
    font-size: 0.8125rem;
    white-space: nowrap;
  }

  .add {
    color: var(--md-sys-color-success);
  }

  .del {
    color: var(--md-sys-color-error);
  }

  .body {
    border-top: 1px solid var(--md-sys-color-outline-variant);
    /* A long file scrolls inside its own box rather than making the page endless. */
    max-height: 26rem;
    overflow: auto;
  }

  .pad {
    padding: 0.5rem 0.75rem;
  }

  .hunk {
    font-size: 0.8125rem;
    line-height: 1.25rem;
    min-width: max-content;
    width: 100%;
  }

  .hunk-head {
    padding: 0.125rem 0.75rem;
    background: var(--md-sys-color-surface-container);
    color: var(--md-sys-color-on-surface-variant);
  }

  .row {
    display: flex;
  }

  .num {
    flex: none;
    width: 3.5rem;
    padding-right: 0.5rem;
    text-align: right;
    color: var(--md-sys-color-on-surface-variant);
    user-select: none;
  }

  .sign {
    flex: none;
    width: 1.25rem;
    text-align: center;
    user-select: none;
  }

  .text {
    flex: 1;
    white-space: pre;
    padding-right: 0.75rem;
  }

  .added {
    background: var(--md-sys-color-success-container);
    color: var(--md-sys-color-on-success-container);
  }

  .removed {
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
  }

  .num-btn {
    padding: 0 0.25rem;
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
    text-decoration: underline dotted;
  }

  .num-btn:hover,
  .num-btn:focus-visible {
    background: color-mix(in srgb, currentColor 18%, transparent);
    outline: 2px solid var(--md-sys-color-primary);
  }

  @media (pointer: coarse) {
    .row {
      line-height: 1.75rem;
    }
  }

  .note-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.75rem;
    border-top: 1px solid var(--md-sys-color-outline-variant);
    background: var(--md-sys-color-surface-container-low);
  }

  .note-form p {
    margin: 0;
  }

  .quote {
    display: block;
    margin-top: 0.25rem;
    overflow-wrap: anywhere;
    font-weight: 400;
  }

  .note-form textarea {
    min-height: 4.5rem;
    padding: 0.5rem;
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-small, 8px);
    background: transparent;
    color: inherit;
    font: inherit;
  }

  .note-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .added .num,
  .removed .num {
    color: inherit;
    opacity: 0.7;
  }
</style>
