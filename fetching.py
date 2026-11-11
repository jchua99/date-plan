"""Read web replies with a limit on the total time, not just on each wait.

`urlopen(..., timeout=N)` only limits each wait for data. A slow server that
sends a byte every few seconds never trips it, so one read could take minutes
(issue #29). Reading in small pieces and checking the clock between them puts
an upper bound on the whole download.
"""

import time

CHUNK_BYTES = 16 * 1024


def read_with_deadline(response, max_bytes: int, deadline_seconds: float) -> bytes:
    """Read up to `max_bytes` of `response`, or raise TimeoutError after `deadline_seconds`.

    Each piece is read with `read1` where the response has it, which returns
    whatever has arrived rather than waiting for a full piece. So the total
    time is at most the deadline plus one socket timeout.
    """
    read = getattr(response, "read1", None) or response.read
    give_up_at = time.monotonic() + deadline_seconds
    parts, total = [], 0
    while total < max_bytes:
        if time.monotonic() > give_up_at:
            raise TimeoutError(f"reply took longer than {deadline_seconds} s")
        piece = read(min(CHUNK_BYTES, max_bytes - total))
        if not piece:
            break
        parts.append(piece)
        total += len(piece)
    return b"".join(parts)
