//! One command line, seventeen binaries.
//!
//! **Why this exists.** `./lempi --help` did not print help. Every binary
//! parsed `std::env::args()` by hand, `lempi` took its database as argument
//! zero, and nothing recognised `--help` -- so the string `--help` became the
//! database path and the player started against a file it then created. A file
//! literally named `--help` was found sitting in a checkout on another machine,
//! left there by someone who tried it. `--version` was recognised by exactly
//! one of the seventeen.
//!
//! **The rule this module enforces `[GDE-CLI-020]`: every option is named.**
//! There are no positional arguments. A bare word on the command line is not a
//! value waiting for a home, it is a mistake -- except during the transition
//! described below.
//!
//! **One declaration, not two `[GDE-ARC-033]`.** An [`Opt`] carries its name,
//! whether it takes a value and of what kind, its default, whether it is
//! required, and the sentence that describes it. The parser reads that record
//! and so does [`Cli::usage`], so the help text cannot describe an option the
//! parser does not accept, or omit one it does. There is no second list. This
//! is the same discipline `SkipShape` and `EchoNode::trim_limit_ms` already
//! follow after `[GDE-ARC-033]` found one quantity written down three times,
//! and it is why adding an option means editing exactly one place.
//!
//! Defaults are part of that record rather than a literal at the call site:
//! the default port is written once, in [`default_port!`], and the option's
//! value, the help line and `fbui`'s default URL all come from it.
//!
//! **No argument-parsing crate.** This repository is deliberately light on
//! dependencies -- every one of them is a memory decision on a 512 MB Pi Zero
//! `[REQ-HW-140]`, and `clap` with its derive feature pulls in a build-time
//! graph an order of magnitude larger than the two hundred lines below. Nothing
//! here needs subcommands, shell completion or value hints. Should any of that
//! ever be wanted, this module is small enough to throw away.

use std::fmt::Write as _;

/// **The default port, defined once in the whole project** `[GDE-CLI-100]`.
///
/// A macro rather than a `const` so that it can be `concat!`ed into the other
/// place a port must appear at compile time -- `fbui`'s default WebSocket
/// URL -- instead of that string carrying a second copy of the number. It was
/// written out in eight places before this: the option default, `fbui`'s URL,
/// `fbui`'s own doc comment, three shell scripts, `tools/lempi_control.py`
/// and a form field in the console. Two copies of one quantity is the fault
/// `[GDE-ARC-033]` names, and eight is the same fault louder.
///
/// A local user moves the port without editing any of that: `--port`, the
/// stored setting, or `LEMPI_PORT` `[GDE-CLI-090]`. This is only the floor
/// under all three.
#[macro_export]
macro_rules! default_port {
    () => {
        "5720"
    };
}

/// How many passages the Director keeps queued ahead. Written here because
/// `--depth`'s default and [`crate::QUEUE_DEPTH`] were two copies of `5`.
///
/// **The literal moved to `lempi_core` with the constant it defines**, and this
/// expands to it rather than restating it. The macro stays because four option
/// tables and `cli/tests.rs` call it, and because the point of it was never the
/// expansion -- it was that there is one place the number is written
/// `[GDE-ARC-033]`. A crate boundary between the option and the setting would
/// have made that two places again.
#[macro_export]
macro_rules! default_queue_depth {
    () => {
        $crate::DEFAULT_QUEUE_DEPTH
    };
}

/// How often a guest's `status` is read while playing, in milliseconds.
/// `--interval`'s default and [`crate::SAMPLE_INTERVAL_MS`] were two copies.
/// Expands to `lempi_core`'s literal, for the reason above.
#[macro_export]
macro_rules! default_sample_interval_ms {
    () => {
        $crate::DEFAULT_SAMPLE_INTERVAL_MS
    };
}

/// The same interval in seconds, for the three tools that take it that way
/// `[GDE-CLI-115]`.
///
/// **Derived, so it cannot drift from the milliseconds above.** Written as a
/// separate literal it would be the second copy `[GDE-ARC-033]` — and the
/// bug this exists to prevent was the reverse of drift: `INTERVAL_S` had no
/// default at all, and three binaries panicked at startup rather than
/// falling back to the 5 seconds their old code used.
#[macro_export]
macro_rules! default_sample_interval_seconds {
    () => {
        "5"
    };
}

/// Read one of the defaults above as a number, at compile time.
///
/// **Defined in `lempi_core` and re-exported here**, so `crate::cli::as_number`
/// still names it. It moved because the constants it reads moved: `QUEUE_DEPTH`
/// and `SAMPLE_INTERVAL_MS` are derived from their own default strings inside
/// that crate, and a `const fn` cannot be borrowed back across the boundary
/// from the crate that depends on it.
pub use lempi_core::as_number;

/// [`default_port!`] as a value, for anything reading it at run time.
pub const DEFAULT_PORT: &str = crate::default_port!();

