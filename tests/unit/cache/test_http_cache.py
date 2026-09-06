"""Tests for HTTP response cache."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import FileLock

from apm_cli.cache.http_cache import (
    MAX_HTTP_CACHE_TTL_SECONDS,
    HttpCache,
)
from apm_cli.cache.locking import atomic_land


class TestHttpCacheHitMiss:
    """Test basic cache hit/miss behavior."""

    def test_miss_returns_none(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        result = cache.get("https://registry.example.com/api/servers/test")
        assert result is None

    def test_store_and_hit(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        url = "https://registry.example.com/api/servers/test"
        body = b'{"name": "test-server"}'
        headers = {"Cache-Control": "max-age=3600", "ETag": '"abc123"'}

        cache.store(url, body, headers=headers)
        entry = cache.get(url)

        assert entry is not None
        assert entry.body == body
        assert entry.etag == '"abc123"'

    def test_expired_entry_returns_none(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        url = "https://registry.example.com/api/servers/expired"
        body = b'{"name": "expired"}'
        headers = {"Cache-Control": "max-age=1"}

        cache.store(url, body, headers=headers)
        # Manually expire by patching the meta file
        import hashlib

        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        meta_path = tmp_path / "http_v1" / url_hash / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["expires_at"] = time.time() - 100
        meta_path.write_text(json.dumps(meta))

        result = cache.get(url)
        assert result is None


class TestHttpCacheConditionalRevalidation:
    """Test ETag-based conditional revalidation."""

    def test_conditional_headers_with_etag(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        url = "https://registry.example.com/api/servers/test"
        cache.store(url, b"body", headers={"ETag": '"v1"', "Cache-Control": "max-age=3600"})

        headers = cache.conditional_headers(url)
        assert headers == {"If-None-Match": '"v1"'}

    def test_conditional_headers_no_entry(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        headers = cache.conditional_headers("https://not-cached.example.com/foo")
        assert headers == {}

    def test_refresh_expiry_on_304(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        url = "https://registry.example.com/api/servers/test"
        cache.store(url, b"body", headers={"ETag": '"v1"', "Cache-Control": "max-age=1"})

        # Expire it
        import hashlib

        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        meta_path = tmp_path / "http_v1" / url_hash / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["expires_at"] = time.time() - 100
        meta_path.write_text(json.dumps(meta))

        # Refresh on 304
        cache.refresh_expiry(url, headers={"Cache-Control": "max-age=3600", "ETag": '"v2"'})

        # Should be valid again
        entry = cache.get(url)
        assert entry is not None
        assert entry.body == b"body"


class TestHttpCacheTTLCap:
    """Test that max-age is capped at MAX_HTTP_CACHE_TTL_SECONDS."""

    def test_max_age_capped(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        url = "https://registry.example.com/api/long-lived"
        # Server says cache for 7 days
        headers = {"Cache-Control": "max-age=604800"}
        cache.store(url, b"body", headers=headers)

        import hashlib

        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        meta_path = tmp_path / "http_v1" / url_hash / "meta.json"
        meta = json.loads(meta_path.read_text())

        # Should be capped at 24h from store time
        max_expiry = meta["stored_at"] + MAX_HTTP_CACHE_TTL_SECONDS
        assert meta["expires_at"] <= max_expiry + 1  # +1 for timing slack


class TestHttpCacheSizeCap:
    """Test LRU eviction when size cap is exceeded."""

    def test_empty_first_store_avoids_descendant_scan(self, tmp_path: Path) -> None:
        with patch("apm_cli.cache.http_cache.os.scandir", wraps=os.scandir) as scan:
            cache = HttpCache(tmp_path)
            cache.store("https://example.com/first", b"first")
            scanned = [Path(call.args[0]) for call in scan.call_args_list]
            assert scanned == [tmp_path / "http_v1", tmp_path / "http_v1"]

            scan.reset_mock()
            cache.store("https://example.com/second", b"second")
            scan.assert_not_called()

        first = cache.get("https://example.com/first")
        second = cache.get("https://example.com/second")
        assert first is not None and first.body == b"first"
        assert second is not None and second.body == b"second"

    @pytest.mark.parametrize("initialize_before_first_store", [False, True])
    def test_first_store_counts_other_instances(
        self, tmp_path: Path, initialize_before_first_store: bool
    ) -> None:
        first = HttpCache(tmp_path)
        second = HttpCache(tmp_path) if initialize_before_first_store else None
        old_url = "https://example.com/old"
        new_url = "https://example.com/new"
        first.store(old_url, b"a" * 1000)
        os.utime(first._entry_path(old_url), (1, 1))
        if second is None:
            second = HttpCache(tmp_path)

        with (
            patch("apm_cli.cache.http_cache.MAX_HTTP_CACHE_BYTES", 2400),
            patch("apm_cli.cache.http_cache.os.scandir", wraps=os.scandir) as scan,
        ):
            second.store(new_url, b"b" * 1000)
        assert any(Path(call.args[0]) == first._entry_path(old_url) for call in scan.call_args_list)
        assert second.get_stats()["total_size_bytes"] <= 2400
        assert second.get(old_url) is None
        new_entry = second.get(new_url)
        assert new_entry is not None and new_entry.body == b"b" * 1000

    @pytest.mark.parametrize("prime_cache", [False, True])
    def test_large_metadata_counts_toward_cap(self, tmp_path: Path, prime_cache: bool) -> None:
        cache = HttpCache(tmp_path)
        if prime_cache:
            cache.store("https://example.com/seed", b"seed")
        url = "https://example.com/large-metadata"
        with patch("apm_cli.cache.http_cache.MAX_HTTP_CACHE_BYTES", 2400):
            cache.store(url, b"x", headers={"ETag": "e" * 3000})
        assert cache.get_stats()["total_size_bytes"] <= 2400
        assert cache.get(url) is None

    @pytest.mark.parametrize("replace_after_landing", [False, True])
    def test_first_store_counts_concurrent_same_key_winner(
        self, tmp_path: Path, replace_after_landing: bool
    ) -> None:
        cache = HttpCache(tmp_path)
        other = HttpCache(tmp_path)
        url = "https://example.com/shared"
        landing_results: list[bool] = []

        def interleave(staged: Path, final: Path, lock: FileLock) -> bool:
            """Publish a real competing entry immediately around our landing."""
            if replace_after_landing:
                landed = atomic_land(staged, final, lock)
            with (
                patch("apm_cli.cache.http_cache.atomic_land", wraps=atomic_land),
                patch.object(other, "_enforce_size_cap"),
            ):
                other.store(url, b"w" * 4000)
            if not replace_after_landing:
                landed = atomic_land(staged, final, lock)
            landing_results.append(landed)
            return landed

        with (
            patch("apm_cli.cache.http_cache.MAX_HTTP_CACHE_BYTES", 2400),
            patch("apm_cli.cache.http_cache.atomic_land", side_effect=interleave),
        ):
            cache.store(url, b"small")
        assert landing_results == [replace_after_landing]
        assert cache.get_stats()["total_size_bytes"] <= 2400
        assert cache.get(url) is None

    def test_nonempty_initialization_cleans_staging(self, tmp_path: Path) -> None:
        staged = tmp_path / "http_v1" / "old.inc.12345678"
        staged.mkdir(parents=True)
        (staged / "body").write_bytes(b"stale")
        cache = HttpCache(tmp_path)
        assert not staged.exists()
        with patch("apm_cli.cache.http_cache.os.scandir", wraps=os.scandir) as scan:
            cache.store("https://example.com/first", b"first")
        assert any(
            Path(call.args[0]).parent == tmp_path / "http_v1" for call in scan.call_args_list
        )

    def test_first_store_stat_error_falls_back_to_scan(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        url = "https://example.com/stat-error"
        body_path = cache._entry_path(url) / "body"
        real_stat = Path.stat
        failed = False

        def fail_once(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
            """Simulate a file disappearing during the live-size probe."""
            nonlocal failed
            if path == body_path and not follow_symlinks and not failed:
                failed = True
                raise FileNotFoundError("concurrent replacement")
            return real_stat(path, follow_symlinks=follow_symlinks)

        with (
            patch("apm_cli.cache.http_cache.MAX_HTTP_CACHE_BYTES", 500),
            patch.object(Path, "stat", fail_once),
        ):
            cache.store(url, b"x" * 1000)
        assert failed
        assert cache.get_stats()["total_size_bytes"] <= 500
        assert cache.get(url) is None

    def test_eviction_on_size_cap(self, tmp_path: Path) -> None:
        # Use a very small cap for testing
        with patch("apm_cli.cache.http_cache.MAX_HTTP_CACHE_BYTES", 500):
            cache = HttpCache(tmp_path)

            # Store entries that exceed 500 bytes total
            for i in range(20):
                url = f"https://registry.example.com/api/entry/{i}"
                body = b"x" * 100  # 100 bytes each
                cache.store(url, body, headers={"Cache-Control": "max-age=3600"})
                # Small delay to ensure different mtimes for LRU
                time.sleep(0.01)

            # Some entries should have been evicted
            stats = cache.get_stats()
            assert stats["total_size_bytes"] <= 1000  # Generous bound


class TestHttpCacheClean:
    """Test cache cleaning."""

    def test_clean_removes_all(self, tmp_path: Path) -> None:
        cache = HttpCache(tmp_path)
        cache.store(
            "https://example.com/1",
            b"body1",
            headers={"Cache-Control": "max-age=3600"},
        )
        cache.store(
            "https://example.com/2",
            b"body2",
            headers={"Cache-Control": "max-age=3600"},
        )

        cache.clean_all()
        stats = cache.get_stats()
        assert stats["entry_count"] == 0
