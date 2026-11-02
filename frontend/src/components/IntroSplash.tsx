import Image from "next/image";

import { POUCH_ART } from "@/components/auth/FloatingPouches";

/**
 * The first-load PouchWale ident.
 *
 * A calm ink field. The wordmark rises line by line from behind a mask while
 * a flock of pouches flies in from below, trailing wind streaks that die
 * away as they settle around it. A pink rule draws, a caption settles - then
 * the logo and every pouch shoot upward together (the streaks stretch out
 * with the speed), and the ink lifts away with a pink layer one beat behind.
 *
 * On the sign-in page the pouches that also float there do not fly off:
 * IntroHandoff glides each one into its spot on the page instead, the ink
 * lifts away behind them, and the page carries on floating them.
 *
 * Static markup: hidden unless <html data-intro> is set by INTRO_INIT_SCRIPT,
 * and every step is a CSS keyframe in globals.css (".pw-intro").
 */

interface Flyer {
  key: string;
  /** Resting place, as an offset from the centre in vmin. */
  x: number;
  y: number;
  /** Width in vmin, resting tilt in degrees. */
  w: number;
  r: number;
  /** When it arrives, and when it leaves, in ms. */
  delay: number;
  leave: number;
}

const FLOCK: Flyer[] = [
  { key: "standup", x: -36, y: -20, w: 15, r: -12, delay: 160, leave: 1700 },
  { key: "spout", x: 35, y: -23, w: 12.5, r: 13, delay: 240, leave: 1740 },
  { key: "sachet-blue", x: -39, y: 21, w: 11, r: 9, delay: 320, leave: 1780 },
  { key: "pillow", x: 34, y: 19, w: 17, r: -9, delay: 280, leave: 1720 },
  { key: "sachet-white", x: -30, y: -40, w: 7.5, r: -7, delay: 380, leave: 1690 },
  { key: "spout-small", x: 9, y: 36, w: 8.5, r: 10, delay: 420, leave: 1760 },
];

/** Speed lines: "in" trails a pouch as it lands and dies away; "out" shoots
 *  long behind it as it leaves. Two sets, because one cannot fade out and
 *  come back brighter through a parent that is already transparent. */
function Wind({ kind, lines = 4 }: { kind: "in" | "out"; lines?: number }) {
  return (
    <span className={`pw-wind pw-wind-${kind}`}>
      {Array.from({ length: lines }, (_, i) => (
        <i key={i} />
      ))}
    </span>
  );
}

function WordmarkHalf({ half }: { half: "top" | "bottom" }) {
  return (
    <span className={`pw-intro-half pw-intro-half-${half}`}>
      <Image
        src="/brand/pouchwale-white.png"
        alt=""
        width={1136}
        height={480}
        unoptimized
        priority
        className="pw-intro-word"
      />
    </span>
  );
}

export function IntroSplash() {
  return (
    <div className="pw-intro" aria-hidden>
      <div className="pw-intro-trail" />
      <div className="pw-intro-panel" />
      {/* The stage is a sibling of the panel, not inside it: on the sign-in
          page the pouches stay put and land while the ink lifts away. */}
      <div className="pw-intro-stage">
          {FLOCK.map((f) => (
            <div
              key={f.key}
              className="pw-fly"
              data-pouch={f.key}
              style={
                {
                  "--x": f.x,
                  "--y": f.y,
                  "--w": f.w,
                  "--r": `${f.r}deg`,
                  "--in": `${f.delay}ms`,
                  "--out": `${f.leave}ms`,
                } as React.CSSProperties
              }
            >
              <div className="pw-fly-exit">
                <Wind kind="in" />
                <Wind kind="out" />
                <div className="pw-fly-art">{POUCH_ART[f.key]}</div>
              </div>
            </div>
          ))}

          <div className="pw-intro-content">
            <span className="pw-intro-logo-wind">
              <Wind kind="out" lines={6} />
            </span>
            <div className="pw-intro-logo">
              <WordmarkHalf half="top" />
              <WordmarkHalf half="bottom" />
            </div>
            <span className="pw-intro-rule" />
            <p className="pw-intro-caption">BDE &amp; Sales Portal</p>
          </div>
      </div>
    </div>
  );
}
