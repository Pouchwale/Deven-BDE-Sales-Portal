/**
 * A tiny stand-up pouch that flips while the sign-in is on its way - the
 * brand's own object instead of a generic ring. Takes the button's text
 * colour; the zip and hang hole are pink. Animated in globals.css.
 */
export function PouchSpinner() {
  return (
    <svg viewBox="0 0 20 24" className="pw-pouch-spinner size-[18px] shrink-0" aria-hidden>
      <path
        d="M3 4.5 Q3 2.5 5 2.5 H15 Q17 2.5 17 4.5 V19 Q17 21 15 21.3 Q10 22.2 5 21.3 Q3 21 3 19 Z"
        fill="currentColor"
      />
      <rect x="8" y="4" width="4" height="1.5" rx="0.75" fill="var(--pw-pink)" />
      <path d="M4.6 7.6 H15.4" stroke="var(--pw-pink)" strokeWidth="1.1" strokeDasharray="0.8 1.6" />
    </svg>
  );
}
