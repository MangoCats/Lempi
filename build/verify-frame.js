// The picture frame's page, its rules checked [SPEC068].
//
// The page (`player/src/web/frame.html`) is served to a browser the player
// starts, so `cargo test` proves only that the bytes are served. This takes
// its two rules out of the page itself and checks them: the clock is h:mm on
// a 12-hour clock with no leading zero [SPEC-PIC-030], and its colour is
// whichever of black and white has the higher contrast ratio against the
// luminance beneath it -- white below about 0.179 [SPEC-PIC-040]. Read out of
// the page's own text, so a rule changed there is a rule checked here.
//
//   node build/verify-frame.js
const fs = require('fs');
const path = require('path');

const page = fs.readFileSync(path.join(__dirname, '..', 'player', 'src', 'web', 'frame.html'), 'utf8');
function grab(re, what) {
  const m = page.match(re);
  if (!m) { console.error(`verify-frame: the page has no ${what}`); process.exit(2); }
  return m[0];
}
const timeText = new Function(grab(/function timeText\(d\) \{[\s\S]*?\n  \}/, 'timeText') + '\nreturn timeText;')();
const pick = new Function(grab(/function pick\(L\) \{[^\n]*\}/, 'pick') + '\nreturn pick;')();

let failed = 0;
function check(ok, what) {
  console.log((ok ? 'ok   ' : 'FAIL ') + what);
  if (!ok) failed++;
}
const at = (h, m) => new Date(2026, 9, 29, h, m);
check(timeText(at(0, 5)) === '12:05', 'midnight is 12, not 0');
check(timeText(at(9, 5)) === '9:05', 'no leading zero on the hour, two digits for the minutes');
check(timeText(at(12, 40)) === '12:40', 'noon is 12');
check(timeText(at(23, 59)) === '11:59', 'no AM or PM, and 23 is 11');
check(pick(0) === '#fff' && pick(0.17) === '#fff', 'white on a dark picture');
check(pick(0.19) === '#000' && pick(1) === '#000', 'black on a light one');
const L = Math.sqrt(1.05 * 0.05) - 0.05;    // where the two ratios are equal
check(pick(L - 1e-4) === '#fff' && pick(L + 1e-4) === '#000', `the threshold is ${L.toFixed(4)}, the equal contrast`);

console.log(failed ? `verify-frame: ${failed} failed` : 'verify-frame: all checks passed');
process.exit(failed ? 1 : 0);
