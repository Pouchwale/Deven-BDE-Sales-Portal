import { POUCH_ART, Wind } from "@/components/auth/FloatingPouches";

/**
 * The first-load PouchWale ident.
 *
 * A calm ink field. The wordmark rises line by line from behind a mask, and
 * its torn strip flicks into place as if the pouch were just opened. Around
 * it a flock of pouches flies in from below at three depths - small, dim and
 * slow at the back (passing behind the logo), big and quick at the front -
 * fluttering like soft film on the way and giving a little squash as they
 * land, wind streaks dying away. A pink rule draws, a caption settles - then
 * the logo and every pouch shoot upward (the streaks stretch out with the
 * speed), and the ink lifts away with a pink layer one beat behind.
 *
 * As the ink starts to lift, the intro marks <html data-intro="reveal">:
 * on the sign-in page that is the cue for the page's own pouches to fly
 * up from below on the same wind and settle into their float, so the two
 * read as one continuous movement.
 *
 * Static markup: hidden unless <html data-intro> is set by INTRO_INIT_SCRIPT,
 * and every step is a CSS keyframe in globals.css (".pw-intro").
 */

type Depth = "far" | "mid" | "near";

interface Flyer {
  key: string;
  /** Which pouch artwork (POUCH_ART); defaults to the key. */
  art?: string;
  /** Resting place, as an offset from the centre in vmin. */
  x: number;
  y: number;
  /** Width in vmin, resting tilt in degrees. */
  w: number;
  r: number;
  /** Far pouches sit behind the logo, dimmer and slower; near ones in front. */
  depth: Depth;
  /** When it arrives, and when it leaves, in ms. */
  delay: number;
  leave: number;
}

const FLOCK: Flyer[] = [
  // back row: small, dim, slow - they pass behind the logo
  { key: "far-pillow", art: "pillow", x: 17, y: -38, w: 6.5, r: 8, depth: "far", delay: 120, leave: 1640 },
  { key: "far-sachet", art: "sachet-blue", x: -15, y: 33, w: 5.5, r: -10, depth: "far", delay: 200, leave: 1660 },
  { key: "far-standup", art: "standup", x: 42, y: -3, w: 6, r: 6, depth: "far", delay: 260, leave: 1680 },
  { key: "sachet-white", x: -30, y: -40, w: 6.5, r: -7, depth: "far", delay: 340, leave: 1650 },
  { key: "spout-small", x: 9, y: 36, w: 7, r: 10, depth: "far", delay: 380, leave: 1670 },
  // middle
  { key: "spout", x: 35, y: -23, w: 12.5, r: 13, depth: "mid", delay: 240, leave: 1740 },
  { key: "sachet-blue", x: -39, y: 21, w: 11, r: 9, depth: "mid", delay: 320, leave: 1780 },
  // front row: biggest, quickest, over everything
  { key: "standup", x: -36, y: -20, w: 16, r: -12, depth: "near", delay: 180, leave: 1720 },
  { key: "pillow", x: 34, y: 19, w: 18, r: -9, depth: "near", delay: 280, leave: 1760 },
];

function WordmarkHalf({ half }: { half: "top" | "bottom" }) {
  return (
    <span className={`pw-intro-half pw-intro-half-${half}`}>
      <span className="pw-intro-word pw-intro-word-body" />
    </span>
  );
}

export function IntroSplash() {
  return (
    <div className="pw-intro" aria-hidden>
      <div className="pw-intro-trail" />
      <div className="pw-intro-panel">
        <div className="pw-intro-stage">
          {FLOCK.map((f) => (
            <div
              key={f.key}
              className="pw-fly"
              data-depth={f.depth}
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
                <div className="pw-fly-art">
                  <div className="pw-flutter">{POUCH_ART[f.art ?? f.key]}</div>
                </div>
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
              {/* The torn strip, cut from the same artwork on the same canvas,
                  so at rest it sits exactly where the wordmark has it. */}
              <span className="pw-intro-tear">
                <span className="pw-intro-word pw-intro-word-tear" />
              </span>
            </div>
            <span className="pw-intro-rule" />
            <p className="pw-intro-caption">BDE &amp; Sales Portal</p>
          </div>
        </div>
      </div>
    </div>
  );
}
