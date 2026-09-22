//! What the one parser guarantees, asserted across all seventeen tables.
//!
//! Three of these read files out of the repository rather than restating what
//! is in them. That is deliberate and is this project's own prescription: a
//! hand-written list that mirrors something else fails silently in the
//! direction of checking less `[GDE-ARC-031]`, so the usage text, the unit
//! files and the documentation are each pinned against the option tables
//! instead of being kept in step by hand `[GDE-ARC-033]`.

use super::specs::{self, ALL};
use super::{Arity, Cli, Outcome};

fn argv(s: &str) -> Vec<String> {
    s.split_whitespace().map(|t| t.to_string()).collect()
}

fn run(c: &Cli, line: &str) -> Outcome {
    c.parse_argv(argv(line))
}

/// The repository root, from the crate's own manifest directory.
fn repo() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

// -------------------------------------------------------------------------
// The fault this module was written for.
// -------------------------------------------------------------------------

/// **The original bug.** `./lempi --help` bound `--help` to the positional
/// database path and started a player against a file it then created; one
/// literally named `--help` was found in a checkout. Asked before any other
/// rule, it cannot be anything but a request for help.
#[test]
fn help_is_help_wherever_it_appears_and_is_never_a_file_name() {
    for line in ["--help", "-h", "--help --port 5720", "--port 5720 --help", "x.db --help"] {
        match run(&specs::lempi::SPEC, line) {
            Outcome::Help(t) => {
                assert!(t.contains("usage: lempi"), "`{line}` produced no usage text");
            }
            _ => panic!("`lempi {line}` was not read as a request for help"),
        }
    }
}

/// The same for the other sixteen, which never had `--help` at all.
#[test]
fn every_binary_answers_help_and_version() {
    for c in ALL {
        assert!(matches!(c.parse_argv(argv("--help")), Outcome::Help(_)), "{} --help", c.program);
        assert!(matches!(c.parse_argv(argv("-h")), Outcome::Help(_)), "{} -h", c.program);
        match c.parse_argv(argv("--version")) {
            Outcome::Version(v) => assert!(
                v.starts_with(c.program) && v.contains(crate::VERSION),
                "{}'s version line is `{v}`",
                c.program
            ),
            _ => panic!("{} --version", c.program),
        }
        assert!(matches!(c.parse_argv(argv("-V")), Outcome::Version(_)), "{} -V", c.program);
    }
}

/// A mistyped option is a mistyped option, never a value. This is what stops
/// the `--help` file from coming back while the positional form is still read.
#[test]
fn a_token_beginning_with_a_dash_is_never_taken_as_a_positional() {
    for line in ["--prt 5720", "--libary x.db", "-p 5720", "--follow=a --nonsense"] {
        match run(&specs::lempi::SPEC, line) {
            Outcome::Usage(t) => {
                assert!(t.contains("usage: lempi"), "`{line}` refused without usage");
            }
            _ => panic!("`lempi {line}` was accepted"),
        }
    }
}

#[test]
fn an_unknown_option_is_refused_and_says_which_one() {
    for c in ALL {
        match c.parse_argv(argv("--no-such-option")) {
            Outcome::Usage(t) => {
                assert!(t.contains("--no-such-option"), "{} did not name it", c.program);
                assert!(t.contains("usage:"), "{} printed no usage", c.program);
            }
            _ => panic!("{} accepted --no-such-option", c.program),
        }
    }
}

/// A present-but-unreadable value used to fall through to the default, which
/// is the quiet half of `[GDE-CLI-030]`: `--port nonsense` served 5720 and
/// said nothing.
#[test]
fn a_malformed_value_is_refused_rather_than_silently_defaulted() {
    assert!(matches!(run(&specs::lempi::SPEC, "--listener x --port nonsense"), Outcome::Usage(_)));
    assert!(matches!(run(&specs::lempi::SPEC, "--listener x --port -1"), Outcome::Usage(_)));
    assert!(matches!(run(&specs::mpd_fill::SPEC, "--listener a --root b --for soon"), Outcome::Usage(_)));
    // And a value that is fine still is.
    assert!(matches!(run(&specs::lempi::SPEC, "--listener x --port 5721"), Outcome::Run(_)));
}

#[test]
fn an_option_that_needs_a_value_and_has_none_is_refused() {
    assert!(matches!(run(&specs::lempi::SPEC, "--listener"), Outcome::Usage(_)));
    // Including when what follows is plainly another option, not a value.
    assert!(matches!(run(&specs::lempi::SPEC, "--listener --port 5720"), Outcome::Usage(_)));
    // And a flag given one.
    assert!(matches!(run(&specs::relink::SPEC, "--library a --audio-root b --apply=yes"), Outcome::Usage(_)));
}

#[test]
fn a_required_option_is_required() {
    for c in ALL {
        for o in c.opts.iter().filter(|o| o.required) {
            match c.parse_argv(Vec::new()) {
                Outcome::Usage(t) => assert!(
                    t.contains("is required"),
                    "{} said `{t}` rather than naming a required option",
                    c.program
                ),
                _ => panic!("{} ran with no arguments although `{}` is required", c.program, o.name),
            }
        }
    }
}

#[test]
fn both_the_spaced_and_the_equals_form_are_accepted() {
    let a = run(&specs::lempi::SPEC, "--listener x.db --port=5721");
    let b = run(&specs::lempi::SPEC, "--listener=x.db --port 5721");
    for o in [a, b] {
        match o {
            Outcome::Run(a) => {
                assert_eq!(a.text(&specs::lempi::LISTENER), Some("x.db"));
                assert_eq!(a.int(&specs::lempi::PORT), Some(5721));
            }
            _ => panic!("a well-formed line was refused"),
        }
    }
}

#[test]
fn the_same_option_twice_is_refused() {
    assert!(matches!(run(&specs::lempi::SPEC, "--listener a --listener b"), Outcome::Usage(_)));
}

