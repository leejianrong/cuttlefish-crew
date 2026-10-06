<script lang="ts">
  import type { Snippet } from "svelte";
  import { applyTheme, nextTheme, saveTheme, type ThemePreference } from "../theme";
  import type { NavItem } from "../nav";
  import { badgeText } from "../requests";
  import Icon from "./Icon.svelte";

  let {
    items,
    active,
    onNavigate,
    onDisconnect,
    theme,
    onThemeChange,
    children,
  }: {
    items: NavItem[];
    active: string;
    onNavigate: (id: string) => void;
    onDisconnect: () => void;
    theme: ThemePreference;
    onThemeChange: (next: ThemePreference) => void;
    children: Snippet;
  } = $props();

  const themeIcon = $derived(theme === "light" ? "sun" : theme === "dark" ? "moon" : "contrast");
  const themeLabel = $derived(
    theme === "system" ? "Theme: match system" : theme === "light" ? "Theme: light" : "Theme: dark",
  );

  function cycleTheme() {
    const next = nextTheme(theme);
    applyTheme(next);
    saveTheme(next);
    onThemeChange(next);
  }
</script>

<div class="shell">
  <nav class="rail" aria-label="Main">
    <svg class="mark" width="40" height="40" viewBox="0 0 40 40" role="img" aria-label="cuttlefish-crew">
      <rect width="40" height="40" rx="12" fill="var(--md-sys-color-primary)" />
      <circle cx="14" cy="17" r="3.2" fill="var(--md-sys-color-attention-container)" />
      <circle cx="22" cy="14" r="2.2" fill="var(--md-sys-color-primary-container)" />
      <circle cx="26" cy="22" r="3.6" fill="var(--md-sys-color-attention-container)" />
      <circle cx="16" cy="26" r="1.8" fill="var(--md-sys-color-primary-container)" />
    </svg>

    <div class="destinations">
      {#each items as item (item.id)}
        <button
          class="destination"
          class:active={item.id === active}
          aria-current={item.id === active ? "page" : undefined}
          aria-label={item.badge ? `${item.label}, ${item.badge} waiting` : undefined}
          onclick={() => onNavigate(item.id)}
        >
          <span class="pill"><Icon name={item.icon} /></span>
          {#if item.badge}<span class="badge" aria-hidden="true">{badgeText(item.badge)}</span>{/if}
          <span class="label">{item.label}</span>
        </button>
      {/each}
    </div>

    <div class="footer">
      <button class="icon-button" onclick={cycleTheme} aria-label={themeLabel} title={themeLabel}>
        <Icon name={themeIcon} />
      </button>
      <button class="icon-button" onclick={onDisconnect} aria-label="Disconnect" title="Disconnect">
        <Icon name="logout" />
      </button>
    </div>
  </nav>

  <div class="content">
    {@render children()}
  </div>
</div>

<style>
  .shell {
    display: flex;
    min-height: 100vh;
  }

  .rail {
    position: sticky;
    top: 0;
    align-self: flex-start;
    height: 100vh;
    width: 88px;
    flex: none;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 20px 0;
    background: var(--md-sys-color-surface);
    border-right: 1px solid var(--md-sys-color-outline-variant);
  }

  .mark {
    margin-bottom: 8px;
  }

  .destinations {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    flex: 1;
  }

  .destination {
    all: unset;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    width: 80px;
    cursor: pointer;
    color: var(--md-sys-color-on-surface-variant);
    font: 500 0.75rem/1rem var(--md-ref-typeface-plain);
    letter-spacing: 0.03125rem;
  }

  .destination {
    position: relative;
  }

  /* Coral means "needs you" and only that (design README): the count of waiting requests. */
  .badge {
    position: absolute;
    top: -4px;
    left: calc(50% + 6px);
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    box-sizing: border-box;
    border-radius: 8px;
    display: grid;
    place-items: center;
    background: var(--md-sys-color-attention);
    color: var(--md-sys-color-on-attention);
    font: 700 0.6875rem/1 var(--md-ref-typeface-plain);
  }

  .pill {
    position: relative;
    overflow: hidden;
    width: 56px;
    height: 32px;
    border-radius: 16px;
    display: grid;
    place-items: center;
    transition: background var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard);
  }

  .pill::before {
    content: "";
    position: absolute;
    inset: 0;
    background: currentColor;
    opacity: 0;
    transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard);
  }

  .destination:hover .pill::before {
    opacity: 0.08;
  }

  .destination:focus-visible .pill {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .destination.active {
    color: var(--md-sys-color-on-surface);
    font-weight: 700;
  }

  .destination.active .pill {
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .footer {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .icon-button {
    all: unset;
    box-sizing: border-box;
    position: relative;
    overflow: hidden;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    cursor: pointer;
    color: var(--md-sys-color-on-surface-variant);
  }

  .icon-button::before {
    content: "";
    position: absolute;
    inset: 0;
    background: currentColor;
    opacity: 0;
    transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard);
  }

  .icon-button:hover::before {
    opacity: 0.08;
  }

  .icon-button:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .content {
    flex: 1;
    min-width: 0;
  }

  /* On a phone the rail becomes a bottom bar (M3 navigation bar). */
  @media (max-width: 640px) {
    .shell {
      flex-direction: column-reverse;
    }

    .rail {
      position: sticky;
      bottom: 0;
      top: auto;
      height: auto;
      width: 100%;
      flex-direction: row;
      justify-content: space-around;
      gap: 8px;
      padding: 8px 12px;
      border-right: 0;
      border-top: 1px solid var(--md-sys-color-outline-variant);
      z-index: 10;
    }

    .mark {
      display: none;
    }

    .destinations {
      flex-direction: row;
      justify-content: space-around;
      flex: 1;
    }

    .destination {
      width: auto;
      flex: 1;
    }

    .footer {
      flex-direction: row;
    }
  }
</style>
