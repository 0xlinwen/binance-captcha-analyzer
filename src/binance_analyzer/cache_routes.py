"""浏览器静态资源缓存路由。"""

from __future__ import annotations

from .local_cache import get_cache_manager
from .traffic_monitor import mark_cached_url


# 可缓存的静态资源域名
CACHEABLE_DOMAINS = [
    "bin.bnbstatic.com/static",
    "public.bnbstatic.com/unpkg",
]


def handle_cache_route(route, request):
    url = request.url
    resource_type = request.resource_type
    cache_manager = get_cache_manager()
    if cache_manager:
        cached = cache_manager.get_cached(url, resource_type)
        if cached:
            mark_cached_url(url)
            route.fulfill(status=200, headers=cached["headers"], body=cached["body"])
            return
    route.continue_()


def track_cache_response(response):
    try:
        url = response.request.url
        resource_type = response.request.resource_type
        if response.status != 200:
            return
        if not any(d in url.lower() for d in CACHEABLE_DOMAINS):
            return
        if resource_type not in ("script", "stylesheet", "fetch"):
            return
        cache_manager = get_cache_manager()
        if cache_manager:
            try:
                cache_manager.save_to_cache(url, resource_type, response.body(), dict(response.headers))
            except Exception:
                pass
    except Exception:
        pass
