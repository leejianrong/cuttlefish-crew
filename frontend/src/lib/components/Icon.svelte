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
