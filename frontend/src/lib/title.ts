// The browser tab's title carries the Needs you count, so a phone with the dashboard in another
// tab or the app switcher shows that something is waiting. Pure, so it is unit-tested.

export const BASE_TITLE = "cuttlefish-crew";

export function tabTitle(waiting: number): string {
  return waiting > 0 ? `(${waiting}) ${BASE_TITLE}` : BASE_TITLE;
}
