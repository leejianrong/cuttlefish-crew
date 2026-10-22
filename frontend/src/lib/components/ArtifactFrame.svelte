<script lang="ts">
  // An agent's HTML design, run in a sandboxed frame (CUT-175, ADR-0044). The file is fetched with the
  // token and handed over as `srcdoc`, so the frame never has a URL on the daemon. It has an opaque
  // origin (no `allow-same-origin`), cannot navigate, submit or open anything, and its policy allows
  // no network, so a design that is not self-contained shows without its outside files.
  import type { FleetClient } from "../api";
  import { FRAME_SANDBOX, htmlFrameDoc } from "../artifacts";

  let {
    client,
    projectId,
    path,
    label,
    tall = false,
  }: { client: FleetClient; projectId: string; path: string; label: string; tall?: boolean } =
    $props();

  let doc = $state<string | null>(null);
  let failed = $state(false);

  $effect(() => {
    const wanted = path;
    let cancelled = false;
    doc = null;
    failed = false;
    client
      .readArtifact(projectId, wanted)
      .then((blob) => blob.text())
      .then((html) => {
        if (!cancelled) doc = htmlFrameDoc(html);
      })
      .catch(() => {
        if (!cancelled) failed = true;
      });
    return () => {
      cancelled = true;
    };
  });
</script>

{#if doc !== null}
  <iframe
    title={label}
    sandbox={FRAME_SANDBOX}
    srcdoc={doc}
    referrerpolicy="no-referrer"
    class:tall
  ></iframe>
  <p class="note body-small muted">
    Runs in a sandbox: scripts work, but it cannot reach the network, the dashboard or your files.
    Anything it loads from outside its own file is blocked.
  </p>
{:else if failed}
  <span class="muted body-small">{label} (design not available)</span>
{:else}
  <span class="muted body-small">Loading design…</span>
{/if}

<style>
  iframe {
    width: 100%;
    height: 24rem;
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: #fff;
  }

  iframe.tall {
    height: min(70vh, 40rem);
  }

  .note {
    margin: 0.5rem 0 0;
  }
</style>
