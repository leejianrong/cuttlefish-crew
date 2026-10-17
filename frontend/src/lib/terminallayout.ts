// How tall the terminal may be on a phone so the key row sits right under it and above the bottom
// bar (CUT-98). Pure, so it is unit-tested; the component feeds it measurements.

export interface TerminalSpace {
  /** The visible height: `visualViewport.height`, which an on-screen keyboard shrinks. */
  visible: number;
  /** The full layout height (`innerHeight`), to tell whether a keyboard is open. */
  layout: number;
  /** Where the terminal panel starts, measured from the top of the page. */
  top: number;
  /** The bottom bar's height, or 0 when it is not shown (a wide screen). */
  nav: number;
  /** The status line above the screen plus the key row below it. */
  chrome: number;
}

export const MIN_SCREEN_PX = 120;

/** A keyboard is open when the visible height is well under the layout height. */
export function keyboardOpen(visible: number, layout: number): boolean {
  return layout - visible > 120;
}

/** Height for the terminal screen. With a keyboard open the page is scrolled so the panel starts
 * at the top, and the bottom bar is behind the keyboard, so neither is subtracted. */
export function screenHeight(space: TerminalSpace): number {
  const open = keyboardOpen(space.visible, space.layout);
  const used = open ? space.chrome : space.top + space.nav + space.chrome;
  return Math.max(MIN_SCREEN_PX, Math.floor(space.visible - used));
}