/// What an option takes after its name.
///
/// The kind is checked at parse time, so a binary that has been handed
/// `--port nonsense` says so and exits rather than quietly falling back to its
/// default -- which is what every hand-rolled `.parse().ok().unwrap_or(d)` in
/// this tree used to do `[GDE-CLI-030]`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Arity {
    /// Present or absent, takes nothing. `--apply`.
    Flag,
    /// One string; the `&str` is the metavariable shown in the usage text.
    Text(&'static str),
    /// One non-negative whole number.
    Int(&'static str),
    /// One real number.
    Real(&'static str),
}

impl Arity {
    /// The metavariable, or `None` for a flag.
    pub fn meta(&self) -> Option<&'static str> {
        match self {
            Arity::Flag => None,
            Arity::Text(m) | Arity::Int(m) | Arity::Real(m) => Some(m),
        }
    }

    /// Whether a given string is an acceptable value for this option.
    pub fn accepts(&self, raw: &str) -> bool {
        self.check(raw).is_ok()
    }

    /// Whether a given string is an acceptable value, and why not if not.
    fn check(&self, raw: &str) -> Result<(), &'static str> {
        match self {
            Arity::Flag => Err("takes no value"),
            Arity::Text(_) => Ok(()),
            Arity::Int(_) => match raw.parse::<u64>() {
                Ok(_) => Ok(()),
                Err(_) => Err("expected a whole number, zero or above"),
            },
            Arity::Real(_) => match raw.parse::<f64>() {
                Ok(v) if v.is_finite() => Ok(()),
                _ => Err("expected a number"),
            },
        }
    }
}

/// One option, declared once.
///
/// Everything anyone needs to know about it is here: the parser reads this,
/// the usage text is written from this, and the default value is taken from
/// this rather than retyped at the call site.
#[derive(Clone, Copy)]
pub struct Opt {
    /// The spelling, including the leading dashes. `--library`.
    pub name: &'static str,
    /// The short form, written without its dash: `"p"`, or `"lib"` where one
    /// letter is genuinely contested. In the same declaration as the long
    /// name, so the two cannot be registered in different places
    /// `[GDE-ARC-033]`. Every option has one.
    ///
    /// **`-abc` is one option named `abc`, never `-a -b -c`.** Both readings
    /// are common in Unix tools; this one does not bundle, because bundling
    /// and multi-letter short forms cannot both be unambiguous and the
    /// multi-letter form is what resolves `--library` against `--listener`
    /// `[GDE-CLI-070]`.
    pub short: Option<&'static str>,
    pub arity: Arity,
    /// One line, lower case, no full stop. Shown in the usage text.
    pub help: &'static str,
    /// Refuse to run without it.
    pub required: bool,
    /// Used when the option is absent, and shown in the usage text. Held as a
    /// string so one field serves every arity and the help line cannot
    /// disagree with what the code actually falls back to.
    pub default: Option<&'static str>,
    /// Which retired positional slot this option used to be, counted from
    /// zero, for as long as the positional form is still accepted
    /// `[GDE-CLI-040]`.
    pub was_positional: Option<usize>,
    /// Spellings this option used to have. Still read, with the same warning
    /// a retired positional gets, so renaming one costs nobody a broken
    /// command line `[GDE-CLI-040]`.
    pub was_named: &'static [&'static str],
    /// The program answers this and stops, the way it does for `--help`.
    /// Required options are then not required: `lempi --list-devices` asks
    /// the machine a question and needs no database to answer it.
    pub stops_early: bool,
    /// The environment variable, when it is not the derived one. Every option
    /// has an environment form; [`env_var`] derives it from the long name, so
    /// there is no list to keep in step. This overrides that derivation where
    /// a name is already established -- `LEMPI_PORT` predates this module.
    pub env_override: Option<&'static str>,
    /// The key this option has in the listener's persisted settings, when it
    /// has one. `None` means layer 2 does not apply to this option, which is
    /// most of them: the sixteen non-player binaries have no layer 2 at all,
    /// and nothing has to say so sixteen times `[GDE-CLI-090]`.
    pub setting: Option<&'static str>,
    /// **Never consults layer 2, because layer 2 is found using it.**
    /// `--listener` names the database the settings live in, so it cannot
    /// ask the settings where the settings are `[GDE-CLI-095]`.
    pub bootstrap: bool,
    /// Multiply a layer-2 value by this to get the option's own unit. The
    /// store keeps `sample_interval_ms` in milliseconds and three binaries
    /// take `--interval` in seconds; declaring the factor keeps those on the
    /// chain instead of converting by hand in each binary `[GDE-CLI-090]`.
    pub setting_scale: Option<f64>,
    /// Environment variables this option used to answer to. Read, with a
    /// warning naming the current one -- the same courtesy a renamed option
    /// gets `[GDE-CLI-040]`.
    pub env_was: &'static [&'static str],
    /// **Not settable from the environment at all.** For the flags that
    /// make a program write: `LEMPI_APPLY=1` left exported in a shell would
    /// turn `relink` and `import_bundle` from reporting into writing, at a
    /// distance, with nothing on the command line to show for it
    /// `[GDE-CLI-105]`.
    pub no_env: bool,
}

