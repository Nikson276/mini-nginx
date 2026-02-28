from fastapi import FastAPI, Request, Response
import logging
import time
import json
import asyncio
from typing import Dict
import uuid
import psutil
import os

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(trace_id)s] - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Статистика для мониторинга
stats = {
    "total_requests": 0,
    "active_requests": 0,
    "errors": 0,
    "last_request_time": None,
    "request_times": [],
    "concurrent_requests": 0,
    "max_concurrent": 0
}

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware для логирования всех запросов"""
    # Генерируем trace_id если его нет
    trace_id = request.headers.get('X-Trace-ID', str(uuid.uuid4())[:8])
    
    # Обновляем статистику
    stats["total_requests"] += 1
    stats["active_requests"] += 1
    stats["concurrent_requests"] += 1
    stats["max_concurrent"] = max(stats["max_concurrent"], stats["concurrent_requests"])
    
    # Логируем входящий запрос
    logger.info(
        f"→ Received request: {request.method} {request.url.path}",
        extra={"trace_id": trace_id}
    )
    
    # Замеряем время обработки
    start_time = time.time()
    
    # Логируем основные заголовки
    important_headers = {
        'host': request.headers.get('host'),
        'user-agent': request.headers.get('user-agent'),
        'content-length': request.headers.get('content-length'),
        'connection': request.headers.get('connection'),
        'x-forwarded-for': request.headers.get('x-forwarded-for'),
    }
    logger.debug(
        f"Request headers: {important_headers}",
        extra={"trace_id": trace_id}
    )
    
    try:
        # Обрабатываем запрос
        response = await call_next(request)
        
        # Считаем время
        process_time = time.time() - start_time
        stats["request_times"].append(process_time)
        # Храним только последние 1000 значений
        if len(stats["request_times"]) > 1000:
            stats["request_times"] = stats["request_times"][-1000:]
        
        # Логируем ответ
        logger.info(
            f"← Response: {response.status_code} - {process_time:.3f}s",
            extra={"trace_id": trace_id}
        )
        
        # Добавляем заголовки с временем обработки
        response.headers["X-Process-Time"] = str(process_time)
        response.headers["X-Trace-ID"] = trace_id
        
        return response
        
    except Exception as e:
        # Логируем ошибки
        stats["errors"] += 1
        logger.error(
            f"✗ Error processing request: {str(e)}",
            exc_info=True,
            extra={"trace_id": trace_id}
        )
        raise
    finally:
        stats["active_requests"] -= 1
        stats["concurrent_requests"] -= 1

@app.api_route("{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def echo(path: str, request: Request):
    """
    Echo endpoint that returns request information.
    Useful for testing proxy functionality.
    """
    # Получаем trace_id из заголовков или генерируем новый
    trace_id = request.headers.get('trace_id', None)
    
    # Логируем начало обработки
    logger.debug(f"Processing {request.method} request to /{path}", extra={"trace_id": trace_id})
    
    # Имитация небольшой задержки (как в реальном приложении)
    # Можно убрать или настроить
    # await asyncio.sleep(0.001)  # 1ms задержки
    
    try:
        # Читаем тело запроса
        body = await request.body()
        
        # Логируем размер тела
        if body:
            logger.debug(f"Request body size: {len(body)} bytes", extra={"trace_id": trace_id})
        
        # Собираем информацию о запросе
        response_data = {
            "method": request.method,
            "path": f"/{path}",
            "headers": dict(request.headers),
            "query_params": dict(request.query_params),
            "client": {
                "host": request.client.host if request.client else None,
                "port": request.client.port if request.client else None,
            },
            "trace_id": trace_id,
        }
        
        # Добавляем тело, если оно не слишком большое
        if body and len(body) < 10000:  # Не логируем большие тела
            try:
                response_data["body"] = body.decode('utf-8', errors='replace')
            except:
                response_data["body"] = "<binary data>"
        elif body:
            response_data["body"] = f"<body too large: {len(body)} bytes>"
        
        # Периодически логируем полную информацию (каждый 100-й запрос)
        if stats["total_requests"] % 100 == 0:
            logger.info(f"Sample request details: {response_data}", extra={"trace_id": trace_id})
        
        # Возвращаем ответ
        return Response(
            content=json.dumps(response_data, indent=2),
            media_type="application/json",
            status_code=200,
        )
        
    except Exception as e:
        logger.error(f"Error in echo endpoint: {str(e)}", exc_info=True, extra={"trace_id": trace_id})
        raise

@app.get("/")
async def root():
    """Root endpoint with stats."""
    # Рассчитываем среднее время ответа
    avg_time = sum(stats["request_times"]) / len(stats["request_times"]) if stats["request_times"] else 0
    
    return {
        "message": "Echo server is running",
        "status": "ok",
        "stats": {
            "total_requests": stats["total_requests"],
            "active_requests": stats["active_requests"],
            "errors": stats["errors"],
            "max_concurrent_requests": stats["max_concurrent"],
            "avg_response_time_ms": round(avg_time * 1000, 2),
            "last_100_requests_avg_ms": round(sum(stats["request_times"][-100:]) / len(stats["request_times"][-100:]) * 1000, 2) if stats["request_times"] else 0,
        }
    }

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}

@app.get("/stats")
async def get_stats():
    """Get detailed statistics."""
    return stats

@app.get("/metrics")
async def metrics():
    process = psutil.Process(os.getpid())
    return {
        "cpu_percent": process.cpu_percent(),
        "memory_percent": process.memory_percent(),
        "connections": len(process.connections()),
        "threads": len(process.threads()),
        "workers": 1,  # Можно передавать из переменной окружения
    }

# На порту 9001
# uvicorn main:app --host 0.0.0.0 --port 9001 --log-level info

# На порту 9002 (в другом терминале)
# uvicorn main:app --host 0.0.0.0 --port 9002 --log-level info
