/**
 * Avid mark (lotus): a self-colored illustration shipped as `<img>` from
 * `src/assets/avid-mark.svg`, keeping its ~47 KB / 431 paths out of the first-screen JS; it
 * never follows the theme or `currentColor`, and sits on a transparent background.
 * Two copies must stay identical — that asset and `src/assets/favicon.svg`, which adds a
 * paper plate — checked path by path in `Mark.test.tsx`.
 */

import markUrl from '../assets/avid-mark.svg'

export type AvidMarkProps = {
  /** Side length in px; the call site picks it. */
  size?: number
  className?: string
}

export function AvidMark({ size = 24, className }: AvidMarkProps) {
  return (
    <img
      src={markUrl}
      width={size}
      height={size}
      alt=""
      aria-hidden
      draggable={false}
      className={className}
    />
  )
}
