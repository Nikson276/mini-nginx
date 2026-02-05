"""
Simple echo server for testing the proxy.

Run with:
    uvicorn echo_app:app --host 127.0.0.1 --port 9001 --workers 1

Or use Python's built-in server:
    python3 -m http.server 9001
"""

import pyroscope
import os
from fastapi import FastAPI, Request
from fastapi.responses import Response


def init_pyroscope_from_env():
    """Читаем все настройки из переменных окружения"""
    config = {
        'application_name': os.getenv('PYROSCOPE_APPLICATION_NAME', 'upstream-default'),
        'server_address': os.getenv('PYROSCOPE_SERVER', 'http://pyroscope:4040'),
        'tags': {
            'service': 'echo',
            'type': 'upstream',
            'instance': os.getenv('SERVICE_INSTANCE', '0'),
            'port': os.getenv('PORT', '9000'),
            'host': os.getenv('HOSTNAME', 'unknown')
        }
    }
    
    # Добавляем кастомные теги если есть
    custom_tags = os.getenv('PYROSCOPE_CUSTOM_TAGS', '')
    if custom_tags:
        for tag in custom_tags.split(','):
            if '=' in tag:
                key, val = tag.split('=', 1)
                config['tags'][key.strip()] = val.strip()
    
    pyroscope.configure(**config)
    print(f"Pyroscope configured: {config['application_name']}")

init_pyroscope_from_env()

app = FastAPI()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def echo(path: str, request: Request):
    """
    Echo endpoint that returns request information.
    Useful for testing proxy functionality.
    """
    # Read request body
    body = await request.body()
    
    # Build response with request info
    response_data = {
        "method": request.method,
        "path": f"/{path}",
        "headers": dict(request.headers),
        "body": body.decode('utf-8', errors='replace') if body else None,
        "query_params": dict(request.query_params),
    }
    
    import json
    return Response(
        content=json.dumps(response_data, indent=2),
        media_type="application/json",
        status_code=200,
    )


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Echo server is running", "status": "ok"}
