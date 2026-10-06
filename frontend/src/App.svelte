<script lang="ts">
  import { FleetClient, type NeedsYouRequest } from "./lib/api";
  import AddProject from "./lib/components/AddProject.svelte";
  import ConnectScreen from "./lib/components/ConnectScreen.svelte";
  import RolesLibrary from "./lib/components/RolesLibrary.svelte";
  import NeedsYouScreen from "./lib/components/NeedsYouScreen.svelte";
  import Portfolio from "./lib/components/Portfolio.svelte";
  import ProjectDetail from "./lib/components/ProjectDetail.svelte";
  import Shell from "./lib/components/Shell.svelte";
  import SpriteGallery from "./lib/components/SpriteGallery.svelte";
  import { NAV_ITEMS } from "./lib/nav";
  import { clearConnection, loadConnection, saveConnection } from "./lib/session";
  import { applyTheme, loadTheme, type ThemePreference } from "./lib/theme";

  const remembered = loadConnection();
  let client = $state<FleetClient | null>(
    remembered ? new FleetClient(remembered.baseUrl, remembered.token) : null,
  );
  let openProjectId = $state<string | null>(null);
  let showGallery = $state(false);
  let showRoles = $state(false);
  let adding = $state(false);
  let showNeedsYou = $state(false);
  // Every request waiting on a person, polled with the same 2.5 s beat as the rest of the app.
  let waiting = $state<NeedsYouRequest[]>([]);
  let waitingFetchedAt = $state(Date.now());
  let waitingUnreachable = $state(false);
  const storedTheme = loadTheme();
  applyTheme(storedTheme);
  let theme = $state<ThemePreference>(storedTheme);

  const active = $derived(
    showGallery ? "sprites" : showRoles ? "roles" : showNeedsYou ? "needs-you" : "projects",
  );
  const navItems = $derived(
    NAV_ITEMS.map((item) =>
      item.id === "needs-you" ? { ...item, badge: waiting.length } : item,
    ),
  );

  async function refreshWaiting() {
    if (!client) return;
    try {
      waiting = (await client.listRequests()).requests;
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
    openProjectId = null;
    showGallery = false;
    showRoles = false;
    showNeedsYou = false;
    adding = false;
    waiting = [];
    clearConnection();
  }

  function navigate(id: string) {
    showGallery = id === "sprites";
    showRoles = id === "roles";
    showNeedsYou = id === "needs-you";
    // Choosing Projects from inside a project or the add screen goes back to the list.
    if (id === "projects") {
      openProjectId = null;
      adding = false;
    }
  }
</script>

{#if !client}
  {#if showGallery}
    <SpriteGallery onBack={() => (showGallery = false)} />
  {:else}
    <ConnectScreen {onConnected} onShowGallery={() => (showGallery = true)} />
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
      <SpriteGallery onBack={() => (showGallery = false)} />
    {:else if showRoles}
      <RolesLibrary {client} />
    {:else if showNeedsYou}
      <NeedsYouScreen
        {client}
        requests={waiting}
        fetchedAt={waitingFetchedAt}
        unreachable={waitingUnreachable}
        onAnswered={refreshWaiting}
        onOpenProject={(id) => {
          showNeedsYou = false;
          openProjectId = id;
        }}
      />
    {:else if adding}
      <AddProject
        {client}
        onCancel={() => (adding = false)}
        onRegistered={(project) => {
          adding = false;
          openProjectId = project.id;
        }}
      />
    {:else if openProjectId}
      <ProjectDetail {client} projectId={openProjectId} onBack={() => (openProjectId = null)} />
    {:else}
      <Portfolio
        {client}
        onOpenProject={(id) => (openProjectId = id)}
        onAddProject={() => (adding = true)}
      />
    {/if}
  </Shell>
{/if}
