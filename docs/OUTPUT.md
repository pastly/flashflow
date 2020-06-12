# Per-second results

The FlashFlow coordinator outputs the raw per-second results it receives from
all parties for all measurements into a single file. You should setup `logrotate`
or similar for this file, but that's not what we're talking about here.

A BEGIN line signals the start of data for the measurement of a relay. An END
line signals the end. Between these lines there are zero or more result lines,
each with a per-second result from either a measurer measuring that relay or
that relay reporting the amount of background traffic it saw that second.

## BEGIN line

    <fp> <time> BEGIN

Where:

- `fp`: the fingerprint of the relay this BEGIN message is for.
- `time`: the integer unix timestamp at which active measurement began.

Example:

    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979504 BEGIN

## END line

    <fp> <time> END

Where:

- `fp`: the fingerprint of the relay this END message is for.
- `time`: the integer unix timestamp at which active measurement ended.

Example:

    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979534 END

## Results line

    <fp> <time> <is_bg> GIVEN=<given> TRUSTED=<trusted>

Where:

- `fp`: the fingerprint of the relay.
- `time`: the integer unix timestamp at which this result was received.
- `is_bg`: 'BG' if this result is a report from the relay on the number of
  background bytes it saw in the last second, or 'MEASR' if this is a result
from a measurer
- `given`: the number of bytes reported
- `trusted`: if a bg report from the relay, the maximum `given` is trusted to
  be; or if a measurer result, then the same as `given`.

Both `given` and `trusted` are in bytes. Yes, for measurer lines it is
redundant.

Background traffic reports from the relay include the raw actual reported value
in `given`; if the relay is malicious and claims 8 TiB of background traffic in
the last second, you will see that here. `trusted` is the **max** that `given`
can be. When reading results from this file, use `min(given, trusted)` as the
trusted number of background bytes this second.

Example:

    # bg report from relay, use GIVEN b/c less than TRUSTED
    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979083 BG GIVEN=744904 TRUSTED=1659029
    # bg report from relay, use TRUSTED b/c less than GIVEN
    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979042 BG GIVEN=671858 TRUSTED=50960
    # result from measurer, always trusted
    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979083 MEASR GIVEN=5059082 TRUSTED=5059082
