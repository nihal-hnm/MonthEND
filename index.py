"""Vercel Python Web Application Entrypoint for MonthEnd Sovereign FinTech."""
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent / "index.html"


def app(environ, start_response):
    """Standard WSGI entrypoint exported for Vercel Python Serverless Runtime."""
    status = "200 OK"
    headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Cache-Control", "no-cache, no-store, must-revalidate"),
    ]
    start_response(status, headers)

    if HTML_PATH.exists():
        content = HTML_PATH.read_bytes()
    else:
        content = b"<!DOCTYPE html><html><body><h1>MonthEnd Sovereign FinTech</h1></body></html>"

    return [content]


# Export common WSGI / ASGI handler aliases expected by Vercel
application = app
handler = app

if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    print("Serving on http://127.0.0.1:8000...")
    httpd = make_server("127.0.0.1", 8000, app)
    httpd.serve_forever()
