import Image from "next/image";

/**
 * The first-load PouchWale ident.
 *
 * A calm ink field. The wordmark rises line by line from behind a mask
 * ("POUCH", then "WALE"), a pink rule draws underneath, a letter-spaced
 * caption settles in - then the ink lifts away like a curtain with a pink
 * layer trailing one beat behind it, and the page is underneath.
 *
 * Static markup: hidden unless <html data-intro> is set by INTRO_INIT_SCRIPT,
 * and every step is a CSS keyframe in globals.css (".pw-intro").
 */
function WordmarkHalf({ half }: { half: "top" | "bottom" }) {
  return (
    <span className={`pw-intro-half pw-intro-half-${half}`}>
      <Image
        src="/brand/pouchwale-white.png"
        alt=""
        width={379}
        height={160}
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
      <div className="pw-intro-panel">
        <div className="pw-intro-content">
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
