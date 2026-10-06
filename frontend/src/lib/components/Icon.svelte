<script lang="ts">
  // Stroke icons on a 24px grid, drawn for this dashboard. One component so a screen
  // never needs inline SVG, and so an icon-only button can pair it with an aria-label.
  const paths: Record<string, string> = {
    folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    sprite: "M6 20v-6a6 6 0 0 1 12 0v6M9 11h.01M15 11h.01M9 20v2M15 20v2",
    sun: "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
    moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
    contrast: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 3v18",
    logout: "M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M16 8l4 4-4 4M20 12H9",
    back: "M19 12H5M11 6l-6 6 6 6",
    plus: "M12 5v14M5 12h14",
    "chevron-right": "m9 6 6 6-6 6",
    "chevron-down": "m6 9 6 6 6-6",
    "arrow-up": "M12 19V6M6 12l6-6 6 6",
    check: "m5 12 5 5 9-10",
    git: "M6 8v8M18 11c0 4-6 3-12 5M6 6m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0M6 18m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0M18 9m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0",
    users: "M9 8m-3 0a3 3 0 1 0 6 0 3 3 0 1 0-6 0M3 19c0-3 3-5 6-5s6 2 6 5M17 9m-2.2 0a2.2 2.2 0 1 0 4.4 0 2.2 2.2 0 1 0-4.4 0M17 14c2.5 0 4 1.7 4 4",
    shield: "M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z",
    bolt: "M13 3 5 14h6l-1 7 8-11h-6z",
    chat: "M4 5h16v11H9l-5 4z",
    code: "m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14",
  };

  let { name, size = 24 }: { name: keyof typeof paths | string; size?: number } = $props();
</script>

<svg
  class="icon"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  <path d={paths[name] ?? ""} />
</svg>

<style>
  .icon {
    flex: none;
  }
</style>
