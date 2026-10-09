/** Minimal class-name join: truthy parts only, so the component layer stays dependency-free. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
