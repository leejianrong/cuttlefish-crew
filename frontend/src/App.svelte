<script lang="ts">
  import { untrack } from "svelte";
  import { FleetApiError, FleetClient, type NeedsYouRequest } from "./lib/api";
  import AddProject from "./lib/components/AddProject.svelte";
  import ConnectScreen from "./lib/components/ConnectScreen.svelte";
  import DevicesScreen from "./lib/components/DevicesScreen.svelte";
  import PairScreen from "./lib/components/PairScreen.svelte";
  import RolesLibrary from "./lib/components/RolesLibrary.svelte";
  import SharedSecretsScreen from "./lib/components/SharedSecretsScreen.svelte";
  import NeedsYouScreen from "./lib/components/NeedsYouScreen.svelte";
  import Portfolio from "./lib/components/Portfolio.svelte";
  import ProjectDetail from "./lib/components/ProjectDetail.svelte";
  import SignInAgain from "./lib/components/SignInAgain.svelte";
  import Palette from "./lib/components/Palette.svelte";
  import Icon from "./lib/components/Icon.svelte";
  import NotificationBell from "./lib/components/NotificationBell.svelte";
  import Shell from "./lib/components/Shell.svelte";
  import SpriteGallery from "./lib/components/SpriteGallery.svelte";
  import type { DeviceIdentity, PairingLink, RelayTransport } from "./lib/relay";
  import { NAV_ITEMS } from "./lib/nav";
  import {
    addEvents,
    createTracker,
    loadOn,
    saveOn,
    show,
    unreadCount,
    type NotifyEvent,
  } from "./lib/notify";
  import { isOpenKey, type PaletteProject } from "./lib/palette";
  import { keepIfSame } from "./lib/same";
  import { HOME, parseHash, routeHash, sameRoute, type ProjectTab, type Route } from "./lib/route";
  import { forgetRelay, makeRelayTransport, startRelay } from "./lib/relayconnect";
  import { clearConnection, loadConnection, saveConnection } from "./lib/session";
  import { applyTheme, loadTheme, type ThemePreference } from "./lib/theme";
  import { tabTitle } from "./lib/title";

  // A password-mode daemon hands out a fresh session token as the old one ages (ADR-0038). Every
  // client keeps it in this browser, so a reload carries on signed in.
  function makeClient(baseUrl: string, token: string): FleetClient {
    return new FleetClient(baseUrl, token, (fresh) => saveConnection({ baseUrl, token: fresh }));
  }

  // What this page is: opened from a pairing link (`#pair=...`, ADR-0039), a browser that paired before
  // (then every request and the terminal go through the relay as that device), a remembered direct
  // connection, or none yet. Finding a stored pairing is asynchronous, so the first paint waits for it.
  let booting = $state(true);
  let pairing = $state<PairingLink | null>(null);
  let notice = $state("");
  let relay: RelayTransport | null = null;
  let relayDevice: DeviceIdentity | null = null;
  // Set while this device removes itself, so the daemon's "revoked" notice is not shown as news.
  let removingSelf = false;

  function useRelay(identity: DeviceIdentity) {
    relayDevice = identity;
    relay = makeRelayTransport(identity, () => {
      if (removingSelf) return;
      // The owner removed this device on the PC: forget the pairing and say why.
      void forgetRelay();
      relay = null;
      client = null;
      notice = "This device was removed on the PC. Pair it again to use the relay.";
    });
    client = new FleetClient(`relay://${identity.room.slice(0, 8)}`, "relay", undefined, relay);
  }

  const remembered = loadConnection();
  let client = $state<FleetClient | null>(null);
  startRelay().then((start) => {
    if (start.kind === "pair") pairing = start.link;
    else if (start.kind === "error") notice = start.message;
    else if (start.kind === "paired") useRelay(start.identity);
    else if (remembered) client = makeClient(remembered.baseUrl, remembered.token);
    booting = false;
  });
  // The daemon stopped accepting the saved sign-in (a restart, an expired session): ask again here
  // instead of leaving the page on "Lost connection" (CUT-112).
  let signedOut = $state(false);
  // Where we are lives in the URL hash, so a reload, Back and a pasted link land on the same screen.
  let route = $state<Route>(parseHash(window.location.hash));
  const openProjectId = $derived(route.view === "project" ? route.id : null);
  const projectTab = $derived<ProjectTab>(route.view === "project" ? route.tab : "overview");
  const showGallery = $derived(route.view === "sprites");
  const showRoles = $derived(route.view === "roles");
  const showSecrets = $derived(route.view === "secrets");
  const showDevices = $derived(route.view === "devices");
  const showNeedsYou = $derived(route.view === "needs-you");
  const adding = $derived(route.view === "add");

  function go(next: Route) {
    if (sameRoute(next, route)) return;
    route = next;
    history.pushState(null, "", routeHash(next));
  }

  // A hash we do not recognise (a typo, a stale link) is replaced by the one for the screen it
  // landed on, so a reload or a copied link does not keep the junk.
  function canonicalise() {
    const wanted = routeHash(route);
    if (window.location.hash !== "" && window.location.hash !== wanted) {
      history.replaceState(null, "", wanted);
    }
  }
  canonicalise();

  // Back, Forward and a hand-edited hash re-read the URL.
  $effect(() => {
    const reread = () => {
      const next = parseHash(window.location.hash);
      if (!sameRoute(next, route)) route = next;
      canonicalise();
    };
    window.addEventListener("popstate", reread);
    window.addEventListener("hashchange", reread);
    return () => {
      window.removeEventListener("popstate", reread);
      window.removeEventListener("hashchange", reread);
    };
  });
  // Every request waiting on a person, polled with the same 2.5 s beat as the rest of the app.
  let waiting = $state<NeedsYouRequest[]>([]);
  let waitingFetchedAt = $state(Date.now());
  let waitingUnreachable = $state(false);
  // CUT-290: what is new since the last poll, the bell's unread count and the per-browser mute.
  const tracker = createTracker();
  let events = $state<NotifyEvent[]>([]);
  let readAt = $state(Date.now());
  let noticesOn = $state(loadOn());
  // CUT-294: the palette filters the list the finished-project beat already reads, so typing asks nothing.
  let paletteProjects = $state<PaletteProject[]>([]);
  let paletteOpen = $state(false);
  $effect(() => {
    if (!client) return;
    const onKey = (event: KeyboardEvent) => {
      if (!isOpenKey(event)) return;
      event.preventDefault();
      paletteOpen = !paletteOpen;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const unread = $derived(unreadCount(events, readAt));
  function noticed(fresh: NotifyEvent[]) {
    if (fresh.length === 0) return;
    events = addEvents(events, fresh);
    show(fresh, noticesOn);
  }
  const storedTheme = loadTheme();
  applyTheme(storedTheme);
  let theme = $state<ThemePreference>(storedTheme);

  const active = $derived(
    showGallery
      ? "sprites"
      : showDevices
        ? "devices"
        : showRoles
        ? "roles"
        : showSecrets
          ? "secrets"
          : showNeedsYou
            ? "needs-you"
            : "projects",
  );
  const navItems = $derived(
    NAV_ITEMS.map((item) =>
      item.id === "needs-you" ? { ...item, badge: waiting.length } : item,
    ),
  );

  async function refreshWaiting() {
    if (!client) return;
    try {
      const requests = (await client.listRequests()).requests;
      waiting = keepIfSame(waiting, requests);
      noticed(tracker.requests(requests));
      waitingFetchedAt = Date.now();
      waitingUnreachable = false;
    } catch (error) {
      if (error instanceof FleetApiError && error.status === 401) signedOut = true;
      else waitingUnreachable = true;
    }
  }

  // The count in the tab title is what a phone shows in its tab switcher.
  $effect(() => {
    document.title = client ? tabTitle(waiting.length) : tabTitle(0);
  });

  $effect(() => {
    if (!client) return;
    refreshWaiting();
    const interval = setInterval(refreshWaiting, 2500);
    return () => clearInterval(interval);
  });

  // Finished projects come from the project list, which only this slow beat reads here.
  $effect(() => {
    if (!client) return;
    const c = client;
    const check = async () => {
      try {
        const list = (await c.listProjects()).projects;
        paletteProjects = keepIfSame(
          paletteProjects,
          list.map((p) => ({ id: p.id, name: p.name, tags: p.tags })),
        );
        const by: Record<string, number> = {};
        for (const r of untrack(() => waiting)) by[r.project_id] = (by[r.project_id] ?? 0) + 1;
        noticed(tracker.projects(list, by));
      } catch {
        // The next beat tries again; the Projects screen says when the daemon is unreachable.
      }
    };
    check();
    const interval = setInterval(check, 10_000);
    return () => clearInterval(interval);
  });

  function onConnected(newClient: FleetClient) {
    client = makeClient(newClient.baseUrl, newClient.token);
    signedOut = false;
    saveConnection({ baseUrl: newClient.baseUrl, token: newClient.token });
  }

  async function disconnect() {
    if (relay && client) {
      // A paired device that disconnects removes itself, so no orphan is left on the PC.
      removingSelf = true;
      await client.revokeDevice(relayDevice?.id ?? "").catch(() => undefined);
      relay.close();
      relay = null;
      relayDevice = null;
      removingSelf = false;
      await forgetRelay();
    }
    client = null;
    signedOut = false;
    go(HOME);
    waiting = [];
    clearConnection();
  }

  function navigate(id: string) {
    // Choosing Projects from inside a project or the add screen goes back to the list.
    go(
      id === "sprites"
        ? { view: "sprites" }
        : id === "devices"
          ? { view: "devices" }
        : id === "roles"
          ? { view: "roles" }
          : id === "secrets"
            ? { view: "secrets" }
            : id === "needs-you"
              ? { view: "needs-you" }
              : HOME,
    );
  }
</script>

{#if booting}
  <p class="booting" role="status">Loading…</p>
{:else if pairing}
  <PairScreen
    link={pairing}
    onPaired={(identity) => {
      pairing = null;
      useRelay(identity);
    }}
    onCancel={() => (pairing = null)}
  />
{:else if !client}
  {#if showGallery}
    <SpriteGallery onBack={() => go(HOME)} />
  {:else}
    <ConnectScreen {onConnected} {notice} onShowGallery={() => go({ view: "sprites" })} />
  {/if}
{:else}
  <Shell
    items={navItems}
    {active}
    onNavigate={navigate}
    onDisconnect={disconnect}
    {theme}
    onThemeChange={(next) => (theme = next)}
  >
    {#snippet topActions()}
      <button
        class="jump"
        aria-label="Jump to"
        aria-keyshortcuts="Control+K Meta+K"
        title="Jump to (Ctrl-K)"
        onclick={() => (paletteOpen = true)}
      >
        <Icon name="search" size={22} />
      </button>
      <NotificationBell
        {events}
        {unread}
        on={noticesOn}
        onRead={() => (readAt = Date.now())}
        onToggle={(next) => {
          noticesOn = next;
          saveOn(next);
        }}
        onOpenNeedsYou={() => navigate("needs-you")}
      />
    {/snippet}
    {#if showGallery}
      <SpriteGallery onBack={() => go(HOME)} />
    {:else if showRoles}
      <RolesLibrary {client} />
    {:else if showSecrets}
      <SharedSecretsScreen {client} />
    {:else if showDevices}
      <DevicesScreen {client} />
    {:else if showNeedsYou}
      <NeedsYouScreen
        {client}
        requests={waiting}
        fetchedAt={waitingFetchedAt}
        unreachable={waitingUnreachable}
        onAnswered={refreshWaiting}
        onOpenProject={(id) => go({ view: "project", id, tab: "overview" })}
      />
    {:else if adding}
      <AddProject
        {client}
        onCancel={() => go(HOME)}
        onRegistered={(project) => go({ view: "project", id: project.id, tab: "overview" })}
      />
    {:else if openProjectId}
      <!-- Keyed by project: switching by the URL alone must not carry one project's page state (a
           draft, a half-typed task) over to another. -->
      {#key openProjectId}
        <ProjectDetail
          {client}
          projectId={openProjectId}
          tab={projectTab}
          onTabChange={(tab) => go({ view: "project", id: openProjectId, tab })}
          onBack={() => go(HOME)}
        />
      {/key}
    {:else}
      <Portfolio
        {client}
        onOpenProject={(id) => go({ view: "project", id, tab: "overview" })}
        onAddProject={() => go({ view: "add" })}
      />
    {/if}
  </Shell>
  {#if paletteOpen}
    <Palette
      projects={paletteProjects}
      requests={waiting}
      current={route}
      onGo={go}
      onClose={() => (paletteOpen = false)}
    />
  {/if}
  {#if signedOut}
    <SignInAgain
      {client}
      onSignedIn={(next) => {
        client = makeClient(next.baseUrl, next.token);
        saveConnection({ baseUrl: next.baseUrl, token: next.token });
        signedOut = false;
        refreshWaiting();
      }}
      onDisconnect={disconnect}
    />
  {/if}
{/if}

<style>
  .jump {
    all: unset;
    box-sizing: border-box;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    cursor: pointer;
    color: var(--md-sys-color-on-surface-variant);
  }

  .jump:hover {
    background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent);
  }

  .jump:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .booting {
    min-height: 100vh;
    display: grid;
    place-items: center;
    color: var(--text-muted);
    margin: 0;
  }
</style>