impl Opt {
    const fn of(name: &'static str, arity: Arity, help: &'static str) -> Opt {
        Opt {
            name,
            short: None,
            arity,
            help,
            required: false,
            default: None,
            was_positional: None,
            was_named: &[],
            stops_early: false,
            env_override: None,
            setting: None,
            bootstrap: false,
            setting_scale: None,
            env_was: &[],
            no_env: false,
        }
    }
    /// A value-taking option with no default, not required.
    pub const fn text(name: &'static str, meta: &'static str, help: &'static str) -> Opt {
        Opt::of(name, Arity::Text(meta), help)
    }
    pub const fn int(name: &'static str, meta: &'static str, help: &'static str) -> Opt {
        Opt::of(name, Arity::Int(meta), help)
    }
    pub const fn real(name: &'static str, meta: &'static str, help: &'static str) -> Opt {
        Opt::of(name, Arity::Real(meta), help)
    }
    pub const fn flag(name: &'static str, help: &'static str) -> Opt {
        Opt::of(name, Arity::Flag, help)
    }
    /// Refuse to run without this option.
    pub const fn needed(self) -> Opt {
        Opt { required: true, ..self }
    }
    /// What it falls back to when absent. Written here and nowhere else.
    pub const fn or(self, d: &'static str) -> Opt {
        Opt { default: Some(d), ..self }
    }
    /// This option used to be positional argument `n`, counting from zero.
    pub const fn was(self, n: usize) -> Opt {
        Opt { was_positional: Some(n), ..self }
    }
    /// Spellings this option used to answer to, still read and warned about.
    pub const fn formerly(self, names: &'static [&'static str]) -> Opt {
        Opt { was_named: names, ..self }
    }
    /// The short form, written without its dash. Declared beside the long one.
    pub const fn short(self, c: &'static str) -> Opt {
        Opt { short: Some(c), ..self }
    }
    /// The program answers this and stops; nothing else is required.
    pub const fn stops(self) -> Opt {
        Opt { stops_early: true, ..self }
    }
    /// Use this environment variable rather than the derived one.
    pub const fn env(self, name: &'static str) -> Opt {
        Opt { env_override: Some(name), ..self }
    }
    /// This option is also a persisted listener setting, under `key`
    /// -- layer 2 of the chain `[GDE-CLI-090]`.
    pub const fn setting(self, key: &'static str) -> Opt {
        Opt { setting: Some(key), ..self }
    }
    /// As [`Opt::setting`], where the stored value is in a different unit:
    /// the option's value is the stored one times `scale`.
    pub const fn setting_scaled(self, key: &'static str, scale: f64) -> Opt {
        Opt { setting: Some(key), setting_scale: Some(scale), ..self }
    }
    /// Environment variables this option used to answer to.
    pub const fn env_formerly(self, names: &'static [&'static str]) -> Opt {
        Opt { env_was: names, ..self }
    }
    /// Keep this option off layer 3 entirely `[GDE-CLI-105]`.
    pub const fn not_from_env(self) -> Opt {
        Opt { no_env: true, ..self }
    }
    /// This option is needed to *find* the persisted settings, so it resolves
    /// through command line, environment and default only `[GDE-CLI-095]`.
    pub const fn bootstrap(self) -> Opt {
        Opt { bootstrap: true, ..self }
    }
    /// The same option, worded for one binary that means something slightly
    /// different by it. Name and arity -- the halves a caller's command line
    /// depends on -- still come from the shared declaration.
    pub const fn saying(self, help: &'static str) -> Opt {
        Opt { help, ..self }
    }

    /// `-l, --library PATH`, as the usage text shows it -- both forms, from
    /// the one declaration.
    fn spelling(&self) -> String {
        let head = match self.short {
            Some(c) => format!("-{c}, {}", self.name),
            None => format!("{:4}{}", "", self.name),
        };
        match self.arity.meta() {
            Some(m) => format!("{head} {m}"),
            None => head,
        }
    }
}

/// The options this module handles itself, for every binary, so that no spec
/// declares them and none can forget them `[GDE-CLI-010]`.
/// The two options every binary has, declared once, here.
///
/// They are ordinary [`Opt`]s: the parser matches them, and [`Cli::usage`]
/// renders them through the same loop as any other option rather than
/// printing two hardcoded lines beside it. `-V` for version is the
/// widespread convention, not `-v` `[GDE-CLI-060]`.
pub const CORE: [Opt; 2] = [
    Opt::flag("--help", "print this and exit").short("h"),
    Opt::flag("--version", "print the build identity and exit").short("V"),
];

/// Whether a token is one of [`CORE`]'s, by either spelling.
fn is_core(o: &Opt, tok: &str) -> bool {
    tok == o.name || tok.strip_prefix('-').filter(|s| !s.starts_with('-')) == o.short
}

pub fn is_help(tok: &str) -> bool {
    is_core(&CORE[0], tok)
}

pub fn is_version(tok: &str) -> bool {
    is_core(&CORE[1], tok)
}

/// Whether a short form belongs to the core and is therefore unavailable to
/// a table. `v` is held back unused on top of [`CORE`]'s own: `-v` is
/// `--verbose` everywhere else, and a binary here that one day wants
/// verbosity must not find the letter already spent `[GDE-CLI-060]`.
pub fn short_is_reserved(s: &str) -> bool {
    s == "v" || CORE.iter().any(|o| o.short == Some(s))
}

/// One binary's command line.
pub struct Cli {
    /// The binary's name, as `[[bin]]` in `Cargo.toml` spells it. Pinned to
    /// that list by `specs::every_binary_has_exactly_one_spec`.
    pub program: &'static str,
    /// One line saying what the program is for.
    pub summary: &'static str,
    pub opts: &'static [Opt],
    /// Extra lines printed below the options, for anything an option's own
    /// sentence cannot carry.
    pub notes: &'static [&'static str],
}

