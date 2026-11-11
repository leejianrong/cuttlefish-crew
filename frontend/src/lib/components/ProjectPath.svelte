<script lang="ts">
  // The project's folder under its name (CUT-285). One line: the directory gives way (an ellipsis)
  // before the folder name does, so a phone sees where the project is without five lines of path.
  // Press it to see the whole path wrapped; the full path is also its title.
  import { splitPath } from "../folders";

  let { root }: { root: string } = $props();
  let open = $state(false);
  const parts = $derived(splitPath(root));
</script>

<button
  type="button"
  class="root mono"
  class:open
  title={root}
  aria-expanded={open}
  aria-label={`Folder ${root}`}
  onclick={() => (open = !open)}
>
  <span class="dir">{parts.dir}</span><span class="name">{parts.name}</span>
</button>

<style>
  .root {
    display: flex;
    max-width: 100%;
    margin: 0.3rem 0 1.25rem;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--text-faint);
    font-size: 0.8rem;
    text-align: left;
    cursor: pointer;
  }

  .root:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .dir {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .name {
    flex: none;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--md-sys-color-on-surface-variant);
  }

  .open {
    display: block;
    overflow-wrap: anywhere;
  }

  .open .dir,
  .open .name {
    white-space: normal;
  }
</style>
