"""web/nginx.conf + web/Dockerfile load-speed settings: caching must never loosen the security headers or let the
auth_request cache answer for a request without a session."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = (ROOT / "web" / "nginx.conf").read_text()
DOCKERFILE = (ROOT / "web" / "Dockerfile").read_text()


def _block(start: str) -> str:
    i = CONF.index(start)
    depth, j = 0, CONF.index("{", i)
    for k in range(j, len(CONF)):
        depth += {"{": 1, "}": -1}.get(CONF[k], 0)
        if depth == 0:
            return CONF[i:k + 1]
    raise AssertionError(start)


def test_compression_on():
    for d in ("gzip on;", "gzip_static on;", "gzip_vary on;"):
        assert d in CONF
    types = re.search(r"gzip_types ([^;]+);", CONF).group(1).split()
    assert {"application/json", "application/javascript", "text/css", "image/svg+xml"} <= set(types)
    assert "gzip -9" in DOCKERFILE and ".gz" in DOCKERFILE


def test_immutable_only_for_versioned_shell_files_and_fonts():
    m = _block("map $arg_v $shell_cc")
    assert '""       "no-cache"' in m and "immutable" in m
    assert 'add_header Cache-Control "no-cache" always;' in _block("location / {")          # index.html revalidates
    assert "Cache-Control $shell_cc" in _block("location ~* ^/[A-Za-z0-9_.-]+\\.(?:js|css)$")
    assert "immutable" in _block("location /fonts/")
    assert "immutable" not in _block("location /api/") and "private" in _block("location /api/")


def test_shell_locations_keep_security_headers():
    csp = re.search(r'set \$csp "([^"]+)"', CONF).group(1)
    assert "script-src 'self';" in csp and "object-src 'none'" in csp
    for loc in ("location / {", "location ~* ^/[A-Za-z0-9_.-]+\\.(?:js|css)$", "location /fonts/"):
        b = _block(loc)
        for h in ("Content-Security-Policy $csp", "X-Content-Type-Options", "X-Frame-Options", "Strict-Transport-Security",
                  "Referrer-Policy", "Permissions-Policy"):
            assert h in b, (loc, h)
    # /fonts/ must not be ^~ (that would skip the dotfile / database 404 rules)
    assert "location ^~ /fonts/" not in CONF


def test_auth_cache_is_short_and_needs_a_session():
    b = _block("location = /_auth")
    assert "proxy_cache cs_auth;" in b
    valid = re.findall(r"proxy_cache_valid ([^;]+);", b)
    assert valid == ["204 5s"]                                   # only the yes answer, a few seconds
    key = re.search(r'proxy_cache_key "([^"]+)"', b).group(1)
    for part in ("$cookie_cs_session", "$remote_addr", "$http_x_forwarded_for", "$http_cf_connecting_ip"):
        assert part in key
    assert "proxy_no_cache $auth_nocache;" in b and "proxy_cache_bypass $auth_nocache;" in b
    assert '""       1;' in _block("map $cookie_cs_session $auth_nocache")
    # the cache lives on the web container's writable tmpfs (read-only root filesystem)
    assert "proxy_cache_path /var/cache/nginx/" in CONF