/// What a command line turned out to be.
///
/// Returned rather than acted on, so the whole grammar is testable in-process
/// without a subprocess and without exiting the test runner.
pub enum Outcome {
    Run(Args),
    /// Print on stdout, exit 0.
    Help(String),
    /// Print on stdout, exit 0.
    Version(String),
    /// Print on stderr, exit 2.
    Usage(String),
}

impl Cli {
    /// By its long spelling, or by its short one written `-l`. Retired
    /// spellings are not found here; see [`Self::find_any`].
    pub fn find(&self, name: &str) -> Option<&Opt> {
        if let Some(o) = self.opts.iter().find(|o| o.name == name) {
            return Some(o);
        }
        // **The whole token after the dash is one name.** `-lib` is the
        // option whose short form is `lib`; it is never `-l -i -b`. Nothing
        // bundles here `[GDE-CLI-070]`.
        let short = name.strip_prefix('-').filter(|s| !s.starts_with('-'))?;
        self.opts.iter().find(|o| o.short == Some(short))
    }

    /// By its current spelling or a retired one; the `bool` is true when the
    /// spelling used is retired.
    pub fn find_any(&self, name: &str) -> Option<(&Opt, bool)> {
        if let Some(o) = self.find(name) {
            return Some((o, false));
        }
        self.opts.iter().find(|o| o.was_named.contains(&name)).map(|o| (o, true))
    }

    /// `lempi 0.1.0 (93566b08ee51)` -- the crate version and the commit, from
    /// the same [`crate::build_id`] the Settings page and `/build` already
    /// serve, never retyped `[GDE-ARC-033]`.
    pub fn version_line(&self) -> String {
        format!("{} {}", self.program, crate::build_id())
    }

    /// The usage text, written from [`Self::opts`] and from nothing else.
    pub fn usage(&self) -> String {
        let mut s = String::new();
        let _ = writeln!(s, "{}", self.version_line());
        let _ = writeln!(s, "{}", self.summary);
        let _ = writeln!(s);
        let _ = writeln!(s, "usage: {} [options]", self.program);
        let _ = writeln!(s);
        // One column, wide enough for the longest spelling this binary has, so
        // the layout follows the options rather than a guessed constant.
        let w = self
            .opts
            .iter()
            .chain(CORE.iter())
            .map(|o| o.spelling().chars().count())
            .max()
            .unwrap_or(20)
            + 2;
        for o in self.opts.iter().chain(CORE.iter()) {
            let mut tail = o.help.to_string();
            if let Some(d) = o.default {
                let _ = write!(tail, " (default {d})");
            }
            if o.required {
                let _ = write!(tail, " [required]");
            }
            let _ = writeln!(s, "  {:<w$}{}", o.spelling(), tail, w = w);
        }
        if !self.notes.is_empty() {
            let _ = writeln!(s);
        }
        for n in self.notes {
            let _ = writeln!(s, "{n}");
        }
        let _ = writeln!(s);
        let _ = write!(
            s,
            "Every option is named; there are no positional arguments [GDE-CLI-020].\n\
              A short form is one name: -ab is the option `ab`, never -a -b [GDE-CLI-070]."
        );
        // The chain, stated once. **The worked example is taken from this
        // binary's own first option**, so it is always a name the reader can
        // really export, and there is no second place where a variable name
        // is written down `[GDE-CLI-090]`. A hardcoded one sat here saying
        // `LEMPI_` after the prefix became `LEMPI_`.
        let example = self
            .opts
            .first()
            .map(|o| format!("{} is ${}", o.name, env_var(o)))
            .unwrap_or_default();
        let _ = write!(
            s,
            "\n\nA value comes from, in order: the command line; {}the environment\n\
             ({example}, and so on); then the default shown above. On startup a\n\
             process says which of those answered, wherever it was not the\n\
             default [GOV-SRC-040].",
            if self.opts.iter().any(|o| o.setting.is_some()) { "the stored setting; " } else { "" }
        );
        if self.opts.iter().any(|o| o.was_positional.is_some()) {
            let mut retired: Vec<&Opt> =
                self.opts.iter().filter(|o| o.was_positional.is_some()).collect();
            retired.sort_by_key(|o| o.was_positional.unwrap());
            let names: Vec<&str> = retired.iter().map(|o| o.name).collect();
            let _ = write!(
                s,
                "\nThe old bare-argument form is still read, in the order {}, and\n\
                 prints the corrected line on stderr. It will be removed [GDE-CLI-040].",
                names.join(" ")
            );
        }
        for o in self.opts.iter().filter(|o| !o.was_named.is_empty()) {
            let _ = write!(
                s,
                "\n{} was {}; the old spelling is still read and warns [GDE-CLI-040].",
                o.name,
                o.was_named.join(", ")
            );
        }
        s
    }

    /// Usage preceded by what was wrong with the command line.
    fn refuse(&self, why: &str) -> String {
        format!("{}: {}\n\n{}", self.program, why, self.usage())
    }

    /// Read `argv` and nothing else: layer 3 is empty, so this resolves
    /// command line then default. Used by the tests, which must not depend on
    /// whatever happens to be exported in the shell that runs them.
    pub fn parse_argv<I: IntoIterator<Item = String>>(&self, argv: I) -> Outcome {
        self.parse_argv_in(argv, |_| None)
    }

