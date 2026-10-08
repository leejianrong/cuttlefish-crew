// Polling hands back fresh objects every few seconds. Assigning them as-is makes every component
// that reads them re-render, and a running project's page has a lot of them, so a value is only
// taken when it differs from what is held (a structural comparison, not identity).

/** Whether two JSON-shaped values hold the same data. */
export function sameJson(a: unknown, b: unknown): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

/** `next` when it differs from `current`, else `current` itself, so a `$state` assigned the result
 * keeps its identity (and Svelte its renders) when nothing changed. */
export function keepIfSame<T>(current: T, next: T): T {
  return sameJson(current, next) ? current : next;
}