// -------------------------------------------------------------------------
// The declaration is the only copy.
// -------------------------------------------------------------------------

/// An absent option reads as the default written in its own declaration, so
/// `5720` exists once in the repository rather than once per call site
/// `[GDE-ARC-033]`.
#[test]
fn an_absent_option_reads_as_the_default_its_declaration_carries() {
    let Outcome::Run(a) = run(&specs::lempi::SPEC, "--listener x.db") else {
        panic!("refused a minimal line")
    };
    assert_eq!(a.int(&specs::lempi::PORT), Some(5720));
    assert_eq!(a.int(&specs::lempi::DEPTH), Some(5));
    assert_eq!(a.int(&specs::lempi::ECHO_RATE), Some(44_100));
    assert_eq!(a.text(&specs::lempi::DEVICE), None);
    assert!(!a.has(&specs::station::LIST));
}

/// Every declared default is a value that option would itself accept. A
/// default the parser would reject is a program that cannot run bare.
#[test]
fn every_declared_default_is_a_value_that_option_accepts() {
    for c in ALL {
        for o in c.opts {
            let Some(d) = o.default else { continue };
            assert!(o.arity != Arity::Flag, "{} {} is a flag with a default", c.program, o.name);
            assert!(
                o.arity.accepts(d),
                "{}'s default for {} is `{d}`, which that option rejects",
                c.program,
                o.name
            );
        }
    }
}

/// **The usage text has no second list.** Every option the parser accepts is
/// described, and every `--name` the usage text shows is one the parser
/// accepts. Both directions, because drift has two of them.
#[test]
fn the_usage_text_and_the_parser_describe_the_same_options() {
    for c in ALL {
        let usage = c.usage();
        for o in c.opts {
            assert!(usage.contains(o.name), "{}'s usage omits {}", c.program, o.name);
            assert!(!o.help.is_empty(), "{} {} has no description", c.program, o.name);
            if let Some(m) = o.arity.meta() {
                assert!(
                    usage.contains(&format!("{} {m}", o.name)),
                    "{}'s usage does not show {} taking {m}",
                    c.program,
                    o.name
                );
            }
        }
        for word in usage.split(|ch: char| ch.is_whitespace() || ch == ',') {
            let word = word
                .trim_matches(|ch: char| !ch.is_ascii_graphic())
                .trim_end_matches(['.', ';', ':']);
            if !word.starts_with("--") || word.len() < 3 {
                continue;
            }
            // Retired spellings appear in the footnote that announces them.
            let known =
                c.find_any(word).is_some() || super::is_help(word) || super::is_version(word);
            assert!(known, "{}'s usage shows `{word}`, which it does not accept", c.program);
        }
    }
}

/// **Both forms, one declaration.** Every option has a short form and a long
/// one, written together, and within a binary no two options claim the same
/// one -- a check only possible because the options are data rather than
/// hand-written match arms `[GDE-ARC-033]`.
#[test]
fn every_option_has_a_unique_short_form() {
    for c in ALL {
        let mut seen: Vec<(&str, &str)> = Vec::new();
        for o in c.opts {
            let Some(sh) = o.short else {
                panic!("{} {} has no short form", c.program, o.name);
            };
            assert!(
                !sh.is_empty() && sh.chars().all(|ch| ch.is_ascii_alphanumeric()),
                "{} {} has the short form `-{sh}`",
                c.program,
                o.name
            );
            assert!(
                sh.len() <= 3,
                "{} {}'s short form `-{sh}` is longer than a short form should be",
                c.program,
                o.name
            );
            assert!(
                !super::short_is_reserved(sh),
                "{} {} claims `-{sh}`, which the core owns",
                c.program,
                o.name
            );
            if let Some((_, other)) = seen.iter().find(|(x, _)| *x == sh) {
                panic!("{}: {} and {other} both claim `-{sh}`", c.program, o.name);
            }
            seen.push((sh, o.name));
        }
    }
}

/// **A one-letter short form wherever it is not contested.** Multi-letter is
/// for a real clash -- `--listener` against `--library` -- and not a house
/// style `[GDE-CLI-070]`. A longer form is allowed only where its first
/// letter is already taken in that same binary.
#[test]
fn a_short_form_is_only_long_where_one_letter_is_contested() {
    for c in ALL {
        for o in c.opts.iter().filter(|o| o.short.unwrap().len() > 1) {
            let first = &o.short.unwrap()[..1];
            // Contested means more than one of this binary's own long names
            // begins with that letter, so neither can have it uncontroversially
            // -- or the core has already spent it.
            let rivals = c
                .opts
                .iter()
                .filter(|x| x.name.trim_start_matches('-').starts_with(first))
                .count();
            assert!(
                rivals > 1 || super::short_is_reserved(first),
                "{} {} is `-{}` although `-{first}` is uncontested",
                c.program,
                o.name,
                o.short.unwrap()
            );
        }
    }
}

/// And the short form reaches the same option the long one does.
#[test]
fn the_short_form_and_the_long_form_are_the_same_option() {
    for c in ALL {
        for o in c.opts {
            let sh = format!("-{}", o.short.unwrap());
            let found = c.find(&sh).expect("the short form resolves");
            assert_eq!(found.name, o.name, "{}: `{sh}` reached the wrong option", c.program);
        }
        // Both spellings appear in the usage text.
        let usage = c.usage();
        for o in c.opts {
            assert!(
                usage.contains(&format!("-{}, {}", o.short.unwrap(), o.name)),
                "{}'s usage does not show both forms of {}",
                c.program,
                o.name
            );
        }
    }
    // And a real line parses either way.
    let Outcome::Run(a) = run(&specs::lempi::SPEC, "-lis x.db -p 5721") else {
        panic!("the short forms were refused")
    };
    assert_eq!(a.text(&specs::lempi::LISTENER), Some("x.db"));
    assert_eq!(a.int(&specs::lempi::PORT), Some(5721));
}