    /// Read `argv` against a given environment. `argv` excludes argument zero.
    ///
    /// The environment arrives as a lookup rather than being read from the
    /// process, so the whole four-layer chain is testable in-process and a
    /// test cannot be perturbed by the developer's own shell.
    pub fn parse_argv_in<I: IntoIterator<Item = String>>(
        &self,
        argv: I,
        env: impl Fn(&str) -> Option<String>,
    ) -> Outcome {
        let argv: Vec<String> = argv.into_iter().collect();

        // **Before anything else.** This is the whole original fault: with the
        // database read as argument zero, `lempi --help` bound `--help` to it
        // and started a player against a file of that name. Asked here, ahead
        // of every other rule, `--help` cannot be anything but a request for
        // help, wherever on the line it appears and whatever else is there.
        for a in &argv {
            let head = a.split('=').next().unwrap_or(a);
            if is_help(head) {
                return Outcome::Help(self.usage());
            }
            if is_version(head) {
                return Outcome::Version(self.version_line());
            }
        }

        let mut vals: Vec<(&'static str, Option<String>)> = Vec::new();
        // Anything read through the retired grammar, so the one warning below
        // can be built from what was actually typed.
        let mut retired = false;
        let mut positional = 0usize;
        let mut i = 0;
        while i < argv.len() {
            let tok = argv[i].clone();
            if tok.starts_with('-') && tok != "-" {
                let (name, inline) = match tok.split_once('=') {
                    Some((n, v)) => (n.to_string(), Some(v.to_string())),
                    None => (tok.clone(), None),
                };
                let Some((opt, was_retired)) = self.find_any(&name) else {
                    return Outcome::Usage(self.refuse(&format!("unknown option `{name}`")));
                };
                retired |= was_retired;
                if vals.iter().any(|(n, _)| *n == opt.name) {
                    return Outcome::Usage(
                        self.refuse(&format!("`{}` was given more than once", opt.name)),
                    );
                }
                if opt.arity == Arity::Flag {
                    if inline.is_some() {
                        return Outcome::Usage(
                            self.refuse(&format!("`{}` takes no value", opt.name)),
                        );
                    }
                    vals.push((opt.name, None));
                    i += 1;
                    continue;
                }
                let meta = opt.arity.meta().unwrap_or("a value");
                let raw = match inline {
                    Some(v) => {
                        i += 1;
                        v
                    }
                    None => {
                        // A value is whatever follows -- unless it *looks* like
                        // an option.
                        //
                        // **Option-shaped, not merely known** `[GDE-CLI-120]`.
                        // This used to reject only what `find_any` recognised,
                        // which closed the `--help`-as-filename fault for
                        // `--help` and left it open for every misspelling of
                        // an option: `lempi --listener --prt` bound `--prt` --
                        // a typo of `--port`, and a name this project has
                        // never defined -- as the database path and created a
                        // file called `--prt`. A reviewer produced exactly
                        // that, in this repository.
                        //
                        // So the test is shape, not membership. Nothing whose
                        // first character is `-` is ever a value here, and a
                        // user who genuinely means a path beginning with a
                        // dash can write `--listener=-odd-name`, which takes
                        // the inline branch above and is unambiguous.
                        //
                        // A bare `-` is exempt: it is the long-standing
                        // convention for standard input or output, it names
                        // no option, and refusing it would break a meaning
                        // people expect to work.
                        match argv.get(i + 1) {
                            Some(v)
                                if (v.as_str() == "-" || !v.starts_with('-'))
                                    && self
                                        .find_any(v.split('=').next().unwrap_or(v))
                                        .is_none() =>
                            {
                                i += 2;
                                v.clone()
                            }
                            _ => {
                                return Outcome::Usage(
                                    self.refuse(&format!("`{}` needs {meta} after it", opt.name)),
                                )
                            }
                        }
                    }
                };
                if let Err(e) = opt.arity.check(&raw) {
                    return Outcome::Usage(
                        self.refuse(&format!("`{} {raw}`: {e}", opt.name)),
                    );
                }
                vals.push((opt.name, Some(raw)));
                continue;
            }

            // A bare word. Anything beginning with a dash took the branch
            // above, so the transition below can never swallow a mistyped
            // option -- which is exactly how a file called `--help` came to
            // exist, and must not be able to happen again even while the
            // positional form is still read `[GDE-CLI-040]`.
            let slot = self.opts.iter().find(|o| o.was_positional == Some(positional));
            let Some(opt) = slot else {
                return Outcome::Usage(self.refuse(&format!(
                    "unexpected argument `{tok}` -- every option must be named"
                )));
            };
            positional += 1;
            if vals.iter().any(|(n, _)| *n == opt.name) {
                return Outcome::Usage(self.refuse(&format!(
                    "`{}` was given by name and again as the bare argument `{tok}`",
                    opt.name
                )));
            }
            if let Err(e) = opt.arity.check(&tok) {
                return Outcome::Usage(self.refuse(&format!("`{tok}`: {e}")));
            }
            retired = true;
            vals.push((opt.name, Some(tok)));
            i += 1;
        }

        // **One warning, carrying the line the caller should be using** --
        // built from what was actually passed, not a generic example, so
        // migrating a unit file is a copy rather than a guess `[GDE-CLI-040]`.
        // A generic "use named options" would leave whoever reads it in the
        // journal to work out the new spelling, which is exactly the step a
        // warning exists to remove.
        let warnings = if retired {
            vec![
                format!(
                    "{}: DEPRECATED command line -- this form will stop working [GDE-CLI-040].",
                    self.program
                ),
                format!("{}: use: {}", self.program, self.as_typed(&vals)),
            ]
        } else {
            Vec::new()
        };
        // **Layer 3, read once and checked now.** An environment variable
        // holding nonsense is refused exactly as `--port nonsense` is, rather
        // than being skipped over to the default -- the quiet half of
        // `[GDE-CLI-030]`, which would be quieter still coming from the
        // environment, where nobody is looking.
        let mut env_vals: Vec<(&'static str, String)> = Vec::new();
        let mut env_warnings: Vec<String> = Vec::new();
        for o in self.opts {
            if vals.iter().any(|(n, _)| *n == o.name) {
                continue;
            }
            if o.no_env {
                continue;
            }
            let name = env_var(o);
            let (name, raw) = match env(&name) {
                Some(v) => (name, v),
                None => {
                    // A retired variable still answers, and says so.
                    let found = o.env_was.iter().find_map(|old| env(old).map(|v| (*old, v)));
                    match found {
                        Some((old, v)) => {
                            env_warnings.push(format!(
                                "{}: DEPRECATED -- ${old} is being read as ${name}; \
                                 export ${name} instead [GDE-CLI-040].",
                                self.program
                            ));
                            (old.to_string(), v)
                        }
                        None => continue,
                    }
                }
            };
            if raw.is_empty() {
                continue;
            }
            if o.arity == Arity::Flag {
                // Present and not obviously a denial switches the flag on.
                if !matches!(raw.as_str(), "0" | "false" | "no" | "off") {
                    env_vals.push((o.name, String::new()));
                }
                continue;
            }
            if let Err(e) = o.arity.check(&raw) {
                return Outcome::Usage(self.refuse(&format!("${name} is `{raw}`: {e}")));
            }
            env_vals.push((o.name, raw));
        }

        // Required, asked of the whole chain rather than of the command line
        // alone: an option the environment supplies is supplied. Layer 2
        // cannot help here and is not consulted -- it is not attached yet,
        // because finding it needs these very values `[GDE-CLI-095]`.
        //
        // A `stops_early` option -- `--list-devices` -- is answered and the
        // program ends, so what it would otherwise need is not needed. The
        // same reasoning `--help` gets, declared rather than special-cased.
        let stopping = self
            .opts
            .iter()
            .any(|o| o.stops_early && vals.iter().any(|(n, _)| *n == o.name));
        if !stopping {
            for o in self.opts.iter().filter(|o| o.required) {
                let given = vals.iter().any(|(n, _)| *n == o.name)
                    || env_vals.iter().any(|(n, _)| *n == o.name)
                    || o.default.is_some();
                if !given {
                    return Outcome::Usage(self.refuse(&format!(
                        "`{}` is required -- give it, or set ${}",
                        o.name,
                        env_var(o)
                    )));
                }
            }
        }

        let mut warnings = warnings;
        warnings.extend(env_warnings);
        Outcome::Run(Args {
            vals,
            defaults: self.opts,
            env: env_vals,
            settings: Vec::new(),
            settings_attached: false,
            warnings,
        })
    }

    /// The same options, spelled the way they should be spelled now.
    fn as_typed(&self, vals: &[(&'static str, Option<String>)]) -> String {
        let mut s = self.program.to_string();
        for (n, v) in vals {
            let _ = write!(s, " {n}");
            if let Some(v) = v {
                // Quoted only where it has to be; an unquoted path is what a
                // unit file wants and what someone will paste.
                if v.contains(' ') {
                    let _ = write!(s, " \"{v}\"");
                } else {
                    let _ = write!(s, " {v}");
                }
            }
        }
        s
    }

    /// Read the real command line, act on it, and return what the program was
    /// asked to do. Prints and exits for help, version and refusal.
    pub fn parse(&self) -> Args {
        match self.parse_argv_in(std::env::args().skip(1), |k| std::env::var(k).ok()) {
            Outcome::Run(a) => {
                // Installed here, once, for all seventeen binaries: this is
                // the one point every program passes before it does anything,
                // so an eighteenth cannot forget it `[GDE-HST-070]`. Not for
                // help, version or refusal, which print and exit below --
                // the command line's own output, written directly.
                crate::logging::install(self.program);
                // Loud, on stderr, so systemd's journal carries it and
                // `journalctl -u lempi | grep DEPRECATED` is the fleet's
                // migration checklist rather than someone's memory
                // `[GDE-DEP-060]`.
                for w in &a.warnings {
                    eprintln!("{w}");
                }
                a
            }
            Outcome::Help(t) => {
                println!("{t}");
                std::process::exit(0)
            }
            Outcome::Version(t) => {
                println!("{t}");
                std::process::exit(0)
            }
            Outcome::Usage(t) => {
                eprintln!("{t}");
                std::process::exit(2)
            }
        }
    }

    /// Refuse, from inside a binary, for a condition the grammar cannot state
    /// -- `import_bundle` needing `--bundle` unless `--inventory` was asked
    /// for. Prints the same usage text an unknown option prints.
    pub fn fail(&self, why: &str) -> ! {
        eprintln!("{}", self.refuse(why));
        std::process::exit(2)
    }
}

/// Which of the four layers answered `[GDE-CLI-090]`.
///
/// Carried so a resolved value can say where it came from. A precedence chain
/// is four copies of one quantity, and this project has been bitten enough
/// times by two copies disagreeing quietly: a fallback must be visible in the
/// output rather than silently equivalent `[GOV-SRC-040]`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Source {
    /// Layer 1.
    CommandLine,
    /// Layer 2: the listener's persisted settings. Only the player has these.
    Settings,
    /// Layer 3.
    Environment,
    /// Layer 4.
    Default,
    /// No layer answered.
    Unset,
}

impl Source {
    /// How a startup line names it.
    pub fn describe(&self, o: &Opt) -> String {
        match self {
            Source::CommandLine => "the command line".to_string(),
            Source::Settings => format!("the stored setting `{}`", o.setting.unwrap_or("?")),
            Source::Environment => format!("${}", env_var(o)),
            Source::Default => "the built-in default".to_string(),
            Source::Unset => "nothing".to_string(),
        }
    }
}

/// The environment variable an option answers to `[GDE-CLI-090]`.
///
/// Derived from the long name -- `--mpd-root` is `LEMPI_MPD_ROOT` -- so there
/// is no second list of variable names to fall out of step with the options
/// `[GDE-ARC-033]`. [`Opt::env`] overrides it only where a name is already
/// established elsewhere. The scripts exported `LEMPI_PORT` long before this
/// module, so `--port` lists it as a retired spelling rather than as its name.
pub fn env_var(o: &Opt) -> String {
    match o.env_override {
        Some(n) => n.to_string(),
        // `--mpd-root` becomes `LEMPI_MPD_ROOT`. The hyphens must go: no
        // POSIX shell can assign to a name containing one, so a derivation
        // that kept them would produce variables nobody could set.
        None => format!(
            "LEMPI_{}",
            o.name.trim_start_matches('-').replace('-', "_").to_uppercase()
        ),
    }
}

/// One parsed command line, and the layers below it.
pub struct Args {
    vals: Vec<(&'static str, Option<String>)>,
    /// The declarations. Layer 4 -- the default -- is read from here rather
    /// than from a literal repeated at each call site.
    defaults: &'static [Opt],
    /// Layer 3, captured and validated at parse time so the process reads the
    /// environment once and a malformed variable is refused rather than used.
    env: Vec<(&'static str, String)>,
    /// Layer 2, absent until [`Args::with_settings`] attaches it -- which the
    /// player does only after it has opened the database the settings live
    /// in. The sixteen binaries that have no persisted settings never attach
    /// one, and that is the whole of how they "have no layer 2"
    /// `[GDE-CLI-090]`.
    settings: Vec<(&'static str, String)>,
    settings_attached: bool,
    /// Deprecation notices, printed by [`Cli::parse`] and inspectable by a
    /// test that wants to assert there were none.
    pub warnings: Vec<String>,
}

/// What an accessor may be keyed by.
///
/// Binaries key by the [`Opt`] constant itself, so the spelling of an option
/// is written down exactly once and renaming it is one edit `[GDE-ARC-033]`.
/// A bare `&str` is accepted too, for the tests and the doc-scanning guard,
/// which are given a name and have no constant to hand.
pub trait Key {
    fn key(&self) -> &str;
}
// Implemented for `&Opt` and not for `Opt`. `Opt` is `Copy`, so both would
// compile -- and then clippy reads every `args.text(&opt::PORT)` in the tree
// as a needless borrow, ninety-one of them. One impl, and the `&` is load
// bearing.
impl Key for &Opt {
    fn key(&self) -> &str {
        self.name
    }
}
impl Key for &str {
    fn key(&self) -> &str {
        self
    }
}

impl Args {
    /// **Attach layer 2**, the listener's persisted settings, by handing over
    /// a lookup for the keys options declare with [`Opt::setting`].
    ///
    /// Called *after* the database is open, because the path to it is itself
    /// an option `[GDE-CLI-095]`. Anything marked [`Opt::bootstrap`] ignores
    /// what is attached here, for exactly that reason.
    ///
    /// Returns a complaint for every stored value the option would reject.
    /// Those are ignored and resolution falls through to the layer below --
    /// said out loud rather than silently, since a setting that quietly does
    /// nothing is the failure this project keeps meeting `[GOV-SRC-040]`.
    pub fn with_settings(&mut self, lookup: impl Fn(&str) -> Option<String>) -> Vec<String> {
        let mut complaints = Vec::new();
        self.settings_attached = true;
        for o in self.defaults.iter().filter(|o| !o.bootstrap) {
            let Some(key) = o.setting else { continue };
            let Some(raw) = lookup(key) else { continue };
            let raw = match (o.setting_scale, raw.parse::<f64>()) {
                // Declared unit conversion: the store's milliseconds become
                // this option's seconds here, once, rather than in each
                // binary that happens to want the other unit.
                (Some(k), Ok(v)) => {
                    let scaled = v * k;
                    if scaled.fract() == 0.0 {
                        format!("{}", scaled as i64)
                    } else {
                        format!("{scaled}")
                    }
                }
                _ => raw,
            };
            if o.arity.accepts(&raw) {
                self.settings.push((o.name, raw));
            } else {
                complaints.push(format!(
                    "stored setting `{key}` is `{raw}`, which {} will not take; using {} instead",
                    o.name,
                    self.below_settings(o).1.describe(o)
                ));
            }
        }
        complaints
    }

    /// Whether layer 2 has been attached at all.
    pub fn has_settings(&self) -> bool {
        self.settings_attached
    }

    fn opt_of(&self, name: &str) -> Option<&'static Opt> {
        self.defaults.iter().find(|o| o.name == name)
    }

    /// Layers 3 and 4 only, for reporting what a rejected layer-2 value
    /// falls through to.
    fn below_settings(&self, o: &Opt) -> (Option<&str>, Source) {
        if let Some((_, v)) = self.env.iter().find(|(n, _)| *n == o.name) {
            return (Some(v), Source::Environment);
        }
        match o.default {
            Some(d) => (Some(d), Source::Default),
            None => (None, Source::Unset),
        }
    }

    /// **The chain, in one place** `[GDE-CLI-090]`: the command line, then the
    /// stored setting, then the environment, then the built-in default.
    ///
    /// Every option in every binary resolves through this function and no
    /// other. Nothing is implemented per option or per binary; what varies is
    /// only what the declaration says, which is the point `[GDE-ARC-033]`.
    fn resolve(&self, name: &str) -> (Option<&str>, Source) {
        if let Some((_, v)) = self.vals.iter().find(|(m, _)| *m == name) {
            // A flag's presence is its value; `has` is what reads those.
            return (v.as_deref(), Source::CommandLine);
        }
        let Some(o) = self.opt_of(name) else { return (None, Source::Unset) };
        // Layer 2 is skipped entirely for an option that is used to find
        // layer 2 -- and for every binary that never attached one.
        if !o.bootstrap {
            if let Some((_, v)) = self.settings.iter().find(|(n, _)| *n == name) {
                return (Some(v), Source::Settings);
            }
        }
        self.below_settings(o)
    }

    /// Which layer answered. For a startup line, or `--help`.
    pub fn source_of<K: Key>(&self, k: K) -> Source {
        self.resolve(k.key()).1
    }

    /// Every option whose value did **not** come from the built-in default,
    /// as a line each: what it is, what it resolved to, and which layer said
    /// so `[GOV-SRC-040]`. This is what a process prints at startup so that a
    /// surprising port is a line in the journal rather than a mystery.
    pub fn provenance(&self) -> Vec<String> {
        let mut out = Vec::new();
        for o in self.defaults {
            let (v, src) = self.resolve(o.name);
            if matches!(src, Source::Default | Source::Unset) {
                continue;
            }
            let shown = v.unwrap_or("(set)");
            out.push(format!("{} = {shown}, from {}", o.name, src.describe(o)));
        }
        out
    }

    pub fn has<K: Key>(&self, k: K) -> bool {
        let n = k.key();
        if self.vals.iter().any(|(m, _)| *m == n) {
            return true;
        }
        // A flag can also be switched on from below: a stored `"1"`/`"true"`,
        // or the environment.
        matches!(self.opt_of(n), Some(o) if o.arity == Arity::Flag)
            && matches!(self.resolve(n).1, Source::Settings | Source::Environment)
    }

    /// What the chain resolves to, or nothing.
    pub fn text<K: Key>(&self, k: K) -> Option<&str> {
        self.resolve(k.key()).0
    }

    /// For an option the spec marks `required`: what was given. The parser
    /// refuses to return an [`Args`] without it, so this cannot fail.
    pub fn need<K: Key>(&self, k: K) -> &str {
        let n = k.key();
        self.text(n)
            .unwrap_or_else(|| panic!("{n} is read as required but is not declared so"))
    }

    /// Validated at parse time, so this is never a silent fallback from a
    /// value that was present and unreadable `[GDE-CLI-030]`.
    pub fn int<K: Key>(&self, k: K) -> Option<u64> {
        self.text(k).and_then(|v| v.parse().ok())
    }

    pub fn real<K: Key>(&self, k: K) -> Option<f64> {
        self.text(k).and_then(|v| v.parse().ok())
    }

    /// [`Self::int`], for the call sites that want a `usize`.
    pub fn size<K: Key>(&self, k: K) -> Option<usize> {
        self.int(k).map(|v| v as usize)
    }

    /// For an option that declares a default, or is required: the value.
    /// Cannot be absent, and says which option is misdeclared if it ever is
    /// -- rather than falling back to a zero that would read as a real
    /// setting `[GOV-SRC-040]`.
    pub fn must_int<K: Key>(&self, k: K) -> u64 {
        let n = k.key();
        self.int(n).unwrap_or_else(|| panic!("{n} has neither a value nor a declared default"))
    }

    pub fn must_size<K: Key>(&self, k: K) -> usize {
        self.must_int(k) as usize
    }

    pub fn must_real<K: Key>(&self, k: K) -> f64 {
        let n = k.key();
        self.real(n).unwrap_or_else(|| panic!("{n} has neither a value nor a declared default"))
    }
}

pub mod specs;

#[cfg(test)]
mod tests;
