<script lang="ts">
  import { FleetClient, type NeedsYouRequest } from "./lib/api";
  import AddProject from "./lib/components/AddProject.svelte";
  import ConnectScreen from "./lib/components/ConnectScreen.svelte";
  import RolesLibrary from "./lib/components/RolesLibrary.svelte";
  import SharedSecretsScreen from "./lib/components/SharedSecretsScreen.svelte";
  import NeedsYouScreen from "./lib/components/NeedsYouScreen.svelte";
  import Portfolio from "./lib/components/Portfolio.svelte";
  import ProjectDetail from "./lib/components/ProjectDetail.svelte";
  import Shell from "./lib/components/Shell.svelte";
  import SpriteGallery from "./lib/components/SpriteGallery.svelte";
  import { NAV_ITEMS } from "./lib/nav";
  import { keepIfSame } from "./lib/same";
  import { HOME, parseHash, routeHash, sameRoute, type ProjectTab, type Route } from "./lib/route";
  import { clearConnection, loadConnection, saveConnection } from "./lib/session";
  import { applyTheme, loadTheme, type ThemePreference } from "./lib/theme";

  const remembered = loadConnection();
  let client = $state<FleetClient | null>(
    remembered ? new FleetClient(remembered.baseUrl, remembered.token) : null,
  );
  // Where we are lives in the URL hash, so a reload, Back and a pasted link land on the same screen.
  let route = $state<Route>(parseHash(window.location.hash));
  const openProjectId = $derived(route.view === "project" ? route.id : null);
  const projectTab = $derived<ProjectTab>(route.view === "project" ? route.tab : "overview");
  const showGallery = $derived(route.view === "sprites");
  const showRoles = $derived(route.view === "roles");
  const showSecrets = $derived(route.view === "secrets");
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
  const storedTheme = loadTheme();
  applyTheme(storedTheme);
  let theme = $state<ThemePreference>(storedTheme);

  const active = $derived(
    showGallery
      ? "sprites"
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
      waiting = keepIfSame(waiting, (await client.listRequests()).requests);
      waitingFetchedAt = Date.now();
      waitingUnreachable = false;
    } catch {
      waitingUnreachable = true;
    }
  }

  $effect(() => {
    if (!client) return;
    refreshWaiting();
    const interval = setInterval(refreshWaiting, 2500);
    return () => clearInterval(interval);
  });

  function onConnected(newClient: FleetClient) {
    client = newClient;
    saveConnection({ baseUrl: newClient.baseUrl, token: newClient.token });
  }

  function disconnect() {
    client = null;
    go(HOME);
    waiting = [];
    clearConnection();
  }

  function navigate(id: string) {
    // Choosing Projects from inside a project or the add screen goes back to the list.
    go(
      id === "sprites"
        ? { view: "sprites" }
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

{#if !client}
  {#if showGallery}
    <SpriteGallery onBack={() => go(HOME)} />
  {:else}
    <ConnectScreen {onConnected} onShowGallery={() => go({ view: "sprites" })} />
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
    {#if showGallery}
      <SpriteGallery onBack={() => go(HOME)} />
    {:else if showRoles}
      <RolesLibrary {client} />
    {:else if showSecrets}
      <SharedSecretsScreen {client} />
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
{/if}
