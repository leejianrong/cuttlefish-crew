<script lang="ts">
  // Present mode (CUT-199, ADR-0056): full screen, one artifact at a time in the order the agent wrote
  // in present.json, with its line of speaker text underneath. Arrow keys, Space and the buttons step;
  // the list jumps; Escape (or leaving full screen) closes. An HTML slide deck keeps its own keys
  // while the frame has focus. The manifest is the agent's file, so it is parsed defensively and
  // its text is shown as text.
  import { FleetApiError, type Artifact, type FleetClient } from "../api";
  import { parsePresentation, refusalText, type Presentation } from "../artifacts";
  import ArtifactView from "./ArtifactView.svelte";

  let {
    client,
    projectId,
    files,
    manifest,
    onClose,
  }: {
    client: FleetClient;
    projectId: string;
    files: readonly Artifact[];
    manifest: Artifact;
    onClose: () => void;
  } = $props();

  let plan = $state<Presentation | null>(null);
  let problem = $state<string | null>(null);
  let index = $state(0);
  let root = $state<HTMLDivElement | null>(null);
  let enteredFullscreen = false;

  const step = $derived(plan?.steps[index] ?? null);
  const last = $derived(plan ? plan.steps.length - 1 : 0);

  $effect(() => {
    let cancelled = false;
    client
      .readArtifact(projectId, manifest.path)
      .then((blob) => blob.text())
      .then((text) => {
        if (cancelled) return;
        const parsed = parsePresentation(text, files);
        if (!parsed) problem = "present.json is not a walk-through (it needs an items list).";
        else if (parsed.steps.length === 0) problem = "None of the files in present.json can be shown.";
        else plan = parsed;
      })
      .catch((e: unknown) => {
        if (!cancelled) problem = refusalText(e instanceof FleetApiError ? e.status : 0);
      });
    return () => {
      cancelled = true;
    };
  });

  $effect(() => {
    if (!root) return;
    root.focus();
    // Best effort: a browser that refuses full screen still gets the full-window overlay.
    void root
      .requestFullscreen?.()
      .then(() => {
        enteredFullscreen = true;
      })
      .catch(() => {});
    const left = () => {
      if (enteredFullscreen && !document.fullscreenElement) onClose();
    };
    document.addEventListener("fullscreenchange", left);
    return () => {
      document.removeEventListener("fullscreenchange", left);
      enteredFullscreen = false;
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    };
  });

  function go(to: number) {
    index = Math.min(Math.max(to, 0), last);
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") onClose();
    else if (e.key === "ArrowRight" || e.key === "PageDown") go(index + 1);
    else if (e.key === "ArrowLeft" || e.key === "PageUp") go(index - 1);
    else if (e.key === " " && !(e.target instanceof HTMLSelectElement || e.target instanceof HTMLButtonElement)) go(index + 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(last);
    else return;
    e.preventDefault();
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="present" role="dialog" aria-modal="true" aria-label="Present" tabindex="-1" bind:this={root}>
  <header>
    <h2 class="title-medium">{plan?.title ?? "Present"}</h2>
    {#if plan}
      <span class="body-small muted" aria-live="polite">{index + 1} of {plan.steps.length}</span>
    {/if}
    <button type="button" class="close" onclick={onClose}>Close</button>
  </header>

  {#if problem}
    <p class="body-medium notice" role="alert">{problem}</p>
  {:else if plan && step}
    <main>
      {#key `${step.path}:${step.file.modified}`}
        <ArtifactView {client} {projectId} file={step.file} tall />
      {/key}
    </main>
    <footer>
      <p class="say body-large" data-testid="say">{step.say || step.file.name}</p>
      {#if plan.skipped > 0}
        <p class="body-small muted">
          {plan.skipped} item{plan.skipped === 1 ? "" : "s"} skipped: a file that is missing or cannot be shown.
        </p>
      {/if}
      <nav aria-label="Steps">
        <button type="button" onclick={() => go(index - 1)} disabled={index === 0}>Back</button>
        <select aria-label="Jump to" value={index} onchange={(e) => go(Number(e.currentTarget.value))}>
          {#each plan.steps as s, i (i)}
            <option value={i}>{i + 1}. {s.file.name}</option>
          {/each}
        </select>
        <button type="button" onclick={() => go(index + 1)} disabled={index === last}>Next</button>
      </nav>
    </footer>
  {:else}
    <p class="body-medium muted">Reading…</p>
  {/if}
</div>

<style>
  .present {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    padding: 1rem;
    overflow-y: auto;
    background: var(--md-sys-color-surface);
    color: var(--md-sys-color-on-surface);
  }
  .present:focus { outline: none; }
  header { display: flex; align-items: center; gap: 1rem; }
  header h2 { margin: 0; flex: 1; min-width: 0; overflow-wrap: anywhere; }
  main { flex: 1; min-height: 0; min-width: 0; }
  footer { display: flex; flex-direction: column; gap: 0.5rem; }
  .say { margin: 0; max-width: 60rem; overflow-wrap: anywhere; white-space: pre-wrap; }
  footer p { margin: 0; }
  nav { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
  nav select { min-height: 2.75rem; max-width: 100%; flex: 1; min-width: 8rem; }
  button { min-height: 2.75rem; min-width: 2.75rem; }
  .notice { margin: 0; }
</style>
