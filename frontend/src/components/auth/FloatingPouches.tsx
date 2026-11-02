/**
 * Flat PouchWale pouches floating on the sign-in page: a stand-up zipper
 * pouch with the wordmark, a spout pouch, a 3-side-seal sachet and a pillow
 * pack, plus the brand's four-point sparkles. Flat brand colours, ink
 * outlines and hard offset shadows, like everything else on the site.
 *
 * Pure SVG + CSS keyframes (".pw-pouch" in globals.css), so it costs no
 * script and stops moving for anyone who has asked for reduced motion.
 * Decorative only, hidden from screen readers.
 */

type Layout = "panel" | "band";

const INK = "#111111";
const STROKE = 5;

/* ------------------------------------------------------------- shapes */

function zigzag(x0: number, x1: number, y: number, depth: number, teeth: number): string {
  const step = (x1 - x0) / teeth;
  let d = "";
  for (let i = 0; i < teeth; i++) {
    d += ` L${x0 + step * i + step / 2} ${y - depth} L${x0 + step * (i + 1)} ${y}`;
  }
  return d;
}

export function StandUpPouch({ fill }: { fill: string }) {
  return (
    <svg viewBox="0 0 200 250" className="block h-auto w-full overflow-visible">
      <path
        d="M20 22 Q20 10 32 10 H168 Q180 10 180 22 V220 Q180 236 164 238 Q100 246 36 238 Q20 236 20 220 Z"
        fill={fill}
        stroke={INK}
        strokeWidth={STROKE}
        strokeLinejoin="round"
      />
      {/* hang hole, zipper, tear notches, bottom gusset */}
      <rect x="78" y="22" width="44" height="11" rx="5.5" fill={INK} />
      <path d="M30 54 H170" stroke={INK} strokeWidth="3.5" strokeLinecap="round" strokeDasharray="1 7" />
      <path d="M20 40 l9 5 l-9 5 M180 40 l-9 5 l9 5" fill="none" stroke={INK} strokeWidth="3" strokeLinejoin="round" />
      <path d="M36 220 Q100 230 164 220" fill="none" stroke={INK} strokeWidth="3" strokeLinecap="round" />
      {/* a flat colour block across the face, like the cover pouch */}
      <path d="M22 168 L178 128 V220 Q178 234 164 236 Q100 244 36 236 Q22 234 22 220 Z" fill="#FFFFFF" opacity="0.18" />
      <image href="/brand/pouchwale-white.png" x="42" y="96" width="116" height="49" />
    </svg>
  );
}

export function SpoutPouch({ fill, cap }: { fill: string; cap: string }) {
  return (
    <svg viewBox="0 0 200 260" className="block h-auto w-full overflow-visible">
      {/* the cap sits on the angled shoulder */}
      {/* Centre of the shoulder, turned to point straight out of it. */}
      <g transform="translate(45 74) rotate(-55)">
        <rect x="-13" y="-24" width="26" height="30" fill={cap} stroke={INK} strokeWidth="4" />
        <rect x="-22" y="-54" width="44" height="32" rx="6" fill={cap} stroke={INK} strokeWidth="4" />
        <path d="M-10 -48 V-28 M0 -48 V-28 M10 -48 V-28" stroke={INK} strokeWidth="3" strokeLinecap="round" />
      </g>
      <path
        d="M66 44 H160 Q176 44 176 60 V230 Q176 246 160 248 Q100 256 40 248 Q24 246 24 230 V104 Z"
        fill={fill}
        stroke={INK}
        strokeWidth={STROKE}
        strokeLinejoin="round"
      />
      <path d="M40 230 Q100 240 160 230" fill="none" stroke={INK} strokeWidth="3" strokeLinecap="round" />
      <circle cx="132" cy="150" r="30" fill="#FFFFFF" opacity="0.28" />
      <path d="M56 182 H120 M56 198 H100" stroke={INK} strokeWidth="5" strokeLinecap="round" />
    </svg>
  );
}

export function Sachet({ fill, band }: { fill: string; band: string }) {
  const top = `M14 32${zigzag(14, 166, 32, 9, 10)}`;
  return (
    <svg viewBox="0 0 180 220" className="block h-auto w-full overflow-visible">
      <path
        d={`${top} V196 Q166 208 154 208 H26 Q14 208 14 196 Z`}
        fill={fill}
        stroke={INK}
        strokeWidth={STROKE}
        strokeLinejoin="round"
      />
      <rect x="14" y="96" width="152" height="40" fill={band} stroke={INK} strokeWidth="4" />
      <path d="M30 52 V190 M150 52 V190" stroke={INK} strokeWidth="3" strokeDasharray="2 8" strokeLinecap="round" />
    </svg>
  );
}

