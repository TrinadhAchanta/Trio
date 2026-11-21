export function Logo({ size = 28, withText = true }: { size?: number; withText?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 select-none">
      <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
        <circle cx="12" cy="12" r="8" fill="var(--accent)" opacity="0.9" />
        <circle cx="20" cy="12" r="8" fill="var(--accent-2)" opacity="0.8" />
        <circle cx="16" cy="19" r="8" fill="var(--accent-3)" opacity="0.75" />
      </svg>
      {withText && <span className="text-lg font-semibold tracking-tight">Trio</span>}
    </span>
  );
}
