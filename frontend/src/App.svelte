<script lang="ts">
  import { FleetClient } from "./lib/api";
  import ConnectScreen from "./lib/components/ConnectScreen.svelte";
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
  const storedTheme = loadTheme();
  applyTheme(storedTheme);
  let theme = $state<ThemePreference>(storedTheme);

  const active = $derived(showGallery ? "sprites" : "projects");

  function onConnected(newClient: FleetClient) {
    client = newClient;
    saveConnection({ baseUrl: newClient.baseUrl, token: newClient.token });
  }

  function disconnect() {
    client = null;
    openProjectId = null;
    showGallery = false;
    clearConnection();
  }

  function navigate(id: string) {
    showGallery = id === "sprites";
    // Choosing Projects from inside a project goes back to the list.
    if (id === "projects") openProjectId = null;
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
    items={NAV_ITEMS}
    {active}
    onNavigate={navigate}
    onDisconnect={disconnect}
    {theme}
    onThemeChange={(next) => (theme = next)}
  >
    {#if showGallery}
      <SpriteGallery onBack={() => (showGallery = false)} />
    {:else if openProjectId}
      <ProjectDetail {client} projectId={openProjectId} onBack={() => (openProjectId = null)} />
    {:else}
      <Portfolio {client} onOpenProject={(id) => (openProjectId = id)} />
    {/if}
  </Shell>
{/if}
