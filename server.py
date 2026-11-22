        return _wsgi_json(
            start_response, 502,
            {"error": "Cloudflare error (" + str(exc.code) + "): " + error_message(detail)[:300]},
        )
    except Exception as exc:
        return _wsgi_json(
            start_response, 502,
            {"error": "Image generation failed: " + str(exc)[:250]},
        )


def _mpoxel_wsgi_application(environ, start_response):
    """Core WSGI app. CORS is applied by the public wrapper below."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = urllib.parse.unquote(environ.get("PATH_INFO", "/") or "/")

    if method == "POST":
        if path == "/api/chat":
            return _wsgi_chat(environ, start_response)
        if path == "/api/image-prompt":
            return _wsgi_image_prompt(environ, start_response)
        if path == "/api/generate-image":
            return _wsgi_image_generation(environ, start_response)
        return _wsgi_json(start_response, 404, {"error": "Endpoint not found"})

    if method not in ("GET", "HEAD"):
        return _wsgi_json(start_response, 405, {"error": "Method not allowed"})

    # Render health check. Keep this independent of API credentials so the
    # platform can verify that the web process is alive after deployment.
    if path == "/health":
        return _wsgi_json(start_response, 200, {"status": "ok", "service": "mpoxel-api"})

    # Serve local files only as a fallback. In this deployment the frontend
    # lives on mpoxel.in; the API is hosted separately on PythonAnywhere.
    relative = "index.html" if path == "/" else path.lstrip("/")
    target = (PUBLIC_DIR / relative).resolve()
    try:
        target.relative_to(PUBLIC_DIR.resolve())
    except ValueError:
        return _wsgi_json(start_response, 404, {"error": "File not found"})

    blocked_suffixes = {
        ".py", ".pyc", ".pyo", ".env", ".ini", ".toml",
        ".yaml", ".yml", ".sh", ".bat", ".ps1",
    }
    if any(part.startswith(".") for part in target.relative_to(PUBLIC_DIR.resolve()).parts):
        return _wsgi_json(start_response, 404, {"error": "File not found"})
    if target.suffix.lower() in blocked_suffixes or not target.is_file():
        return _wsgi_json(start_response, 404, {"error": "File not found"})

    content_type = __import__("mimetypes").guess_type(str(target))[0] or "application/octet-stream"
    try:
        size = target.stat().st_size
        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(size)),
                ("Cache-Control", "no-cache" if target.name == "index.html" else "public, max-age=3600"),
            ],
        )
        if method == "HEAD":
            return [b""]
        return [target.read_bytes()]
    except OSError:
        return _wsgi_json(start_response, 500, {"error": "Unable to read requested file."})


# The browser frontend is hosted on mpoxel.in while this API is hosted on
# mpoxel.pythonanywhere.com. Allow only the known frontend origins.
ALLOWED_CORS_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "MPOXEL_ALLOWED_ORIGINS",
        "https://mpoxel.in,https://www.mpoxel.in"
    ).split(",")
    if origin.strip()
}


def application(environ, start_response):
    """WSGI entry point with restricted cross-origin support."""
    origin = (environ.get("HTTP_ORIGIN") or "").rstrip("/")
    origin_allowed = origin in ALLOWED_CORS_ORIGINS

    def cors_start_response(status, headers, exc_info=None):
        response_headers = list(headers)
        # Vary prevents caches from mixing responses for different origins.
        vary_value = next((value for key, value in response_headers if key.lower() == "vary"), None)
        if vary_value:
            if "Origin" not in [part.strip() for part in vary_value.split(",")]:
                response_headers = [(key, value) for key, value in response_headers if key.lower() != "vary"]
                response_headers.append(("Vary", vary_value + ", Origin"))
        else:
            response_headers.append(("Vary", "Origin"))

        if origin_allowed:
            response_headers.extend([
                ("Access-Control-Allow-Origin", origin),
                ("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS"),
                ("Access-Control-Allow-Headers", "Content-Type, Accept"),
                ("Access-Control-Max-Age", "86400"),
            ])
        return start_response(status, response_headers, exc_info)

    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method == "OPTIONS":
        if not origin_allowed:
            return _wsgi_json(start_response, 403, {"error": "Origin not allowed."})
        requested_headers = {
            item.strip().lower()
            for item in (environ.get("HTTP_ACCESS_CONTROL_REQUEST_HEADERS") or "").split(",")
            if item.strip()
        }
        permitted_headers = {"content-type", "accept"}
        if not requested_headers.issubset(permitted_headers):
            return _wsgi_json(start_response, 403, {"error": "Requested CORS header is not allowed."})
        cors_start_response("204 No Content", [("Content-Length", "0"), ("Cache-Control", "no-store")])
        return [b""]

    return _mpoxel_wsgi_application(environ, cors_start_response)


# -------------------- SERVER --------------------

class MpoxelServer(ThreadingHTTPServer):
    daemon_threads = True
