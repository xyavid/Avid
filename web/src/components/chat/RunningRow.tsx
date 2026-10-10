/**
 * Row above the composer while a run is in flight: the same three-dot cycle the waiting bubble
 * uses, pinned at the bottom so the state stays visible however far the answer has scrolled.
 */

export function RunningRow() {
  return (
    <div
      role="status"
      aria-label="运行中"
      data-testid="run-indicator"
      className="mx-auto flex max-w-chat-input items-center gap-[3px] px-a16 pb-a6 pt-a2"
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
