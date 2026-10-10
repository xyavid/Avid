/**
 * Running indicator at the end of the live turn: the same three-dot cycle the waiting bubble
 * uses, sitting in the slot the copy / branch row takes once the run is over, so the two never
 * show at once and nothing moves when they swap.
 */

export function RunningRow() {
  return (
    <div
      role="status"
      aria-label="运行中"
      data-testid="run-indicator"
      className="mt-a4 flex items-center gap-a2"
    >
      {[0, 1, 2].map((step) => (
        <span
          key={step}
          className="h-[4px] w-[4px] rounded-full bg-accent"
          style={{
            animation: 'hana-cycling-dots 1.2s var(--ease-standard) infinite',
            animationDelay: `${step * 0.2}s`,
          }}
        />
      ))}
    </div>
  )
}