/// **`-ab` is the option named `ab`, never `-a -b`.** Nothing bundles, so an
/// unrecognised run of letters is a mistake and is reported as one rather
/// than guessed at `[GDE-CLI-070]`.
#[test]
fn short_forms_do_not_cluster() {
    assert!(matches!(run(&specs::lempi::SPEC, "-pd x.db"), Outcome::Usage(_)));
}

#[test]
fn no_table_redeclares_what_the_parser_already_handles() {
    for c in ALL {
        for o in c.opts {
            assert!(
                !super::is_help(o.name) && !super::is_version(o.name),
                "{} declares {}, which every binary inherits",
                c.program,
                o.name
            );
            assert!(o.name.starts_with("--"), "{}'s {} is not a long name", c.program, o.name);
        }
        let mut names: Vec<&str> = c.opts.iter().map(|o| o.name).collect();
        names.sort_unstable();
        let before = names.len();
        names.dedup();
        assert_eq!(before, names.len(), "{} declares an option twice", c.program);

        // The retired positional slots must be 0..n with no gap, or a bare
        // argument would bind to the wrong option.
        let mut slots: Vec<usize> = c.opts.iter().filter_map(|o| o.was_positional).collect();
        slots.sort_unstable();
        for (i, s) in slots.iter().enumerate() {
            assert_eq!(i, *s, "{}'s retired positional slots are not dense", c.program);
        }
    }
}

/// **The table list is pinned to `src/bin/`, not maintained beside it.** A
/// binary added without a table fails here rather than shipping its own
/// parsing `[GDE-ARC-031]`.
#[test]
fn every_binary_has_exactly_one_spec() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/bin");
    let mut on_disk: Vec<String> = std::fs::read_dir(&dir)
        .expect("src/bin is readable")
        .filter_map(|e| e.ok())
        .filter_map(|e| {
            let p = e.path();
            (p.extension()? == "rs").then(|| p.file_stem()?.to_str().map(str::to_string))?
        })
        .collect();
    on_disk.sort();
    assert!(on_disk.len() >= 17, "found only {} binaries; the scan is broken", on_disk.len());

    let mut declared: Vec<String> = ALL.iter().map(|c| c.program.to_string()).collect();
    declared.sort();
    assert_eq!(on_disk, declared, "src/bin and cli::specs::ALL disagree");
}

// -------------------------------------------------------------------------
// The transition `[GDE-CLI-040]`.
// -------------------------------------------------------------------------

/// The live appliances pass the database positionally. Until every unit is
/// migrated, that must keep working -- and must say so every time, on stderr,
/// where the journal keeps it `[GDE-DEP-060]`.
#[test]
fn the_retired_positional_form_still_runs_and_warns_by_name() {
    let Outcome::Run(a) = run(
        &specs::lempi::SPEC,
        "/var/lempi/listener.db --library /srv/library/library.db --port 5720",
    ) else {
        panic!("the live lempi02w command line was refused")
    };
    assert_eq!(a.text(&specs::lempi::LISTENER), Some("/var/lempi/listener.db"));
    assert_eq!(a.text(&specs::lempi::LIBRARY), Some("/srv/library/library.db"));
    let w = a.warnings.join("\n");
    assert!(w.contains("DEPRECATED"), "the warning does not announce itself: {w}");
    // **Copy-and-paste, not a generic example.** It must carry the exact
    // corrected line for the arguments this node was actually started with,
    // so migrating a unit file needs no guess at the new spelling.
    assert!(
        w.contains(
            "lempi --listener /var/lempi/listener.db --library /srv/library/library.db \
             --port 5720"
        ),
        "the warning does not carry the corrected line: {w}"
    );
}

#[test]
fn a_bare_argument_with_no_slot_left_is_refused() {
    assert!(matches!(run(&specs::tagscan::SPEC, "a.db b.db"), Outcome::Usage(_)));
    assert!(matches!(run(&specs::lempi::SPEC, "--listener a.db also.db"), Outcome::Usage(_)));
}

#[test]
fn naming_an_option_and_also_passing_it_bare_is_refused() {
    assert!(matches!(run(&specs::lempi::SPEC, "--listener a.db"), Outcome::Run(_)));
    assert!(matches!(run(&specs::dircheck::SPEC, "--listener a.db b.db"), Outcome::Usage(_)));
}

// -------------------------------------------------------------------------
// Everything outside the crate that types one of these command lines.
// -------------------------------------------------------------------------

/// Files worth reading for an invocation: text the repository owns, minus
/// build output and vendored trees.
fn repo_text_files() -> Vec<std::path::PathBuf> {
    fn walk(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        for e in rd.filter_map(|e| e.ok()) {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            if p.is_dir() {
                if matches!(
                    name.as_str(),
                    ".git" | "node_modules" | "target" | "__pycache__" | "out" | "models"
                ) {
                    continue;
                }
                walk(&p, out);
            } else if matches!(
                p.extension().and_then(|e| e.to_str()),
                Some("md" | "sh" | "service" | "conf" | "html" | "py" | "txt")
            ) {
                out.push(p);
            }
        }
    }
    let mut out = Vec::new();
    walk(&repo(), &mut out);
    out
}

