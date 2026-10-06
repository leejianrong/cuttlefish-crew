// Arrow-key behaviour for a group of `role="radio"` buttons: the arrows move the choice and
// the focus, and (with `tabindex` set from `aria-checked` in the markup) Tab enters and
// leaves the group as one stop, which is how a native radio group behaves.

export function radiogroup(node: HTMLElement): { destroy: () => void } {
  function onKeydown(event: KeyboardEvent) {
    const forward = event.key === "ArrowRight" || event.key === "ArrowDown";
    const backward = event.key === "ArrowLeft" || event.key === "ArrowUp";
    if (!forward && !backward) return;
    const radios = Array.from(node.querySelectorAll<HTMLElement>('[role="radio"]'));
    const current = radios.indexOf(document.activeElement as HTMLElement);
    if (current === -1) return;
    event.preventDefault();
    const next = radios[(current + (forward ? 1 : -1) + radios.length) % radios.length];
    next.click();
    next.focus();
  }
  node.addEventListener("keydown", onKeydown);
  return { destroy: () => node.removeEventListener("keydown", onKeydown) };
}
