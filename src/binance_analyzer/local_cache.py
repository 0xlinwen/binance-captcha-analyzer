"""
本地缓存管理器：拦截请求，缓存静态资源到本地
解决浏览器缓存不生效的问题
"""

import hashlib
import os
import json
import time
from pathlib import Path
from urllib.parse import urlparse


class LocalCacheManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "local_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.cache_dir / "index.json"
        self.index = self._load_index()
        self.stats = {
            "hits": 0,
            "misses": 0,
            "blocked": 0,
            "bytes_saved": 0,
        }

    def _load_index(self):
        """加载缓存索引"""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_index(self):
        """保存缓存索引"""
        try:
            with open(self.index_file, "w") as f:
                json.dump(self.index, f)
        except Exception:
            pass

    def _get_cache_key(self, url: str) -> str:
        """生成缓存键（去除查询参数中的时间戳等动态部分）"""
        parsed = urlparse(url)
        # 只用路径部分生成 key，忽略大部分查询参数
        clean_url = f"{parsed.netloc}{parsed.path}"
        return hashlib.md5(clean_url.encode()).hexdigest()

    def _get_cache_path(self, cache_key: str) -> Path:
        """获取缓存文件路径"""
        return self.cache_dir / cache_key

    def _is_cacheable(self, url: str, resource_type: str) -> bool:
        """判断资源是否可缓存"""
        # 缓存静态资源：script、stylesheet、fetch（JS动态加载）
        if resource_type not in ("script", "stylesheet", "fetch"):
            return False

        url_lower = url.lower()

        # 不缓存动态 API
        if "/api/" in url_lower or "/bapi/" in url_lower:
            return False

        # 不缓存验证码相关
        captcha_keywords = ["captcha", "puzzle", "slider", "challenge", "geetest"]
        if any(kw in url_lower for kw in captcha_keywords):
            return False

        # 缓存静态资源 CDN
        cacheable_domains = [
            "bin.bnbstatic.com/static",
            "public.bnbstatic.com/unpkg",
        ]
        if any(domain in url_lower for domain in cacheable_domains):
            return True

        return False

    def get_cached(self, url: str, resource_type: str):
        """获取缓存的资源"""
        if not self._is_cacheable(url, resource_type):
            return None

        cache_key = self._get_cache_key(url)
        cache_path = self._get_cache_path(cache_key)

        if cache_key in self.index and cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    body = f.read()
                self.stats["hits"] += 1
                self.stats["bytes_saved"] += len(body)
                return {
                    "body": body,
                    "headers": self.index[cache_key].get("headers", {}),
                }
            except Exception:
                pass

        self.stats["misses"] += 1
        return None

    def save_to_cache(self, url: str, resource_type: str, body: bytes, headers: dict):
        """保存资源到缓存"""
        if not self._is_cacheable(url, resource_type):
            return

        cache_key = self._get_cache_key(url)
        cache_path = self._get_cache_path(cache_key)

        try:
            with open(cache_path, "wb") as f:
                f.write(body)

            # 只保存必要的响应头
            saved_headers = {}
            for key in ["content-type", "content-encoding"]:
                if key in headers:
                    saved_headers[key] = headers[key]

            self.index[cache_key] = {
                "url": url[:200],
                "headers": saved_headers,
                "size": len(body),
                "time": time.time(),
            }
            self._save_index()
        except Exception:
            pass

    def print_stats(self):
        """打印缓存统计"""
        total = self.stats["hits"] + self.stats["misses"]
        if total > 0:
            hit_rate = self.stats["hits"] / total * 100
            saved_mb = self.stats["bytes_saved"] / 1024 / 1024
            print(f"\n本地缓存统计: 命中 {self.stats['hits']}/{total} ({hit_rate:.1f}%), 节省 {saved_mb:.2f}MB")


# 全局实例
_cache_manager = None


def get_cache_manager(cache_dir: Path = None) -> LocalCacheManager:
    """获取缓存管理器单例"""
    global _cache_manager
    if _cache_manager is None and cache_dir:
        _cache_manager = LocalCacheManager(cache_dir)
    return _cache_manager


def init_cache_manager(cache_dir: Path):
    """初始化缓存管理器"""
    global _cache_manager
    _cache_manager = LocalCacheManager(cache_dir)
    return _cache_manager
