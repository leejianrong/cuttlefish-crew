<script lang="ts">
  import { Terminal } from "@xterm/xterm";
  import { FitAddon } from "@xterm/addon-fit";
  import "@xterm/xterm/css/xterm.css";
  import type { FleetClient } from "../api";
  import type { SocketLike } from "../relay";
  import {
    KEY_ROW,
    authMessage,
    describeClose,
    resizeMessage,
    type CloseInfo,
  } from "../terminal";
  import { keyboardOpen, screenHeight } from "../terminallayout";
  import {
    TERMINAL_THEME_CHOICES,
    ansiNames,
    appIsDark,
    loadTerminalTheme,
    resolveTerminalMode,
    saveTerminalTheme,
    type TerminalMode,
    type TerminalThemePreference,
  } from "../terminaltheme";

  let {
    client,
    projectId,
    active,
  }: { client: FleetClient; projectId: string; active: boolean } = $props();

  let host: HTMLDivElement | undefined = $state();
  let status = $state<"idle" | "connecting" | "connected" | "closed">("idle");
  let closed = $state<CloseInfo | null>(null);
  let starting = $state(false);
  // On a phone the screen is sized to the space left between the header and the bottom bar, so
  // the key row sits right under it instead of beneath the bar (CUT-98). Null means CSS decides.
  let panel: HTMLElement | undefined = $state();
  let bar: HTMLElement | undefined = $state();
  let keys: HTMLElement | undefined = $state();
  let screenPx = $state<number | null>(null);
  // The terminal's own light, dark or follow-the-app choice (CUT-169); dark unless the person chose.
  let themePref = $state<TerminalThemePreference>(loadTerminalTheme());
  let appDark = $state(appIsDark());
  let mode = $derived<TerminalMode>(resolveTerminalMode(themePref, appDark));

  let term: Terminal | null = null;
  let fit: FitAddon | null = null;
  let socket: SocketLike | null = null;
  let observer: ResizeObserver | null = null;
  let connectedFor = "";
  const encoder = new TextEncoder();
  const ANSI_KEYS = [
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
  ] as const;

  function role(name: string, fallback: string): string {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function xtermTheme(which: TerminalMode) {
    const name = (suffix: string) => `--term-${which}-${suffix}`;
    return {
      background: role(name("bg"), "#1d2426"),
      foreground: role(name("fg"), "#e4efef"),
      cursor: role(name("cursor"), "#7fd6dc"),
      cursorAccent: role(name("bg"), "#1d2426"),
      selectionBackground: role(name("selection"), "#3a5a5d"),
      ...Object.fromEntries(
        ansiNames(which).map((n, i) => [ANSI_KEYS[i], role(n, "")] as const).filter(([, v]) => v),
      ),
    };
  }

  function chooseTheme(next: TerminalThemePreference) {
    themePref = next;
    saveTerminalTheme(next);
  }

  function ensureTerminal(): Terminal | null {
    if (term || !host) return term;
    term = new Terminal({
      cursorBlink: true,
      fontFamily: "'JetBrains Mono Variable', 'JetBrains Mono', ui-monospace, monospace",
      fontSize: 14,
      scrollback: 5000,
      theme: xtermTheme(mode),
    });
    fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    term.onData((data) => send(encoder.encode(data)));
    observer = new ResizeObserver(() => refit());
    observer.observe(host);
    return term;
  }

  function refit() {
    if (!term || !fit || !active) return;
    try {
      fit.fit();
    } catch {
      return; // hidden or zero-sized: the next resize fits it
    }
    if (socket?.readyState === WebSocket.OPEN) socket.send(resizeMessage(term.cols, term.rows));
  }

  function layout() {
    if (!active || !panel || !window.matchMedia("(max-width: 640px)").matches) {
      screenPx = null;
      return;
    }
    const visible = window.visualViewport?.height ?? window.innerHeight;
    const typing = keyboardOpen(visible, window.innerHeight);
    // The panel starts at the top of the page when nothing above it is scrolled away.
    const top = panel.getBoundingClientRect().top + window.scrollY;
    const nav = document.getElementById("shell-nav")?.offsetHeight ?? 0;
    const chrome = (bar?.offsetHeight ?? 0) + (keys?.offsetHeight ?? 0) + 24;
    screenPx = screenHeight({ visible, layout: window.innerHeight, top, nav, chrome });
    // With a keyboard up, scroll the header away so the screen and the keys stay in view.
    if (typing) panel.scrollIntoView({ block: "start" });
  }

  function send(data: Uint8Array<ArrayBuffer>) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(data);
  }

  function disconnect() {
    const current = socket;
    socket = null;
    if (current) {
      current.onclose = null;
      current.close();
    }
  }

  function connect() {
    const terminal = ensureTerminal();
    if (!terminal || !fit) return;
    disconnect();
    closed = null;
    status = "connecting";
    connectedFor = projectId;
    terminal.reset();
    try {
      fit.fit();
    } catch {
      /* fitted on the first resize */
    }
    const ws = client.openTerminal(projectId);
    ws.binaryType = "arraybuffer";
    socket = ws;
    ws.onopen = () => ws.send(authMessage(client.token, terminal.cols, terminal.rows));
    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        if ((JSON.parse(event.data) as { type?: string }).type === "ready") {
          status = "connected";
          terminal.focus();
        }
        return;
      }
      terminal.write(new Uint8Array(event.data as ArrayBuffer));
    };
    ws.onclose = (event) => {
      if (socket !== ws) return;
      socket = null;
      status = "closed";
      closed = describeClose(event.code, event.reason);
    };
  }

  async function startSession() {
    starting = true;
    try {
      await client.startAgentSession(projectId);
      connect();
    } catch (error) {
      closed = {
        kind: "no-session",
        text: error instanceof Error ? error.message : "Could not start the agent session.",
      };
    } finally {
      starting = false;
    }
  }

  function press(sequence: string) {
    send(encoder.encode(sequence));
    term?.focus();
  }

  // Connect when the tab is first shown, and again if it is shown for another project. A hidden
  // panel has no size to fit, so nothing is opened before then.
  $effect(() => {
    if (active && host && connectedFor !== projectId) connect();
    else if (active) queueMicrotask(refit);
  });

  // Paint the palette again when the choice changes, and follow the app's own theme.
  $effect(() => {
    // Read first: with no terminal yet, a short-circuit would leave this effect tracking nothing.
    const palette = xtermTheme(mode);
    if (term) term.options.theme = palette;
  });

  $effect(() => {
    const follow = () => (appDark = appIsDark());
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", follow);
    const watch = new MutationObserver(follow);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      media.removeEventListener("change", follow);
      watch.disconnect();
    };
  });

  // Size the screen when the tab is shown, and again when the window or the keyboard changes it.
  $effect(() => {
    if (!active) return;
    window.scrollTo({ top: 0 });
    layout();
    const again = () => layout();
    window.addEventListener("resize", again);
    window.visualViewport?.addEventListener("resize", again);
    return () => {
      window.removeEventListener("resize", again);
      window.visualViewport?.removeEventListener("resize", again);
    };
  });

  // After the sign-in dialog hands over a new client, a terminal that closed because its token was
  // refused, or because the daemon restarted under it, opens again by itself.
  // Read once on purpose: it is compared with the later value, not tracked.
  // svelte-ignore state_referenced_locally
  let lastClient = client;
  $effect(() => {
    if (client === lastClient) return;
    lastClient = client;
    if (active && (closed?.kind === "unauthorised" || closed?.kind === "lost")) connect();
  });

  $effect(() => {
    return () => {
      disconnect();
      observer?.disconnect();
      term?.dispose();
      term = null;
      fit = null;
    };
  });
