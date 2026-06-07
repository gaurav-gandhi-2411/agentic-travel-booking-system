from __future__ import annotations

import os


def init_sentry() -> None:
    """Initialise Sentry SDK if SENTRY_DSN is set; no-op otherwise.

    Wires the FastAPI integration for automatic exception capture.
    DSN is injected via environment variable — never hardcoded.

    The lazy import of sentry_sdk is intentional: the SDK has a non-trivial
    startup cost (thread spawning, monkey-patching) and we only pay it when
    a DSN is actually configured.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    import sentry_sdk  # noqa: PLC0415
    from sentry_sdk.integrations.fastapi import FastApiIntegration  # noqa: PLC0415
    from sentry_sdk.integrations.starlette import StarletteIntegration  # noqa: PLC0415

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
    )
