# lempi-hci-capture's aggregation. Every measurement in PI011 came through it
# -- the 87% deficits, the matched pair, the completeness map -- and it was
# verified once by hand against synthetic input and never again.
group hci || return 0
printf '\nhci\n'

agg() { sh "$PI/lempi-hci-capture" aggregate "$1"; }

setup
F="$VT_STATE/raw"

# btmon's real shape: a marker a second, and packet lines between them. The
# marker carries the monotonic clock because btmon's own timestamp format
# varies between versions.
cat > "$F" <<'RAW'
#T 30.10
> ACL Data TX: handle 11 flags 0x00 dlen 612
> ACL Data TX: handle 11 flags 0x00 dlen 612
> ACL Data TX: handle 11 flags 0x00 dlen 212
< HCI Event: Number of Completed Packets (0x13) plen 5
#T 31.10
> ACL Data TX: handle 11 flags 0x00 dlen 668
< HCI Event: Number of Completed Packets (0x13) plen 5
< HCI Event: Number of Completed Packets (0x13) plen 5
#T 32.10
#T 33.10
> ACL Data TX: handle 11 flags 0x00 dlen 512
RAW
OUT=$(agg "$F")
assert_eq "$(echo "$OUT" | sed -n 1p)" "30.10 3 1 1436" "counts packets, completions and bytes in a second"
assert_eq "$(echo "$OUT" | sed -n 2p)" "31.10 1 2 668" "counts a second with more completions than packets"

# **A silent second is the signature this instrument exists to find**, and it
# must be emitted as a zero row rather than skipped -- a missing row would
# read as continuous audio.
assert_eq "$(echo "$OUT" | sed -n 3p)" "32.10 0 0 0" "emits a silent second as a zero row"
assert_eq "$(echo "$OUT" | sed -n 4p)" "33.10 1 0 512" "closes the final second at end of input"
assert_eq "$(echo "$OUT" | wc -l)" "4" "emits one row per marker and no more"

# Lines that are neither a marker nor a packet must not be counted. btmon's
# output is full of them, and the capture greps before storing -- but a stray
# line reaching the file must still not become a packet.
cat > "$F" <<'RAW'
#T 10.00
> ACL Data TX: handle 11 flags 0x00 dlen 612
Bluetooth: hci0: ACL Data TX is mentioned in this log line
@ MGMT Event: Device Connected
#T 11.00
RAW
assert_eq "$(agg "$F" | sed -n 1p | cut -d' ' -f2)" "2" "counts any line naming ACL Data TX, as the capture's own filter does"

# Input with no markers at all yields nothing rather than a malformed row.
printf '> ACL Data TX: handle 11 flags 0x00 dlen 612\n' > "$F"
assert_eq "$(agg "$F")" "" "produces nothing when there are no second markers"
assert_eq "$(agg /dev/null)" "" "produces nothing from an empty capture"

# A packet line without a dlen contributes a packet but no bytes, rather than
# poisoning the byte total.
cat > "$F" <<'RAW'
#T 20.00
> ACL Data TX: handle 11 flags 0x00
> ACL Data TX: handle 11 flags 0x00 dlen 100
#T 21.00
RAW
assert_eq "$(agg "$F" | sed -n 1p)" "20.00 2 0 100" "a packet line without a dlen adds no bytes"

# The rate a reader actually computes: total bytes over elapsed time, never a
# single row `[PI3-FOUND-440]`. Two rows of 45900 a second apart are 45900 B/s.
cat > "$F" <<'RAW'
#T 100.00
> ACL Data TX: handle 11 flags 0x00 dlen 45900
#T 101.00
> ACL Data TX: handle 11 flags 0x00 dlen 45900
#T 102.00
RAW
# A row stamped T reports what was seen between T and the next marker, so the
# bytes inside a window are rows t_first..t_{last-1}, over (t_last - t_first).
# Summing the wrong end of that -- dropping the first row and keeping the last
# -- counts the right NUMBER of rows and so passes unnoticed on a long steady
# capture, while being visibly wrong on a short one.
RATE=$(agg "$F" | awk '{t[NR] = $1; b[NR] = $4}
    END {for (i = 1; i < NR; i++) s += b[i]; printf "%.0f", s/(t[NR] - t[1])}')
assert_eq "$RATE" "45900" "totals over elapsed time give the documented rate"
teardown
