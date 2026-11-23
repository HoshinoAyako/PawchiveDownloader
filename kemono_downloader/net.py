from __future__ import annotations

from typing import Any

from .settings import AppSettings


def build_network_options(settings: AppSettings | None) -> dict[str, Any]:
    """把设置转换成 aiohttp.ClientSession 的额外参数。

    - http / https：交给 aiohttp 自带代理（https 表示与代理之间走 TLS）
    - socks5：使用 aiohttp_socks 的 ProxyConnector
    """
    if settings is None or not settings.uses_proxy:
        return {}

    url = settings.proxy_url()
    if not url:
        return {}

    if settings.proxy_type == "socks5":
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
            raise RuntimeError("缺少 aiohttp-socks 依赖，无法使用 SOCKS5 代理") from exc
        return {"connector": ProxyConnector.from_url(url)}

    return {"proxy": url}


__all__ = ["build_network_options"]
