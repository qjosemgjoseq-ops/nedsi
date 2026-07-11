"""Shared retry-with-exponential-backoff decorator for the ingestion scripts."""

import functools
import random
import time

import truststore

# Use the Windows certificate store for TLS verification instead of the
# bundled certifi list, so requests works behind local network/proxy setups
# the same way curl already does.
truststore.inject_into_ssl()


def with_retries(max_retries=5, base_delay=1.0, max_delay=60.0):
    """Retry a function on any exception, waiting longer (with jitter) after each failure."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    attempt += 1
                    if attempt > max_retries:
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.uniform(0, delay * 0.25)
                    print(
                        f"  [retry] {func.__name__} failed ({exc}); "
                        f"attempt {attempt}/{max_retries}, waiting {delay:.1f}s"
                    )
                    time.sleep(delay)

        return wrapper

    return decorator