/// A command line found in a file: the spec it invokes and the arguments.
fn invocations(line: &str) -> Vec<(&'static Cli, Vec<String>)> {
    // The whole line, and each backtick span within it -- prose shows a
    // command line inside backticks and a script shows it bare.
    let mut candidates: Vec<&str> = vec![line];
    let mut rest = line;
    while let Some(a) = rest.find('`') {
        rest = &rest[a + 1..];
        match rest.find('`') {
            Some(b) => {
                candidates.push(&rest[..b]);
                rest = &rest[b + 1..];
            }
            None => break,
        }
    }
    let mut out = Vec::new();
    for cand in candidates {
        // Strip what precedes a command: a prompt, a systemd key, a path,
        // and the backticks prose wraps one in.
        let mut s = cand.trim();
        for lead in ["$ ", "ExecStart=", "ExecStartPre=", "> "] {
            s = s.strip_prefix(lead).unwrap_or(s);
        }
        let head = s.split_whitespace().next().unwrap_or("").trim_matches('`');
        let bare = head.rsplit(['/', '\\']).next().unwrap_or(head);
        let bare = bare.strip_suffix(".exe").unwrap_or(bare);
        let Some(spec) = specs::by_program(bare) else { continue };
        let args: Vec<String> =
            s.split_whitespace().skip(1).map(|t| t.to_string()).collect();
        if args.is_empty() {
            continue;
        }
        out.push((spec, args));
    }
    out
}

/// **Every option the repository's own text passes to one of these binaries
/// is an option that binary accepts.** Documentation and scripts are not kept
/// in step with the tables by hand; they are checked against them
/// `[GDE-ARC-031]`. Rename an option and this fails, naming the file.
#[test]
fn every_option_the_repository_types_is_one_the_binary_accepts() {
    let mut checked = 0usize;
    let mut wrong: Vec<String> = Vec::new();
    for path in repo_text_files() {
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        for (n, line) in text.lines().enumerate() {
            for (spec, args) in invocations(line) {
                let mut saw_option = false;
                for a in &args {
                    // A name as prose leaves it: backticked, and sometimes
                    // ending a sentence or a clause.
                    let a = a.trim_matches('`').trim_end_matches(['.', ',', ';', ')', '`']);
                    let name = a.split('=').next().unwrap_or(a);
                    if !name.starts_with("--") || name.len() < 3 {
                        continue;
                    }
                    saw_option = true;
                    let known = spec.find(name).is_some()
                        || super::is_help(name)
                        || super::is_version(name);
                    if !known {
                        wrong.push(format!(
                            "{}:{}: `{}` is not an option of {}",
                            path.display(),
                            n + 1,
                            name,
                            spec.program
                        ));
                    }
                }
                if saw_option {
                    checked += 1;
                }
            }
        }
    }
    // A scan that reads nothing must not pass by finding no fault
    // `[GDE-ECHO-547]`.
    assert!(checked >= 8, "only {checked} invocations found; the scan is broken");
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
}

/// **A value that is option-shaped is refused, even when the option does not
/// exist** `[GDE-CLI-120]`.
///
/// The `--help`-as-filename fault was believed closed and was not. The
/// lookahead rejected only tokens `find_any` recognised, so `--help` could
/// no longer become a database path but every *misspelling* still could:
/// `lempi --listener --prt` bound `--prt` -- a typo of `--port`, a name this
/// project has never defined -- and created a file called `--prt` in the
/// repository. A reviewer produced exactly that, which is the second time
/// this shape has left a file named after an option lying around; there is
/// one on `lempi02w` dated August, and one was found on `smartboardpc`.
///
/// So the test is shape, not membership: nothing beginning with `-` is a
/// value. The two deliberate exemptions are pinned here too, because both
/// are ways a real path can legitimately start with a dash.
#[test]
fn an_option_shaped_value_is_never_swallowed_as_a_path() {
    let spec = &specs::lempi::SPEC;
    let argv = |s: &str| s.split_whitespace().map(str::to_string).collect::<Vec<_>>();

    // The fault as reproduced: an unknown option-shaped token.
    for bad in ["--listener --prt", "--listener --nope", "--listener -x"] {
        match spec.parse_argv(argv(bad)) {
            Outcome::Usage(u) => assert!(
                u.contains("needs PATH"),
                "`{bad}` refused, but not for needing a value: {}",
                u.lines().next().unwrap_or("")
            ),
            _ => panic!("`{bad}` was accepted -- `{}` became a filename again",
                        bad.split_whitespace().nth(1).unwrap()),
        }
    }

    // A known option following is refused the same way, as it always was.
    assert!(matches!(spec.parse_argv(argv("--listener --port")), Outcome::Usage(_)));

    // **Exemption 1: a bare `-`** is the standard-input convention and names
    // no option. Refusing it would break a meaning people expect.
    match spec.parse_argv(argv("--listener -")) {
        Outcome::Run(a) => assert_eq!(a.text(&specs::lempi::LISTENER), Some("-")),
        _ => panic!("a bare `-` should still be usable as a path"),
    }

    // **Exemption 2: the inline form** is unambiguous, so a path that really
    // does start with a dash has a way to be said.
    match spec.parse_argv(argv("--listener=-odd-name.db")) {
        Outcome::Run(a) => {
            assert_eq!(a.text(&specs::lempi::LISTENER), Some("-odd-name.db"))
        }
        _ => panic!("`--listener=-odd-name.db` is the escape hatch and must work"),
    }
}

