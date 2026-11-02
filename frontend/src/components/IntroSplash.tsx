import Image from "next/image";

/**
 * The first-load PouchWale ident: four brand stripes rise, a pink block wipes
 * across, the wordmark pops in, then the whole field lifts away. Static
 * markup - it is hidden unless <html data-intro> is set by INTRO_INIT_SCRIPT,
 * and every step is a CSS keyframe in globals.css (".pw-intro").
 */
export function IntroSplash() {
  return (
    <div className="pw-intro" aria-hidden>
      <div className="pw-intro-stripes">
        <span className="bg-pw-yellow" />
        <span className="bg-pw-pink" />
        <span className="bg-pw-blue" />
        <span className="bg-pw-orange" />
      </div>
      <div className="pw-intro-wipe" />
      <div className="pw-intro-brand">
        <Image
          src="/brand/pouchwale-white.png"
          alt=""
          width={379}
          height={160}
          unoptimized
          priority
          className="pw-intro-logo"
        />
        <p className="pw-intro-tag">BDE &amp; Sales Portal</p>
      </div>
    </div>
  );
}
