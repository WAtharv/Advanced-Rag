"""TLS trust configuration for corporate networks.

On a machine behind a TLS-inspecting proxy, the certifi bundle that `httpx` and
`requests` ship with does not contain the proxy's signing CA, so downloading the
embedding models fails with CERTIFICATE_VERIFY_FAILED even though the browser on
the same machine is fine. `truststore` makes Python validate against the OS trust
store instead, which is where the corporate CA already is.

Called from the entry points (CLI, API, smoke script) rather than at package
import, so importing the library never mutates global SSL behaviour.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_APPLIED = False


def enable_system_trust_store() -> bool:
    """Route TLS verification through the OS trust store. Returns True if applied.

    Set `RAG_DISABLE_TRUSTSTORE=1` to skip it, e.g. if you would rather point
    `SSL_CERT_FILE` at an explicit CA bundle.
    """
    global _APPLIED
    if _APPLIED or os.environ.get("RAG_DISABLE_TRUSTSTORE") == "1":
        return _APPLIED

    try:
        import truststore
    except ImportError:
        logger.debug("truststore not installed - using the default certifi bundle")
        return False

    truststore.inject_into_ssl()
    _APPLIED = True
    logger.info("TLS verification is using the system trust store")
    return True