/// **Every option read with `must_*` can actually answer** `[GDE-CLI-115]`.
///
/// `must_real`/`must_int`/`must_size` panic when nothing supplies a value —
/// no command line, no stored setting, no environment, no declared default.
/// That is a startup crash on an appliance, not a usage message, and it is
/// exactly what shipped: `common::INTERVAL_S` carried no `.or(..)` while its
/// sibling `INTERVAL_MS` did, so `mpd_watch` panicked every time and
/// `mpd_fill`/`mpd_direct` panicked whenever the listener database had no
/// saved settings. `cargo test` never caught it because those three are
/// behind the `mpd` feature and the default run does not compile them.
///
/// So this reads the source of `src/bin/` rather than the binaries — the
/// same technique as `every_binary_has_a_table` above, and it works whether
/// or not the feature is enabled. A `must_*` call names its option constant,
/// so the name is recoverable from the text.
///
/// The invariant is narrow on purpose: an option read with `must_*` must
/// have a **declared default** or be **required**. A stored setting is not
/// enough — `load_settings` answers `None`, not defaults, when the table is
/// empty, which is precisely how two of the three failures happened.
#[test]
fn every_option_read_as_mandatory_can_answer() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/bin");
    let mut checked = 0usize;
    let mut bad: Vec<String> = Vec::new();
    for entry in std::fs::read_dir(&dir).expect("src/bin is readable") {
        let path = entry.expect("readable entry").path();
        if path.extension().and_then(|e| e.to_str()) != Some("rs") {
            continue;
        }
        let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_string();
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        let Some(spec) = specs::ALL.iter().find(|s| s.program == stem) else { continue };
        for (n, line) in text.lines().enumerate() {
            for call in ["must_real(", "must_int(", "must_size("] {
                let Some(rest) = line.split(call).nth(1) else { continue };
                // `args.must_real(&opt::INTERVAL)` -> `INTERVAL`
                let named = rest
                    .trim_start_matches(['&', ' '])
                    .split(')').next().unwrap_or("")
                    .rsplit("::").next().unwrap_or("")
                    .trim();
                if named.is_empty() {
                    continue;
                }
                let Some(opt) = spec.opts.iter().find(|o| {
                    o.name.trim_start_matches("--").replace('-', "_").eq_ignore_ascii_case(named)
                }) else { continue };
                checked += 1;
                if opt.default.is_none() && !opt.required {
                    bad.push(format!(
                        "{}:{}: `{}` is read with {call}..) but has no default and is not required -- it will panic when nothing supplies it",
                        path.display(), n + 1, opt.name));
                }
            }
        }
    }
    assert!(checked >= 5, "only {checked} mandatory reads matched a declaration; the scan is broken");
    assert!(bad.is_empty(), "{}", bad.join("
"));
}

/// **Every `ExecStart` the repository carries is a command line the player
/// accepts today, with nothing deprecated left in it.** These are the lines a
/// human copies onto an appliance; a typo in one is a speaker that does not
/// come back `[GDE-DEP-070]`.
#[test]
fn every_exec_start_in_the_repository_is_a_current_lempi_command_line() {
    let mut found = 0usize;
    let mut bad: Vec<String> = Vec::new();
    for path in repo_text_files() {
        // **`fleet/` is captured reality, not shipped configuration**
        // `[GDE-CLI-045]`. It holds `systemctl cat` output taken from the
        // live appliances, and those are still on the deprecated positional
        // form until somebody migrates them -- that is the whole point of
        // keeping it, and of the DEPRECATED line the player logs. Asserting
        // that a photograph of today matches the convention would make this
        // test fail *because* the migration is outstanding, which is exactly
        // backwards. The directory is untracked, so it is absent on a clean
        // clone and this only ever mattered on a machine that had captured.
        if path.components().any(|c| c.as_os_str() == "fleet") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        for (n, line) in text.lines().enumerate() {
            let t = line.trim();
            let Some(cmd) = t.strip_prefix("ExecStart=") else { continue };
            let head = cmd.split_whitespace().next().unwrap_or("");
            if head.rsplit('/').next() != Some("lempi") {
                continue;
            }
            found += 1;
            let args: Vec<String> =
                cmd.split_whitespace().skip(1).map(|t| t.to_string()).collect();
            let where_ = format!("{}:{}", path.display(), n + 1);
            match specs::lempi::SPEC.parse_argv(args) {
                Outcome::Run(a) if a.warnings.is_empty() => {}
                Outcome::Run(a) => bad.push(format!("{where_}: {}", a.warnings.join("; "))),
                Outcome::Usage(u) => {
                    bad.push(format!("{where_}: {}", u.lines().next().unwrap_or("refused")))
                }
                _ => bad.push(format!("{where_}: does not start a player")),
            }
        }
    }
    assert!(found >= 3, "only {found} lempi ExecStart lines found; the scan is broken");
    assert!(bad.is_empty(), "{}", bad.join("\n"));
}

// -------------------------------------------------------------------------
// The four-layer chain `[GDE-CLI-090]`.
// -------------------------------------------------------------------------

/// A stub environment, so a test says what layer 3 holds instead of
/// inheriting whatever the developer's shell happens to export.
fn with_env(c: &Cli, line: &str, env: &[(&str, &str)]) -> Outcome {
    let env: Vec<(String, String)> =
        env.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
    c.parse_argv_in(argv(line), |k| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone()))
}

/// **The order, end to end.** One option, four layers, each winning over the
/// ones below it and losing to the ones above.
#[test]
fn the_four_layers_resolve_highest_first() {
    use super::Source;
    let spec = &specs::lempi::SPEC;
    let port = &specs::lempi::PORT;

    // 4: nothing said, so the declared default.
    let Outcome::Run(a) = with_env(spec, "--listener x.db", &[]) else { panic!() };
    assert_eq!(a.int(port), Some(super::as_number(crate::default_port!())));
    assert_eq!(a.source_of(port), Source::Default);

    // 3: the environment beats the default.
    let Outcome::Run(a) = with_env(spec, "--listener x.db", &[("LEMPI_PORT", "13491")]) else {
        panic!()
    };
    assert_eq!(a.int(port), Some(13491));
    assert_eq!(a.source_of(port), Source::Environment);

    // 2: a stored setting beats the environment.
    let Outcome::Run(mut a) = with_env(spec, "--listener x.db", &[("LEMPI_DEPTH", "9")]) else {
        panic!()
    };
    assert_eq!(a.int(&specs::lempi::DEPTH), Some(9));
    assert!(a.with_settings(|k| (k == "queue_depth").then(|| "11".to_string())).is_empty());
    assert_eq!(a.int(&specs::lempi::DEPTH), Some(11));
    assert_eq!(a.source_of(&specs::lempi::DEPTH), Source::Settings);

    // 1: the command line beats everything.
    let Outcome::Run(mut a) = with_env(spec, "--listener x.db --depth 3", &[("LEMPI_DEPTH", "9")])
    else {
        panic!()
    };
    a.with_settings(|k| (k == "queue_depth").then(|| "11".to_string()));
    assert_eq!(a.int(&specs::lempi::DEPTH), Some(3));
    assert_eq!(a.source_of(&specs::lempi::DEPTH), Source::CommandLine);
}

