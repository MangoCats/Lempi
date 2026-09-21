// The three ramp curves, ported from `fade.rs`'s `Curve` `[SPEC-SUI-226]`.
// Checked against `fixtures/fade/{linear,cosine,exponential}.json`,
// the same tables `fade.rs`'s own tests check against -- one formula per
// curve, computed twice, kept from drifting apart silently `[SPEC021 §4]`.
//
// Kept in its own file rather than folded into `edit.js` so the check that
// verifies it can load exactly this and nothing else -- including from plain
// Node, with no DOM and no Web Audio, which is otherwise unreachable here.
(function (root) {
  const DEPTH_DB = 60;
  const FLOOR = Math.pow(10, -DEPTH_DB / 20);
  const clamp01 = t => Math.max(0, Math.min(1, t));

  // Fade-**in** at normalised progress `t`, clamped to [0, 1]. Silent at
  // t=0, unity at t=1, for every curve -- see `fade.rs`'s own doc comment.
  // `curve` is one of 'linear' | 'cosine' | 'exponential', the same names
  // `Curve::as_str`/`Curve::parse` use on the wire -- anything else falls
  // back to 'exponential', matching the production default rather than
  // silently drawing nothing.
  function gainIn(curve, t) {
    t = clamp01(t);
    switch (curve) {
      case 'linear':
        return t;
      case 'cosine':
        return (1 - Math.cos(Math.PI * t)) * 0.5;
      case 'exponential':
      default: {
        const raw = Math.pow(10, (-DEPTH_DB * (1 - t)) / 20);
        return clamp01((raw - FLOOR) / (1 - FLOOR));
      }
    }
  }

  // Fade-**out** is the mirror of fade-in, which is why one function serves
  // both directions in `fade.rs` and does here too.
  function gainOut(curve, t) {
    return gainIn(curve, 1 - t);
  }

  const api = { gainIn, gainOut };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LempiFade = api;
})(typeof window !== 'undefined' ? window : undefined);