</script>

<section class="terminal" aria-labelledby="terminal-heading" bind:this={panel}>
  <div class="bar" bind:this={bar}>
    <h2 id="terminal-heading" class="title-medium">Terminal</h2>
    <span class="state body-small muted" role="status">
      {#if status === "connected"}Connected to the agent's tmux session
      {:else if status === "connecting"}Connecting…
      {:else if status === "closed"}Not connected
      {/if}
    </span>
    <div class="seg" role="group" aria-label="Terminal theme">
      {#each TERMINAL_THEME_CHOICES as choice (choice.id)}
        <button
          type="button"
          class:on={themePref === choice.id}
          aria-pressed={themePref === choice.id}
          onclick={() => chooseTheme(choice.id)}
        >
          {choice.label}
        </button>
      {/each}
    </div>
  </div>

  {#if closed}
    <div class="notice" role="alert">
      <p class="body-medium">{closed.text}</p>
      {#if closed.kind === "no-session"}
        <button class="btn btn-filled" disabled={starting} onclick={startSession}>
          Start the agent session
        </button>
      {:else if closed.kind !== "unauthorised"}
        <button class="btn btn-tonal" onclick={connect}>Reconnect</button>
      {/if}
    </div>
  {/if}

  <div
    class="screen"
    data-mode={mode}
    bind:this={host}
    data-testid="terminal-screen"
    style:height={screenPx === null ? undefined : `${screenPx}px`}
  ></div>

  <div class="keys" bind:this={keys} role="toolbar" aria-label="Keys a phone keyboard lacks">
    {#each KEY_ROW as key (key.label)}
      <button type="button" class="key" aria-label={key.aria} onclick={() => press(key.send)}>
        {key.label}
      </button>
    {/each}
  </div>
</section>

<style>
  .terminal {
    max-width: 72rem;
    margin: 1rem 0;
  }

  .bar {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-bottom: 0.5rem;
  }

  .bar h2 {
    margin: 0;
  }

  .notice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
    padding: 0.75rem 1rem;
    margin-bottom: 0.5rem;
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: var(--md-sys-color-surface-container-high);
  }

  .notice p {
    margin: 0;
  }

  .screen {
    height: min(70vh, 40rem);
    min-height: 16rem;
    padding: 0.5rem;
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    border: 1px solid transparent;
    overflow: hidden;
  }

  .screen[data-mode="dark"] {
    background: var(--term-dark-bg);
    border-color: var(--term-dark-border);
  }

  .screen[data-mode="light"] {
    background: var(--term-light-bg);
    border-color: var(--term-light-border);
  }

  .seg {
    display: inline-flex;
    border: 1px solid var(--md-sys-color-outline);
    border-radius: 1.25rem;
    overflow: hidden;
  }

  .seg button {
    min-height: 2.75rem;
    padding: 0 1rem;
    border: 0;
    background: transparent;
    color: var(--md-sys-color-on-surface);
    font: inherit;
    font-size: 0.8125rem;
    cursor: pointer;
  }

  .seg button.on {
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
    font-weight: 600;
  }

  .seg button:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: -2px;
  }

  @media (max-width: 640px) {
    /* The screen is sized in script to what is left (see layout()); this is the fallback. */
    .screen {
      height: 52dvh;
      min-height: 7.5rem;
    }

    .terminal {
      margin: 0.25rem 0 0;
    }
  }

  .keys {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    margin-top: 0.5rem;
  }

  .key {
    min-width: 2.75rem;
    min-height: 2.75rem;
    padding: 0 0.75rem;
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-small, 8px);
    background: var(--md-sys-color-surface-container);
    color: var(--md-sys-color-on-surface);
    font: inherit;
    cursor: pointer;
  }

  .key:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }
</style>