/// **Bootstrap** `[GDE-CLI-095]`. `--listener` names the database the stored
/// settings live in, so it must not be answerable by them -- and is not, even
/// when a setting of that name somehow exists.
#[test]
fn an_option_used_to_find_the_settings_never_reads_them() {
    use super::Source;
    for c in ALL {
        for o in c.opts.iter().filter(|o| o.bootstrap) {
            assert!(
                o.setting.is_none(),
                "{} {} is bootstrap and also declares a stored setting",
                c.program,
                o.name
            );
        }
        // Both halves of the database pair are bootstrap in every binary
        // that takes them.
        for name in ["--listener", "--library"] {
            let Some(o) = c.find(name) else { continue };
            assert!(o.bootstrap, "{} {name} is not marked bootstrap", c.program);
        }
    }
    // And attaching settings cannot change one.
    let Outcome::Run(mut a) = run(&specs::lempi::SPEC, "--listener from-cli.db") else { panic!() };
    a.with_settings(|_| Some("from-settings.db".to_string()));
    assert_eq!(a.text(&specs::lempi::LISTENER), Some("from-cli.db"));
    assert_eq!(a.source_of(&specs::lempi::LISTENER), Source::CommandLine);
}

/// Sixteen binaries have no layer 2. That is expressed by never attaching
/// one, not by sixteen special cases.
#[test]
fn a_binary_with_no_stored_settings_resolves_through_the_other_three() {
    use super::Source;
    let Outcome::Run(a) = with_env(&specs::tagscan::SPEC, "", &[("LEMPI_LIBRARY", "x.db")]) else {
        panic!("the environment did not satisfy a required option")
    };
    assert!(!a.has_settings());
    assert_eq!(a.text(&specs::tagscan::LIBRARY), Some("x.db"));
    assert_eq!(a.source_of(&specs::tagscan::LIBRARY), Source::Environment);
}

/// The variable name is computed from the long name and written nowhere
/// `[GDE-ARC-033]`. Hyphens become underscores, because no POSIX shell can
/// assign to a name containing one.
#[test]
fn the_environment_variable_is_derived_from_the_long_name() {
    assert_eq!(super::env_var(&specs::lempi::PORT), "LEMPI_PORT");
    assert_eq!(super::env_var(&specs::lempi::MPD_ROOT), "LEMPI_MPD_ROOT");
    assert_eq!(super::env_var(&specs::lempi::ECHO_FLEET_MIN), "LEMPI_ECHO_FLEET_MIN_FRAMES");
    assert_eq!(super::env_var(&specs::lempi::LIST_DEVICES), "LEMPI_LIST_DEVICES");
    for c in ALL {
        for o in c.opts {
            let v = super::env_var(o);
            assert!(
                !v.contains('-'),
                "{} {} derives `{v}`, which no shell can set",
                c.program,
                o.name
            );
            assert!(
                v.chars().all(|ch| ch.is_ascii_uppercase() || ch.is_ascii_digit() || ch == '_'),
                "{} {} derives `{v}`",
                c.program,
                o.name
            );
        }
    }
}

/// The usage text names no environment variable that is not this binary's
/// own. A worked example is useful; a stale one teaches the wrong name, and
/// a `LEMPI_`-prefixed leftover sat in this footer until it was derived.
#[test]
fn the_usage_text_names_only_real_environment_variables() {
    for c in ALL {
        let usage = c.usage();
        for word in usage.split_whitespace() {
            let Some(v) = word.strip_prefix('$') else { continue };
            let v = v.trim_end_matches([',', '.', ')', ';']);
            assert!(
                c.opts.iter().any(|o| super::env_var(o) == v),
                "{}'s usage names ${v}, which is no option of its own",
                c.program
            );
        }
    }
}

