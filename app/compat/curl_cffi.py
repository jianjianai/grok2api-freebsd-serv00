"""
curl_cffi compatibility exports.

Use the real curl_cffi package when available. On unsupported platforms
such as FreeBSD, fall back to a small aiohttp-based subset that matches
the APIs used by this project.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse


_FORCE_FALLBACK = os.getenv("GROK2API_FORCE_CURL_CFFI_FALLBACK", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


if not _FORCE_FALLBACK:
    try:
        from curl_cffi import CurlError
        from curl_cffi.const import CurlOpt
        from curl_cffi.requests import AsyncSession
        from curl_cffi.requests.errors import RequestsError
        from curl_cffi.requests.exceptions import (
            ConnectionError,
            DNSError,
            ProxyError,
            SSLError,
        )

        CURL_CFFI_BACKEND = "curl_cffi"
    except Exception:
        _FORCE_FALLBACK = True


if _FORCE_FALLBACK:
    import aiohttp

    try:
        from aiohttp_socks import ProxyConnector
    except Exception:  # pragma: no cover - optional dependency
        ProxyConnector = None

    CURL_CFFI_BACKEND = "aiohttp-fallback"

    class CurlError(Exception):
        """Fallback base transport error."""

    class RequestsError(CurlError):
        """Fallback request error."""

    class ConnectionError(RequestsError):
        """Fallback connection error."""

    class DNSError(ConnectionError):
        """Fallback DNS resolution error."""

    class ProxyError(ConnectionError):
        """Fallback proxy error."""

    class SSLError(ConnectionError):
        """Fallback SSL error."""

    class CurlOpt:
        """Subset of CurlOpt keys referenced by the project."""

        PROXY_SSL_VERIFYPEER = "PROXY_SSL_VERIFYPEER"
        PROXY_SSL_VERIFYHOST = "PROXY_SSL_VERIFYHOST"

    class _AiohttpResponse:
        def __init__(
            self,
            response: aiohttp.ClientResponse,
            *,
            stream: bool,
            owned_session: Optional[aiohttp.ClientSession] = None,
        ) -> None:
            self._response = response
            self._stream = stream
            self._owned_session = owned_session
            self._body: Optional[bytes] = None
            self._closed = False

        @property
        def status_code(self) -> int:
            return self._response.status

        @property
        def headers(self):
            return self._response.headers

        @property
        def content(self) -> bytes:
            return self._body or b""

        async def _finalize(self) -> None:
            if self._closed:
                return
            self._closed = True
            try:
                self._response.release()
            except Exception:
                try:
                    self._response.close()
                except Exception:
                    pass
            if self._owned_session is not None:
                try:
                    await self._owned_session.close()
                except Exception:
                    pass
                self._owned_session = None

        async def read(self) -> bytes:
            if self._body is None:
                self._body = await self._response.read()
            await self._finalize()
            return self._body

        async def text(self) -> str:
            body = await self.read()
            charset = self._response.charset or "utf-8"
            return body.decode(charset, errors="replace")

        def json(self) -> Any:
            body = self._body or b""
            if not body:
                return {}
            try:
                return json.loads(body.decode("utf-8"))
            except UnicodeDecodeError:
                return json.loads(body)

        async def aiter_content(
            self, chunk_size: int = 65536
        ) -> AsyncIterator[bytes]:
            try:
                async for chunk in self._response.content.iter_chunked(chunk_size):
                    if chunk:
                        yield chunk
            finally:
                await self._finalize()

        async def aiter_lines(self) -> AsyncIterator[str]:
            buffer = ""
            async for chunk in self.aiter_content():
                text = chunk.decode("utf-8", errors="replace")
                buffer += text
                while True:
                    index = buffer.find("\n")
                    if index == -1:
                        break
                    line = buffer[:index]
                    if line.endswith("\r"):
                        line = line[:-1]
                    yield line
                    buffer = buffer[index + 1 :]
            if buffer:
                yield buffer.rstrip("\r")

        async def close(self) -> None:
            await self._finalize()

        async def aclose(self) -> None:
            await self._finalize()

    class AsyncSession:
        """Small subset of curl_cffi AsyncSession backed by aiohttp."""

        def __init__(self, **kwargs: Any) -> None:
            self._default_kwargs = dict(kwargs)
            self._session = aiohttp.ClientSession(trust_env=False)

        def _resolve_proxy(self, url: str, kwargs: dict[str, Any]) -> Optional[str]:
            proxy = kwargs.get("proxy")
            if proxy:
                return proxy
            proxies = kwargs.get("proxies") or {}
            if isinstance(proxies, dict):
                scheme = urlparse(url).scheme.lower()
                return proxies.get(scheme) or proxies.get("all")
            return None

        async def _request(self, method: str, url: str, **kwargs: Any):
            request_kwargs = dict(self._default_kwargs)
            request_kwargs.update(kwargs)

            stream = bool(request_kwargs.pop("stream", False))
            request_kwargs.pop("impersonate", None)
            request_kwargs.pop("http_version", None)
            request_kwargs.pop("curl_options", None)

            timeout = request_kwargs.pop("timeout", None)
            if timeout is not None and not isinstance(timeout, aiohttp.ClientTimeout):
                request_kwargs["timeout"] = aiohttp.ClientTimeout(total=float(timeout))

            proxy_url = self._resolve_proxy(url, request_kwargs)
            request_kwargs.pop("proxy", None)
            request_kwargs.pop("proxies", None)

            verify = request_kwargs.pop("verify", None)
            if verify is False:
                request_kwargs["ssl"] = False

            owned_session = None
            session = self._session

            if proxy_url and urlparse(proxy_url).scheme.lower().startswith("socks"):
                if ProxyConnector is None:
                    raise ProxyError("SOCKS proxy requires aiohttp-socks")
                owned_session = aiohttp.ClientSession(
                    connector=ProxyConnector.from_url(proxy_url),
                    trust_env=False,
                )
                session = owned_session
            elif proxy_url:
                request_kwargs["proxy"] = proxy_url

            try:
                response = await session.request(method, url, **request_kwargs)
                wrapped = _AiohttpResponse(
                    response, stream=stream, owned_session=owned_session
                )
                if not stream:
                    await wrapped.read()
                return wrapped
            except aiohttp.ClientConnectorDNSError as exc:
                if owned_session is not None:
                    await owned_session.close()
                raise DNSError(str(exc)) from exc
            except aiohttp.ClientProxyConnectionError as exc:
                if owned_session is not None:
                    await owned_session.close()
                raise ProxyError(str(exc)) from exc
            except aiohttp.ClientSSLError as exc:
                if owned_session is not None:
                    await owned_session.close()
                raise SSLError(str(exc)) from exc
            except aiohttp.ClientConnectionError as exc:
                if owned_session is not None:
                    await owned_session.close()
                raise ConnectionError(str(exc)) from exc
            except aiohttp.ClientError as exc:
                if owned_session is not None:
                    await owned_session.close()
                raise RequestsError(str(exc)) from exc
            except asyncio.TimeoutError as exc:
                if owned_session is not None:
                    await owned_session.close()
                raise ConnectionError("Request timed out") from exc

        async def get(self, url: str, **kwargs: Any):
            return await self._request("GET", url, **kwargs)

        async def post(self, url: str, **kwargs: Any):
            return await self._request("POST", url, **kwargs)

        async def close(self) -> None:
            await self._session.close()

        async def __aenter__(self) -> "AsyncSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            await self.close()


__all__ = [
    "AsyncSession",
    "ConnectionError",
    "CurlError",
    "CurlOpt",
    "CURL_CFFI_BACKEND",
    "DNSError",
    "ProxyError",
    "RequestsError",
    "SSLError",
]