export function PillowPack({ fill, stripe }: { fill: string; stripe: string }) {
  // Crimped ends: the left edge zigzags down, the right edge back up.
  const teeth = 7;
  const step = 132 / teeth;
  let left = "";
  let right = "";
  for (let i = 0; i < teeth; i++) {
    left += ` L20 ${14 + step * i + step / 2} L30 ${14 + step * (i + 1)}`;
    right += ` L220 ${146 - step * i - step / 2} L210 ${146 - step * (i + 1)}`;
  }
  return (
    <svg viewBox="0 0 240 160" className="block h-auto w-full overflow-visible">
      <path
        d={`M30 14${left} H210${right} Z`}
        fill={fill}
        stroke={INK}
        strokeWidth={STROKE}
        strokeLinejoin="round"
      />
      <path d="M44 14 V146 M196 14 V146" stroke={INK} strokeWidth="3" />
      <path d="M44 62 H196 V98 H44 Z" fill={stripe} stroke={INK} strokeWidth="4" />
    </svg>
  );
}

function Sparkle({ fill }: { fill: string }) {
  return (
    <svg viewBox="-12 -12 24 24" className="block h-auto w-full overflow-visible">
      <path d="M0 -11 Q2 -2 11 0 Q2 2 0 11 Q-2 2 -11 0 Q-2 -2 0 -11 Z" fill={fill} />
    </svg>
  );
}

/** Speed lines: "in" trails a pouch as it arrives and dies away; "out" shoots
 *  long behind it as it leaves. Two sets, because one cannot fade out and
 *  come back brighter through a parent that is already transparent. Timed
 *  by --in / --out on an ancestor (globals.css, ".pw-wind"). */
export function Wind({ kind, lines = 4 }: { kind: "in" | "out"; lines?: number }) {
  return (
    <span className={`pw-wind pw-wind-${kind}`}>
      {Array.from({ length: lines }, (_, i) => (
        <i key={i} />
      ))}
    </span>
  );
}

/* ------------------------------------------------------------ scenes */

interface Placement {
  key: string;
  /** Position and width as % of the stage. */
  left: number;
  top: number;
  width: number;
  rotate: number;
  shadow: string;
  duration: number;
  delay: number;
  node: React.ReactNode;
}

const PINK = "#FE4587";
const YELLOW = "#F3E33F";
const BLUE = "#149EDA";
const ORANGE = "#FF561D";

/* The pouches themselves wear deeper, print-like versions of the brand
   colours (rich on ink without toy-box brightness); the bright pops stay
   for shadows and sparkles. One set, shared with the intro, so the pouches
   that fly in there are the very ones that float here. */
const DEEP = { pink: "#E0377A", blue: "#1289C0", yellow: "#EBD834", orange: "#EE4E18", white: "#F6F6F4" };

export const POUCH_ART: Record<string, React.ReactNode> = {
  standup: <StandUpPouch fill={DEEP.pink} />,
  spout: <SpoutPouch fill={DEEP.yellow} cap={DEEP.orange} />,
  "sachet-blue": <Sachet fill={DEEP.blue} band={DEEP.yellow} />,
  pillow: <PillowPack fill={DEEP.orange} stripe={DEEP.white} />,
  "sachet-white": <Sachet fill={DEEP.white} band={DEEP.pink} />,
  "spout-small": <SpoutPouch fill={DEEP.blue} cap={DEEP.white} />,
};

