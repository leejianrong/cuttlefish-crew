<script lang="ts">
  import type { ThemePreference } from "../theme";
  import Icon from "./Icon.svelte";

  let {
    theme,
    onThemeChange,
    onOpenSprites,
    onDisconnect,
  }: {
    theme: ThemePreference;
    onThemeChange: (next: ThemePreference) => void;
    onOpenSprites: () => void;
    onDisconnect: () => void;
  } = $props();

  const THEMES: { id: ThemePreference; label: string }[] = [
    { id: "system", label: "Match system" },
    { id: "light", label: "Light" },
    { id: "dark", label: "Dark" },
  ];

  let open = $state(false);
  let root: HTMLDivElement | undefined = $state();
  let trigger: HTMLButtonElement | undefined = $state();

  function items(): HTMLButtonElement[] {
    return Array.from(root?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]') ?? []);
  }

  function close(returnFocus: boolean) {
    open = false;
    if (returnFocus) trigger?.focus();
  }

  function toggle() {
    open = !open;
    // The menu is in the DOM only while open: focus its first item once it is.
    if (open) queueMicrotask(() => items()[0]?.focus());
  }

  function pick(action: () => void) {
    close(true);
    action();
  }

  function onKeydown(event: KeyboardEvent) {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return;
    }
    const all = items();
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (event.key === "ArrowDown") next = at < all.length - 1 ? at + 1 : 0;
    else if (event.key === "ArrowUp") next = at > 0 ? at - 1 : all.length - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = all.length - 1;
    else if (event.key === "Tab") {
      close(false);
      return;
    }
    if (next >= 0) {
      event.preventDefault();
      all[next]?.focus();
    }
  }

  // A press anywhere outside the menu closes it, without stealing focus from what was pressed.
  $effect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (root && !root.contains(event.target as Node)) close(false);
    };
    window.addEventListener("pointerdown", outside);
    return () => window.removeEventListener("pointerdown", outside);
  });
</script>

<div class="account" bind:this={root} onkeydown={onKeydown} role="presentation">
  <button
    bind:this={trigger}
    class="avatar"
    aria-haspopup="menu"
    aria-expanded={open}
    aria-label="Account menu"
    title="Account menu"
    onclick={toggle}
  >
    <Icon name="user" size={20} />
  </button>

  {#if open}
    <div class="menu" role="menu" aria-label="Account">
      <p class="group" aria-hidden="true">Theme</p>
      {#each THEMES as option (option.id)}
        <button
          class="item"
          role="menuitemradio"
          aria-checked={theme === option.id}
          tabindex="-1"
          onclick={() => pick(() => onThemeChange(option.id))}
        >
          <span class="tick">{#if theme === option.id}<Icon name="check" size={18} />{/if}</span>
          {option.label}
        </button>
      {/each}
      <hr />
      <button class="item" role="menuitem" tabindex="-1" onclick={() => pick(onOpenSprites)}>
        <span class="tick"><Icon name="sprite" size={18} /></span>
        Sprite gallery
      </button>
      <button class="item" role="menuitem" tabindex="-1" onclick={() => pick(onDisconnect)}>
        <span class="tick"><Icon name="logout" size={18} /></span>
        Disconnect
      </button>
    </div>
  {/if}
</div>

<style>
  .account {
    position: relative;
  }

  .avatar {
    all: unset;
    box-sizing: border-box;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    cursor: pointer;
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .avatar:focus-visible,
  .item:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 30;
    min-width: 220px;
    max-width: calc(100vw - 32px);
    padding: 8px 0;
    border-radius: var(--md-sys-shape-corner-medium);
    background: var(--md-sys-color-surface-container);
    box-shadow: var(--md-sys-elevation-level2);
    border: 1px solid var(--md-sys-color-outline-variant);
  }

  .group {
    margin: 0;
    padding: 4px 16px;
    color: var(--md-sys-color-on-surface-variant);
    font: 500 0.75rem/1rem var(--md-ref-typeface-plain);
    letter-spacing: 0.03125rem;
  }

  hr {
    border: 0;
    border-top: 1px solid var(--md-sys-color-outline-variant);
    margin: 8px 0;
  }

  .item {
    all: unset;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    min-height: 48px;
    padding: 0 16px;
    cursor: pointer;
    color: var(--md-sys-color-on-surface);
    font: 400 0.875rem/1.25rem var(--md-ref-typeface-plain);
  }

  .item:hover {
    background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent);
  }

  .tick {
    width: 18px;
    display: grid;
    place-items: center;
    color: var(--md-sys-color-on-surface-variant);
  }
</style>
