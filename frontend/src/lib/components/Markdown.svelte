<script lang="ts">
  // Renders the tree `parseMarkdown` makes. No {@html} anywhere: text is always text.
  import type { FleetClient } from "../api";
  import type { Block, Inline } from "../markdown";
  import ArtifactImage from "./ArtifactImage.svelte";

  let {
    blocks,
    client,
    projectId,
  }: { blocks: Block[]; client: FleetClient; projectId: string } = $props();
</script>

{#snippet inline(list: Inline[])}
  {#each list as node, n (n)}
    {#if node.t === "text"}{node.text}
    {:else if node.t === "code"}<code>{node.text}</code>
    {:else if node.t === "strong"}<strong>{@render inline(node.children)}</strong>
    {:else if node.t === "em"}<em>{@render inline(node.children)}</em>
    {:else if node.t === "br"}<br />
    {:else if node.t === "link"}<a href={node.href} target="_blank" rel="noopener noreferrer nofollow">{@render inline(node.children)}</a>
    {:else if node.t === "image"}
      {#if node.src.kind === "data"}
        <img class="inline-img" src={node.src.url} alt={node.alt} />
      {:else}
        <ArtifactImage {client} {projectId} path={node.src.path} alt={node.alt} />
      {/if}
    {/if}
  {/each}
{/snippet}

{#snippet tree(list: Block[])}
  {#each list as block, n (n)}
    {#if block.t === "heading"}
      {#if block.level === 1}<h1 class="headline-small">{@render inline(block.children)}</h1>
      {:else if block.level === 2}<h2 class="title-large">{@render inline(block.children)}</h2>
      {:else}<h3 class="title-medium">{@render inline(block.children)}</h3>{/if}
    {:else if block.t === "paragraph"}
      <p class="body-large">{@render inline(block.children)}</p>
    {:else if block.t === "code"}
      <pre><code>{block.text}</code></pre>
    {:else if block.t === "quote"}
      <blockquote>{@render tree(block.children)}</blockquote>
    {:else if block.t === "list"}
      {#if block.ordered}
        <ol>{#each block.items as item, k (k)}<li>{@render tree(item)}</li>{/each}</ol>
      {:else}
        <ul>{#each block.items as item, k (k)}<li>{@render tree(item)}</li>{/each}</ul>
      {/if}
    {:else if block.t === "table"}
      <div class="scroll">
        <table>
          <thead><tr>{#each block.head as cell, k (k)}<th>{@render inline(cell)}</th>{/each}</tr></thead>
          <tbody>
            {#each block.rows as row, r (r)}
              <tr>{#each row as cell, k (k)}<td>{@render inline(cell)}</td>{/each}</tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else if block.t === "rule"}
      <hr />
    {/if}
  {/each}
{/snippet}

<div class="md">{@render tree(blocks)}</div>

<style>
  .md {
    max-width: 48rem;
    overflow-wrap: anywhere;
    min-width: 0;
  }
  .md :global(h1), .md :global(h2), .md :global(h3) { margin: 1.25rem 0 0.5rem; }
  .md :global(p) { margin: 0.5rem 0; }
  .md :global(ul), .md :global(ol) { margin: 0.5rem 0; padding-left: 1.5rem; }
  .md :global(li > p) { margin: 0.1rem 0; }
  .md :global(a) { color: var(--md-sys-color-primary); }
  .md :global(code) {
    font-family: var(--md-ref-typeface-mono);
    font-size: 0.9em;
    background: var(--md-sys-color-surface-container-high);
    padding: 0.1em 0.35em;
    border-radius: var(--md-sys-shape-corner-extra-small);
  }
  .md :global(pre) {
    background: var(--md-sys-color-surface-container-high);
    padding: 0.75rem 1rem;
    border-radius: var(--md-sys-shape-corner-small);
    overflow-x: auto;
  }
  .md :global(pre code) { background: none; padding: 0; }
  .md :global(blockquote) {
    margin: 0.75rem 0;
    padding-left: 1rem;
    border-left: 3px solid var(--md-sys-color-outline-variant);
    color: var(--md-sys-color-on-surface-variant);
  }
  .md :global(hr) { border: 0; border-top: 1px solid var(--md-sys-color-outline-variant); margin: 1.25rem 0; }
  .scroll { overflow-x: auto; margin: 0.75rem 0; }
  table { border-collapse: collapse; min-width: 100%; }
  th, td {
    border: 1px solid var(--md-sys-color-outline-variant);
    padding: 0.4rem 0.7rem;
    text-align: left;
    vertical-align: top;
  }
  th { background: var(--md-sys-color-surface-container); }
  .inline-img { max-width: 100%; height: auto; }
</style>
