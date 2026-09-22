# The systemd units `setup-appliance.sh` writes. Static checks -- no stubs, no
# hardware -- over the single source of truth for a rebuilt card.
group units || return 0
printf '\nunits\n'

setup
SETUP="$PI/setup-appliance.sh"

# **`[PI3-FOUND-670]` Every `Type=oneshot` needs a ceiling.** systemd defaults
# `TimeoutStartSec` to infinity for oneshot services, so a unit that blocks is
# never killed -- and a timer cannot fire while its last run is still going.
# Measured on the appliance: the keeper sat in `activating` for minutes after
# the speaker holding the audio was switched off, timer active, service not
# failed, nothing logged, because a stuck process logs nothing. This check
# exists so the next oneshot added here cannot inherit that.
MISSING=$(awk '
    /^install_unit /     { unit = $2; oneshot = 0; timeout = 0 }
    /^Type=oneshot/      { oneshot = 1 }
    /^TimeoutStartSec=/  { timeout = 1 }
    /^EOF$/              { if (oneshot && !timeout) print unit; oneshot = 0 }
' "$SETUP")
if [ -z "$MISSING" ]; then
    ok "every Type=oneshot unit has a finite TimeoutStartSec"
else
    bad "every Type=oneshot unit has a finite TimeoutStartSec" "missing on: $(echo "$MISSING" | tr '\n' ' ')"
fi

# A ceiling of zero or `infinity` is the same as none at all, written in a way
# that looks deliberate.
INFINITE=$(grep -n '^TimeoutStartSec=\(0\|infinity\)$' "$SETUP" || true)
if [ -z "$INFINITE" ]; then
    ok "no unit disables its start timeout explicitly"
else
    bad "no unit disables its start timeout explicitly" "$INFINITE"
fi

# The keeper's ceiling must clear its own tick budget, or systemd kills work
# that was going to finish. The budget is 25 s `[PI3-FOUND-670]`.
KEEPER_TIMEOUT=$(awk '/^install_unit lempi-speaker.service/,/^EOF$/' "$SETUP" |
    sed -n 's/^TimeoutStartSec=//p')
if [ -n "$KEEPER_TIMEOUT" ] && [ "$KEEPER_TIMEOUT" -gt 25 ]; then
    ok "the keeper's timeout (${KEEPER_TIMEOUT}s) clears its tick budget"
else
    bad "the keeper's timeout clears its tick budget" "got '${KEEPER_TIMEOUT:-none}', budget is 25s"
fi

# Every unit the setup installs must name a program that ships beside it --
# a unit pointing at a helper nobody installed fails only at boot.
#
# **`ExecStartPre` counts, and the first version of this check could not see
# it**: `^ExecStart=` cannot match `ExecStartPre=`, so the two helpers that
# run before the player -- the one that recovers a power-cut database and the
# one that reports whether recovery is even possible `[PI-PRE-010]` -- went
# unchecked. systemd treats a missing `ExecStartPre` as a failed start, and
# with `Restart=always` that is a boot loop: strictly worse than the case this
# was written for, and invisible to it.
BADEXEC=""
for prog in $(sed -n 's|^ExecStart\(Pre\)\{0,1\}=/usr/local/bin/\([a-z-]*\).*|\2|p' "$SETUP" | sort -u); do
    # `lempi` itself is the compiled player, cross-built and deployed
    # separately; everything else is a script that ships beside this setup.
    [ "$prog" = lempi ] && continue
    [ -f "$PI/$prog" ] || [ -f "$PI/$prog.sh" ] || BADEXEC="$BADEXEC $prog"
done
if [ -z "$BADEXEC" ]; then
    ok "every unit's ExecStart/ExecStartPre names a helper that ships here"
else
    bad "every unit's ExecStart/ExecStartPre names a helper that ships here" "missing:$BADEXEC"
fi
teardown
