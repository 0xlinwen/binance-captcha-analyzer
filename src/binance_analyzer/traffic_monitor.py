"""
流量监控工具：统计每个请求的流量消耗
"""

from collections import defaultdict


class TrafficMonitor:
    def __init__(self):
        self.requests = []
        self.total_bytes = 0
        self.cached_bytes = 0  # 从本地缓存返回的流量
        self.by_type = defaultdict(int)
        self.by_domain = defaultdict(int)
        self._cached_urls = set()  # 记录从缓存返回的 URL

    def mark_cached(self, url):
        """标记 URL 为缓存命中"""
        self._cached_urls.add(url)

    def on_response(self, response):
        """响应回调，记录流量"""
        try:
            request = response.request
            url = request.url
            resource_type = request.resource_type

            headers = response.headers
            content_length = int(headers.get("content-length", 0))

            if content_length == 0:
                try:
                    body = response.body()
                    content_length = len(body) if body else 0
                except Exception:
                    pass

            request_size = 500
            total_size = request_size + content_length

            from urllib.parse import urlparse
            domain = urlparse(url).netloc

            is_cached = url in self._cached_urls

            self.requests.append({
                "url": url[:100],
                "type": resource_type,
                "domain": domain,
                "size": total_size,
                "response_size": content_length,
                "cached": is_cached,
            })

            self.total_bytes += total_size
            if is_cached:
                self.cached_bytes += total_size
            else:
                self.by_type[resource_type] += total_size
                self.by_domain[domain] += total_size

        except Exception:
            pass

    def print_summary(self):
        """打印流量统计摘要"""
        network_bytes = self.total_bytes - self.cached_bytes

        print(f"\n{'='*60}")
        print(f"流量统计摘要")
        print(f"{'='*60}")
        print(f"实际网络流量: {self._format_size(network_bytes)}")
        print(f"本地缓存命中: {self._format_size(self.cached_bytes)}")
        print(f"请求数: {len(self.requests)} (网络: {sum(1 for r in self.requests if not r['cached'])}, 缓存: {sum(1 for r in self.requests if r['cached'])})")

        if network_bytes == 0:
            return

        print(f"\n按资源类型 (仅网络流量):")
        sorted_types = sorted(self.by_type.items(), key=lambda x: x[1], reverse=True)
        for rtype, size in sorted_types:
            pct = size / network_bytes * 100 if network_bytes > 0 else 0
            print(f"  {rtype:15} {self._format_size(size):>10} ({pct:.1f}%)")

        print(f"\n按域名 (仅网络流量, Top 10):")
        sorted_domains = sorted(self.by_domain.items(), key=lambda x: x[1], reverse=True)[:10]
        for domain, size in sorted_domains:
            pct = size / network_bytes * 100 if network_bytes > 0 else 0
            print(f"  {domain[:40]:40} {self._format_size(size):>10} ({pct:.1f}%)")

        print(f"\n网络流量最大的请求 (Top 20):")
        network_requests = [r for r in self.requests if not r["cached"]]
        sorted_requests = sorted(network_requests, key=lambda x: x["size"], reverse=True)[:20]
        for req in sorted_requests:
            print(f"  [{req['type']:10}] {self._format_size(req['size']):>10} {req['url'][:60]}")

    def _format_size(self, size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes/1024:.1f}KB"
        else:
            return f"{size_bytes/1024/1024:.2f}MB"

    def reset(self):
        self.requests = []
        self.total_bytes = 0
        self.cached_bytes = 0
        self.by_type = defaultdict(int)
        self.by_domain = defaultdict(int)
        self._cached_urls = set()


# 全局实例
traffic_monitor = TrafficMonitor()


def enable_traffic_monitor(page):
    page.on("response", traffic_monitor.on_response)


def print_traffic_summary():
    traffic_monitor.print_summary()


def reset_traffic_monitor():
    traffic_monitor.reset()


def mark_cached_url(url):
    """标记 URL 为缓存命中"""
    traffic_monitor.mark_cached(url)
