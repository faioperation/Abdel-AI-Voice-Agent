"""
Global HTTP client pool.

A single AsyncClient per upstream service is created at FastAPI startup and
reused for the lifetime of the process.  This eliminates the per-request cost
of DNS lookup + TCP handshake + TLS negotiation (~150–300 ms each).

keepalive_expiry=30.0 keeps idle connections warm across voice-call turn gaps
(user thinking / speaking) which routinely exceed httpx's default 5 s expiry.
Without this, the TLS session tears down mid-call and the next turn pays the
full handshake cost again.
"""
import httpx
import logging

logger = logging.getLogger(__name__)

_openai_client: httpx.AsyncClient | None = None
_vapi_client: httpx.AsyncClient | None = None


def get_openai_client() -> httpx.AsyncClient:
    if _openai_client is None:
        raise RuntimeError("OpenAI client not initialised — call http_client.startup() first.")
    return _openai_client


def get_vapi_client() -> httpx.AsyncClient:
    if _vapi_client is None:
        raise RuntimeError("Vapi client not initialised — call http_client.startup() first.")
    return _vapi_client


async def startup() -> None:
    global _openai_client, _vapi_client

    _openai_client = httpx.AsyncClient(
        base_url="https://api.openai.com",
        timeout=httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0),
        limits=httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
            keepalive_expiry=30.0,   # keep warm across voice-turn gaps (default is 5 s)
        ),
        http2=True,  # requires 'h2' package — see requirements.txt
    )

    _vapi_client = httpx.AsyncClient(
        base_url="https://api.vapi.ai",
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        limits=httpx.Limits(
            max_keepalive_connections=10,
            max_connections=20,
            keepalive_expiry=30.0,
        ),
    )

    logger.info("HTTP connection pools initialised (OpenAI + Vapi)")


async def shutdown() -> None:
    global _openai_client, _vapi_client
    if _openai_client:
        await _openai_client.aclose()
        logger.info("OpenAI connection pool closed")
    if _vapi_client:
        await _vapi_client.aclose()
        logger.info("Vapi connection pool closed")