const SCENES: Record<Layout, Placement[]> = {
  panel: [
    { key: "sachet-white", left: 6, top: 11, width: 15, rotate: -14, shadow: PINK, duration: 6.4, delay: -1.2, node: POUCH_ART["sachet-white"] },
    { key: "sachet-blue", left: 68, top: 9, width: 22, rotate: 12, shadow: ORANGE, duration: 7.2, delay: -3.1, node: POUCH_ART["sachet-blue"] },
    { key: "spout", left: 3, top: 47, width: 27, rotate: -10, shadow: PINK, duration: 6.8, delay: -2.2, node: POUCH_ART.spout },
    { key: "standup", left: 31, top: 23, width: 40, rotate: 4, shadow: YELLOW, duration: 6, delay: 0, node: POUCH_ART.standup },
    { key: "pillow", left: 58, top: 71, width: 34, rotate: -9, shadow: BLUE, duration: 7.6, delay: -4.4, node: POUCH_ART.pillow },
    { key: "spark-1", left: 25, top: 17, width: 4, rotate: 0, shadow: "transparent", duration: 3.2, delay: -0.6, node: <Sparkle fill={YELLOW} /> },
    { key: "spark-2", left: 78, top: 53, width: 5, rotate: 0, shadow: "transparent", duration: 3.8, delay: -1.8, node: <Sparkle fill={PINK} /> },
    { key: "spark-3", left: 22, top: 89, width: 3.5, rotate: 0, shadow: "transparent", duration: 3.4, delay: -2.6, node: <Sparkle fill={BLUE} /> },
    { key: "spark-4", left: 90, top: 35, width: 3, rotate: 0, shadow: "transparent", duration: 4, delay: -0.2, node: <Sparkle fill={YELLOW} /> },
  ],
  band: [
    { key: "spout", left: 5, top: 26, width: 24, rotate: -12, shadow: PINK, duration: 6.8, delay: -2.2, node: POUCH_ART.spout },
    { key: "pillow", left: 68, top: 50, width: 28, rotate: 10, shadow: BLUE, duration: 7.6, delay: -4.4, node: POUCH_ART.pillow },
    { key: "sachet-blue", left: 72, top: 4, width: 17, rotate: 14, shadow: ORANGE, duration: 7.2, delay: -3.1, node: POUCH_ART["sachet-blue"] },
    { key: "standup", left: 34, top: 10, width: 31, rotate: 3, shadow: YELLOW, duration: 6, delay: 0, node: POUCH_ART.standup },
    { key: "spark-1", left: 27, top: 8, width: 5, rotate: 0, shadow: "transparent", duration: 3.2, delay: -0.6, node: <Sparkle fill={YELLOW} /> },
    { key: "spark-2", left: 64, top: 30, width: 4.5, rotate: 0, shadow: "transparent", duration: 3.8, delay: -1.8, node: <Sparkle fill={PINK} /> },
  ],
};

/* When each one arrives, in ms after the cue: bottom of the scene first, so
   the flock rises behind the lifting intro curtain; sparkles once landed. */
const ARRIVES: Record<string, number> = {
  pillow: 120,
  spout: 190,
  standup: 260,
  "sachet-blue": 330,
  "sachet-white": 400,
};
const SPARKLES_FROM = 780;

export function FloatingPouches({
  layout,
  className,
  leaving = false,
}: {
  layout: Layout;
  className?: string;
  /** Signed in: the pouches take off on the wind (".pw-takeoff"). */
  leaving?: boolean;
}) {
  const sparkles = SCENES[layout].filter((p) => p.key.startsWith("spark")).map((p) => p.key);
  return (
    <div className={className} aria-hidden>
      <div className="relative size-full">
        {SCENES[layout].map((p) => {
          const spark = sparkles.indexOf(p.key);
          const arrives = spark >= 0 ? SPARKLES_FROM + spark * 90 : (ARRIVES[p.key] ?? 200);
          return (
            // Outer: the entrance (fly up on the wind, or twinkle in).
            // Inner: the endless float, tilt and stamp shadow.
            <div
              key={p.key}
              className={spark >= 0 ? "pw-spark-in absolute" : "pw-pouch-in absolute"}
              style={
                {
                  left: `${p.left}%`,
                  top: `${p.top}%`,
                  width: `${p.width}%`,
                  "--in": `${arrives}ms`,
                  "--enter": layout === "panel" ? "85vh" : "260px",
                  "--leave": layout === "panel" ? "-120vh" : "-340px",
                } as React.CSSProperties
              }
            >
              {spark >= 0 ? null : <Wind kind="in" />}
              {/* Mounted only for the take-off, so its gust starts then. */}
              {leaving && spark < 0 ? <Wind kind="out" /> : null}
              {/* The cursor's push (usePouchMagnet) and the hover lift. */}
              <div className="pw-react">
                <div
                  className="pw-pouch relative"
                  style={
                    {
                      "--r": `${p.rotate}deg`,
                      "--shadow": p.shadow,
                      "--dur": `${p.duration}s`,
                      "--delay": `${p.delay}s`,
                    } as React.CSSProperties
                  }
                >
                  {spark >= 0 ? p.node : <div className="pw-flutter">{p.node}</div>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
