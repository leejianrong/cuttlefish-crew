<script lang="ts">
  // One artifact, drawn by what it is (CUT-97, CUT-175): markdown by the dashboard's own renderer,
  // an image or video from a blob URL, an HTML design in a sandboxed frame. Used by the Artifacts
  // tab and by the chat's canvas, so a file looks the same in both.
  import { FleetApiError, type Artifact, type FleetClient } from "../api";
  import { refusalText } from "../artifacts";
  import { parseMarkdown } from "../markdown";
  import ArtifactFrame from "./ArtifactFrame.svelte";
  import ArtifactImage from "./ArtifactImage.svelte";
  import ArtifactVideo from "./ArtifactVideo.svelte";
  import Markdown from "./Markdown.svelte";

  let {
    client,
    projectId,
    file,
    tall = false,
  }: { client: FleetClient; projectId: string; file: Artifact; tall?: boolean } = $props();

  let text = $state<string | null>(null);
  let problem = $state<string | null>(null);
  const blocks = $derived(text !== null ? parseMarkdown(text, file.path) : null);
  // A file written again has a new modified time, so it is read again.
  const stamp = $derived(`${file.path}:${file.modified}`);

  $effect(() => {
    const wanted = file;
    text = null;
    problem = null;
    if (wanted.kind !== "markdown") return;
    let cancelled = false;
    client
      .readArtifact(projectId, wanted.path)
      .then((blob) => blob.text())
      .then((body) => {
        if (!cancelled) text = body;
      })
      .catch((e: unknown) => {
        if (!cancelled) problem = refusalText(e instanceof FleetApiError ? e.status : 0);
      });
    return () => {
      cancelled = true;
    };
  });
</script>

{#if file.kind === "other"}
  <p class="muted body-medium">
    {file.name} is listed but not shown here. Markdown, images, video and HTML designs are.
  </p>
{:else if file.kind === "image"}
  {#key stamp}<ArtifactImage {client} {projectId} path={file.path} alt={file.name} />{/key}
{:else if file.kind === "video"}
  {#key stamp}<ArtifactVideo {client} {projectId} path={file.path} label={file.name} />{/key}
{:else if file.kind === "html"}
  {#key stamp}<ArtifactFrame {client} {projectId} path={file.path} label={file.name} {tall} />{/key}
{:else if problem}
  <p class="body-medium error" role="alert">{problem}</p>
{:else if blocks}
  <Markdown {blocks} {client} {projectId} />
{:else}
  <p class="muted body-medium">Reading…</p>
{/if}

<style>
  .error {
    color: var(--md-sys-color-error);
  }
</style>
