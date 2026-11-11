<script lang="ts">
  // The bell in the top bar (CUT-290): an unread count of what happened since the person last
  // looked, and the one place to turn desktop notices on or mute them on this browser.
  import { agoIso } from "../artifacts";
  import { askPermission, eventText, permission, reach, type NotifyEvent, type Permission } from "../notify";
  import { badgeText } from "../requests";
  import Icon from "./Icon.svelte";

  let {
    events,
    unread,
    on,
    onRead,
    onToggle,
    onOpenNeedsYou,
  }: {
    events: NotifyEvent[];
    unread: number;
    on: boolean;
    onRead: () => void;
    onToggle: (on: boolean) => void;
    onOpenNeedsYou: () => void;
  } = $props();

  let open = $state(false);
  let perm = $state<Permission>(permission());
  let root: HTMLDivElement | undefined = $state();

  function toggle() {
    open = !open;
    if (open) {
      perm = permission();
      onRead();
    }
  }

  async function turnOn() {
    // A click is the gesture the browser needs for its prompt.
    perm = await askPermission();
    onToggle(perm === "granted");
  }

  $effect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (root && !root.contains(event.target as Node)) {
        open = false;
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        open = false;
      }
    };
    window.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointerdown", outside);
      window.removeEventListener("keydown", escape);
    };
  });

  const nowS = $derived(Date.now() / 1000);
</script>

<div class="bell" bind:this={root}>
  <button
    class="trigger"
    aria-haspopup="dialog"
    aria-expanded={open}
    aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
    title="Notifications"
    onclick={toggle}
  >
    <Icon name="bell" size={22} />
    {#if unread > 0}<span class="count" aria-hidden="true">{badgeText(unread)}</span>{/if}
  </button>

  {#if open}
    <div class="panel" role="dialog" aria-label="Notifications">
      {#if events.length === 0}
        <p class="empty body-medium">Nothing new since you opened the dashboard.</p>
      {:else}
        <ul>
          {#each events as event (event.id)}
            <li class="body-medium">
              <span>{eventText(event.kind, 1)}</span>
              <span class="ago body-small">{agoIso(new Date(event.at).toISOString(), nowS)}</span>
            </li>
          {/each}
        </ul>
      {/if}
      <button
        class="btn btn-tonal"
        onclick={() => {
          open = false;
          onOpenNeedsYou();
        }}>Open Needs you</button
      >
      <hr />
      <p class="reach body-small" data-perm={perm}>{reach(perm, on)}</p>
      {#if perm === "default" || (perm === "granted" && !on)}
        <button class="btn btn-outlined" onclick={turnOn}>Turn on desktop notices</button>
      {:else if perm === "granted" && on}
        <button class="btn btn-outlined" onclick={() => onToggle(false)}>Mute on this browser</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .bell {
    position: relative;
  }

  .trigger {
    all: unset;
    box-sizing: border-box;
    position: relative;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    cursor: pointer;
    color: var(--md-sys-color-on-surface-variant);
  }

  .trigger:hover {
    background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent);
  }

  .trigger:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .count {
    position: absolute;
    top: 4px;
    right: 2px;
    min-width: 18px;
    height: 18px;
    padding: 0 4px;
    box-sizing: border-box;
    border-radius: 9px;
    background: var(--md-sys-color-tertiary);
    color: var(--md-sys-color-on-tertiary);
    font: 600 0.6875rem/18px var(--md-ref-typeface-plain);
    text-align: center;
  }

  .panel {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 30;
    width: 320px;
    max-width: calc(100vw - 24px);
    padding: 12px 16px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    border-radius: var(--md-sys-shape-corner-medium);
    background: var(--md-sys-color-surface-container);
    box-shadow: var(--md-sys-elevation-level2);
    border: 1px solid var(--md-sys-color-outline-variant);
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 40vh;
    overflow-y: auto;
  }

  li {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 0;
  }

  li + li {
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  .ago,
  .reach,
  .empty {
    color: var(--md-sys-color-on-surface-variant);
  }

  .empty,
  .reach {
    margin: 0;
  }

  hr {
    width: 100%;
    border: 0;
    border-top: 1px solid var(--md-sys-color-outline-variant);
    margin: 4px 0;
  }

  .reach[data-perm="denied"] {
    color: var(--md-sys-color-error);
  }
</style>