/// **One long name, one meaning, across all seventeen** `[GDE-CLI-110]`.
///
/// Sharing a name for a shared concept is the point of [`specs::common`] and
/// is expected: `--library` means the same thing to nine binaries. What is
/// forbidden is one name meaning two things -- a different unit, a different
/// value type, a different concept -- because the environment variable is
/// derived from the name, and a machine-wide `LEMPI_INTERVAL` that meant
/// five seconds to three binaries and five milliseconds to a fourth is a
/// trap with a script's name on it. `mpd_session` takes `--interval-ms` for
/// exactly that reason.
///
/// A per-binary **default** or a differently worded help line is local
/// specialisation, not a conflict, and is deliberately not checked here.
#[test]
fn one_long_name_never_means_two_things() {
    let mut seen: Vec<(&str, &'static super::Opt)> = Vec::new();
    let mut clashes: Vec<String> = Vec::new();
    for c in ALL {
        for o in c.opts {
            let Some((other_prog, other)) = seen.iter().find(|(_, x)| x.name == o.name) else {
                seen.push((c.program, o));
                continue;
            };
            // Meaning, as far as a machine can see it: what the value is,
            // what unit the store keeps it in, and whether the environment
            // may supply it at all. Defaults and wording may differ.
            let same = other.arity == o.arity
                && other.setting == o.setting
                && other.setting_scale == o.setting_scale
                && other.no_env == o.no_env
                && super::env_var(other) == super::env_var(o);
            if !same {
                clashes.push(format!(
                    "`{}` means different things to {} and {other_prog}:
                         {:?}, setting {:?}, scale {:?}, ${}
                         {:?}, setting {:?}, scale {:?}, ${}",
                    o.name,
                    c.program,
                    o.arity,
                    o.setting,
                    o.setting_scale,
                    super::env_var(o),
                    other.arity,
                    other.setting,
                    other.setting_scale,
                    super::env_var(other)
                ));
            }
        }
    }
    assert!(clashes.is_empty(), "{}", clashes.join("

"));
}

/// **The invariant above holds by construction, not by vigilance.**
///
/// A shared name must come from one shared declaration in [`specs::common`].
/// This reads the tables' own source and fails if any long name is built
/// twice by a separate `Opt::text`/`int`/`real`/`flag` -- which is what a
/// future author does when they copy a declaration instead of sharing it,
/// and is how the two copies would begin to differ `[GDE-ARC-031]`.
#[test]
fn a_shared_long_name_is_one_declaration_not_two() {
    let src = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/cli/specs.rs"),
    )
    .expect("the tables are readable");
    let mut built: Vec<(String, usize)> = Vec::new();
    for (n, line) in src.lines().enumerate() {
        let Some(at) = line.find("Opt::") else { continue };
        let rest = &line[at..];
        if !["Opt::text(", "Opt::int(", "Opt::real(", "Opt::flag("]
            .iter()
            .any(|k| rest.starts_with(k))
        {
            continue;
        }
        // The long name is the first string literal of the construction,
        // here or on the line below it.
        let tail: String = src.lines().skip(n).take(2).collect::<Vec<_>>().join(" ");
        let Some(q) = tail.find("\"--") else { continue };
        let after = &tail[q + 1..];
        let Some(e) = after.find('"') else { continue };
        built.push((after[..e].to_string(), n + 1));
    }
    assert!(built.len() >= 20, "only {} constructions found; the scan is broken", built.len());
    let mut dupes: Vec<String> = Vec::new();
    for (i, (name, line)) in built.iter().enumerate() {
        if let Some((_, first)) = built[..i].iter().find(|(m, _)| m == name) {
            dupes.push(format!(
                "`{name}` is constructed twice, at specs.rs:{first} and specs.rs:{line} --                  share one declaration in `common` instead"
            ));
        }
    }
    assert!(dupes.is_empty(), "{}", dupes.join("
"));
}

/// An option that makes a program *act* is not reachable from the
/// environment `[GDE-CLI-105]`.
///
/// **Widened after review, and the original rule was drawn too narrowly.**
/// It guarded the two flags that *write* -- an exported `LEMPI_APPLY` turning
/// reporting into writing at a distance -- and missed every other flag that
/// changes what a program DOES rather than how it does it. A reviewer
/// demonstrated the two that matter:
///
/// - `LEMPI_LIST_DEVICES=1` makes the player print its devices and exit 0.
///   Under `Restart=always` that is a restart loop that looks like a clean
///   shutdown every time.
/// - `LEMPI_FOLLOW=somehost` re-points a speaker at a different master and,
///   because the option SEEDS the stored setting, keeps doing it at every
///   restart `[SPEC-ECHO-030]`.
///
/// The others are the same shape: `--all` re-reads every file, `--quick`
/// "VERIFIES NOTHING", `--calibrate` forces recalibration on a headless
/// panel, `--then-handoff` stops MPD, `--inventory` and `--list` replace the
/// run with a report.
///
/// The distinction is write-versus-read only by accident; the real one is
/// **act versus configure**. A value that tunes a run is fine from the
/// environment -- that is what layer 3 is for. A switch that changes what the
/// run *is* must be visible on the command line that started it.
#[test]
fn a_flag_that_acts_cannot_be_switched_on_by_the_environment() {
    const ACTS: &[&str] = &[
        "--apply", "--write", "--list-devices", "--list", "--all",
        "--quick", "--inventory", "--calibrate", "--then-handoff", "--follow",
    ];
    let mut seen = 0usize;
    for c in ALL {
        for o in c.opts.iter().filter(|o| ACTS.contains(&o.name)) {
            seen += 1;
            assert!(o.no_env, "{} {} is reachable from the environment", c.program, o.name);
        }
    }
    assert!(seen >= ACTS.len(), "only {seen} acting options found; the list has gone stale");
    let Outcome::Run(a) =
        with_env(&specs::relink::SPEC, "--library a --audio-root b", &[("LEMPI_APPLY", "1")])
    else {
        panic!()
    };
    assert!(!a.has(&specs::relink::APPLY), "the environment switched --apply on");
}

/// A malformed environment value is refused, exactly as a malformed command
/// line value is -- and more importantly than one, since nobody is looking at
/// the environment `[GDE-CLI-030]`.
#[test]
fn a_malformed_environment_value_is_refused_and_names_the_variable() {
    match with_env(&specs::lempi::SPEC, "--listener x.db", &[("LEMPI_PORT", "nonsense")]) {
        Outcome::Usage(t) => assert!(t.contains("LEMPI_PORT"), "the refusal does not name it: {t}"),
        _ => panic!("a nonsense LEMPI_PORT was accepted"),
    }
}

/// A stored value the option would reject falls through, and says so rather
/// than silently doing nothing `[GOV-SRC-040]`.
#[test]
fn a_stored_setting_that_cannot_be_read_complains_and_falls_through() {
    use super::Source;
    let Outcome::Run(mut a) = run(&specs::lempi::SPEC, "--listener x.db") else { panic!() };
    let said = a.with_settings(|k| (k == "queue_depth").then(|| "lots".to_string()));
    assert_eq!(said.len(), 1, "a rejected setting said nothing");
    assert!(said[0].contains("queue_depth"), "{}", said[0]);
    assert_eq!(a.source_of(&specs::lempi::DEPTH), Source::Default);
}

/// A declared unit conversion keeps an option on the chain rather than
/// pushing the conversion into the binary `[GDE-CLI-090]`.
#[test]
fn a_scaled_setting_arrives_in_the_options_own_unit() {
    let Outcome::Run(mut a) = run(&specs::mpd_fill::SPEC, "--listener a --root b") else {
        panic!()
    };
    a.with_settings(|k| (k == "sample_interval_ms").then(|| "2500".to_string()));
    assert_eq!(
        a.real(&specs::mpd_fill::INTERVAL),
        Some(2.5),
        "milliseconds did not become seconds"
    );
}

/// **Which layer answered is sayable** `[GOV-SRC-040]`. A precedence chain is
/// four copies of one quantity; a value that cannot name its own source is
/// how two of them come to disagree unnoticed.
#[test]
fn the_resolved_layer_is_reportable() {
    let Outcome::Run(a) =
        with_env(&specs::lempi::SPEC, "--listener x.db --port 13491", &[("LEMPI_DEPTH", "9")])
    else {
        panic!()
    };
    let lines = a.provenance().join("\n");
    assert!(lines.contains("--port = 13491, from the command line"), "{lines}");
    assert!(lines.contains("--depth = 9, from $LEMPI_DEPTH"), "{lines}");
    // What came from the default is not reported: the point is what is
    // surprising, not a wall of everything.
    assert!(!lines.contains("--echo-rate"), "{lines}");
}

// The test that exercised `Opt::env_formerly` end to end lived here. It is
// gone with the retired variable it was written around: no option declares a
// former name now, and every spec in this module resolves against the real
// `specs::lempi::SPEC` rather than a synthetic one, so there is nothing left
// for it to assert against. The mechanism itself is still there and still
// documented `[GDE-CLI-040]`; the next option to retire a name should bring
// this test back with it.

/// **The default port has one definition** `[GDE-CLI-100]`. It was written
/// out in eight places; this holds the line in the crate's own source.
#[test]
fn the_default_port_is_written_once_in_the_source() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut found: Vec<String> = Vec::new();
    fn walk(d: &std::path::Path, out: &mut Vec<String>) {
        let Ok(rd) = std::fs::read_dir(d) else { return };
        for e in rd.filter_map(|e| e.ok()) {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
            } else if p.extension().and_then(|x| x.to_str()) == Some("rs") {
                // This file is where the chain is exercised by name, so it
                // necessarily writes ports out; it defines nothing.
                if p.ends_with("tests.rs") {
                    continue;
                }
                let Ok(t) = std::fs::read_to_string(&p) else { continue };
                for (n, line) in t.lines().enumerate() {
                    let trimmed = line.trim();
                    // A comment may well mention a port; what must not be
                    // duplicated is a definition. And a test module's
                    // fixtures are neither -- `engine` parses host strings
                    // like `speaker:5720` in its own tests.
                    if trimmed.contains("#[cfg(test)]") {
                        break;
                    }
                    if trimmed.starts_with("//") {
                        continue;
                    }
                    if trimmed.contains(crate::default_port!()) {
                        out.push(format!("{}:{}: {}", p.display(), n + 1, trimmed));
                    }
                }
            }
        }
    }
    walk(&dir, &mut found);
    assert_eq!(
        found.len(),
        1,
        "the default port appears {} times in the crate source, not once:\n{}",
        found.len(),
        found.join("\n")
    );
    // And the one occurrence is the macro's own body, in the core.
    assert!(found[0].contains("cli.rs"), "the one definition is not in cli.rs: {}", found[0]);

    // Two mirrors outside the crate, because a shell script and a Python
    // module cannot read a Rust macro. They are pinned here rather than
    // trusted `[GDE-CLI-100]`, and there are exactly two.
    let want = crate::default_port!();
    for (rel, marker) in
        [("build/lib-defaults.sh", "LEMPI_DEFAULT_PORT="), ("tools/lempi_control.py", "LEMPI_DEFAULT_PORT = ")]
    {
        let p = repo().join(rel);
        let t = std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{rel}: {e}"));
        let line = t
            .lines()
            .find(|l| l.trim_start().starts_with(marker))
            .unwrap_or_else(|| panic!("{rel} no longer defines {marker}"));
        let got = line.trim_start().trim_start_matches(marker).trim();
        assert_eq!(got, want, "{rel} says {got}, the crate says {want}");
    }

    // And nowhere else under build/ or tools/ writes it out.
    let mut strays: Vec<String> = Vec::new();
    for rel in ["build", "tools"] {
        let dir = repo().join(rel);
        let mut files = Vec::new();
        collect(&dir, &mut files);
        for f in files {
            let Ok(t) = std::fs::read_to_string(&f) else { continue };
            for (n, line) in t.lines().enumerate() {
                if !line.contains(want) {
                    continue;
                }
                let c = line.trim_start();
                if c.starts_with("LEMPI_DEFAULT_PORT") || c.starts_with('#') || c.starts_with("//")
                {
                    continue;
                }
                // Only a *default* counts. A worked example in a docstring
                // -- `python tools/echo_skew.py speaker-a speaker-b:PORT` --
                // names a port without defining one, and rewriting prose to
                // satisfy a guard teaches the guard to be ignored.
                let defines = c.contains("default")
                    || c.contains(":-")
                    || c.contains("= 5720")
                    || c.contains("=5720");
                if !defines {
                    continue;
                }
                strays.push(format!("{}:{}: {}", f.display(), n + 1, c));
            }
        }
    }
    assert!(
        strays.is_empty(),
        "the default port is written out again outside the crate:
{}",
        strays.join("
")
    );
}

/// Files under a directory, for the guard above.
fn collect(d: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
    let Ok(rd) = std::fs::read_dir(d) else { return };
    for e in rd.filter_map(|e| e.ok()) {
        let p = e.path();
        if p.is_dir() {
            if e.file_name() == "__pycache__" {
                continue;
            }
            collect(&p, out);
        } else if matches!(
            p.extension().and_then(|x| x.to_str()),
            Some("sh" | "py" | "html" | "js")
        ) {
            out.push(p);
        }
    }
}
