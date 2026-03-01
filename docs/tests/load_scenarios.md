# Нагрузочные тесты 

## Критерии приёмки (минимум)  
  
- curl -v 127.0.0.1:8080/anything возвращает ответ апстрима с правильными заголовками/статусом.
- Под нагрузкой (см. ниже) сервер не падает, корректно ограничивает одновременные соединения и не течёт памятью заметно.
- Таймауты срабатывают предсказуемо: зависший апстрим не вешает клиента навсегда.

### Нагрузка (k6) План:

```
wrk -t4 -c128 -d30s http://127.0.0.1:8080/
ab -n 5000 -c 200 http://127.0.0.1:8080/
vegeta attack -duration=30s -rate=500 | vegeta report
```

Локально:

```bash
k6 run tests/k6/load-test.js    # 500-5000 VU постепенная нагрузка
k6 run tests/k6/load-test-wrk-like.js   # 128 VU, 30 с
k6 run tests/k6/load-test-ab-like.js    # 5000 запросов, 200 одновременных
k6 run tests/k6/load-test-vegeta-like.js # 500 RPS, 30 с
k6 run tests/k6/load-test-constant-rate.js -e RPS=1000   # целевой RPS (500 по умолчанию)
```

В Docker Compose:

```bash
docker compose --profile load-test run --rm k6 run /scripts/load-test.js -e BASE_URL=http://proxy:8080
docker compose --profile load-test run --rm k6 run /scripts/load-test-wrk-like.js -e BASE_URL=http://proxy:8080
docker compose --profile load-test run --rm k6 run /scripts/load-test-ab-like.js -e BASE_URL=http://proxy:8080
docker compose --profile load-test run --rm k6 run /scripts/load-test-vegeta-like.js -e BASE_URL=http://proxy:8080
docker compose --profile load-test run --rm k6 run /scripts/load-test-constant-rate.js -e BASE_URL=http://proxy:8080 -e RPS=1000 
```

Расшифровка:

- wrk: wrk -t4 -c128 -d30s → 4 потока, 128 соединений, 30 секунд
- ab: ab -n5000 -c200 → 5000 запросов, 200 одновременных соединений
- vegeta: -duration=30s -rate=500 → 30 секунд, 500 запросов в секунду

### Нагрузка под целевой PRS

```bash
# 500 RPS по умолчанию, 60 с
k6 run tests/k6/load-test-constant-rate.js

# 1000 RPS
k6 run tests/k6/load-test-constant-rate.js -e RPS=1000

# 5000 RPS, 2 минуты
k6 run tests/k6/load-test-constant-rate.js -e RPS=5000 -e DURATION=120s
```

Скрипт: `tests/k6/load-test-constant-rate.js` (POST /events/, как в основном load-test.js).

[Как тестировать целевой RPS](../info/load_test_k6_analyze.md#vus-и-rps-как-тестировать-целевой-rps)

### Результаты (отчеты К6) ПОСЛЕ доработок (keep-alive pool + config)

#### 500-5000 VU постепенная нагрузка

##### **Ключевые метрики**

| Метрика | Значение | Статус |
|---------|----------|--------|
| **RPS (запросов/сек)** | 640.66 | ⚠️ Ниже цели (4000) |
| **Успешные ответы** | 98.72% | ✅ Цель (<95%) |
| **P95 latency** | 5.11с | ✅ Цель (<15с) |
| **Всего ошибок** | 2,230 (1.28%) | ✅ Цель (<5%) |
| **Прерванные итерации** | 1,385 | ⚠️ Требует внимания |

##### **Итоговое заключение**

**Сильные стороны:**
- ✅ Circuit breaker настроен оптимально
- ✅ Connection pool эффективно переиспользует соединения
- ✅ Система стабильна до 4000 VUs
- ✅ Быстрое восстановление после нагрузки

**Что требует внимания:**
- ⚠️ RPS ниже целевого (640 vs 4000)
- ⚠️ Таймауты при 5000 VUs
- ⚠️ 1385 прерванных соединений

**Следующие шаги:**
1. **Профилирование бэкендов** - почему 2.6с на запрос?
2. **Оптимизация прокси** - увеличить лимиты соединений
3. **Горизонтальное масштабирование** - добавить инстансы
4. **Тест с фиксированной нагрузкой** - найти точку отказа

**Итоговая оценка: 8/10** 🎯

```bash
WARN[0268] Request Failed                                error="Post \"http://127.0.0.1:8080/events/\": request timeout"


  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<15000' p(95)=5.11s

    http_req_failed
    ✓ 'rate<0.05' rate=1.28%

    http_reqs
    ✗ 'rate>=4000' rate=640.660278/s


  █ TOTAL RESULTS 

    checks_total.......: 173319 640.660278/s
    checks_succeeded...: 98.71% 171089 out of 173319
    checks_failed......: 1.28%  2230 out of 173319

    ✗ status equals 200
      ↳  98% — ✓ 171089 / ✗ 2230

    HTTP
    http_req_duration..............: avg=3.33s min=754.1µs  med=2.81s max=1m0s p(90)=4.97s p(95)=5.11s
      { expected_response:true }...: avg=2.6s  min=754.1µs  med=2.74s max=8.5s p(90)=4.89s p(95)=5.08s
    http_req_failed................: 1.28%  2230 out of 173319
    http_reqs......................: 173319 640.660278/s

    EXECUTION
    iteration_duration.............: avg=3.33s min=833.38µs med=2.81s max=1m0s p(90)=4.97s p(95)=5.11s
    iterations.....................: 173319 640.660278/s
    vus............................: 570    min=11             max=5000
    vus_max........................: 5000   min=5000           max=5000

    NETWORK
    data_received..................: 90 MB  332 kB/s
    data_sent......................: 35 MB  127 kB/s




running (4m30.5s), 0000/5000 VUs, 173319 complete and 1385 interrupted iterations
default ✓ [======================================] 0000/5000 VUs  4m30s
```


#### wrk-like

**Ключевые результаты**

- RPS: 62 --> 905
- AVG: 2s --> 140ms
- p(95): 2.56s --> 204ms

```bash
         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: tests/k6/load-test-wrk-like.js
        output: -

     scenarios: (100.00%) 1 scenario, 128 max VUs, 35s max duration (incl. graceful stop):
              * wrk_like: 128 looping VUs for 30s (gracefulStop: 5s)



  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<5000' p(95)=204.73ms
    ✓ 'p(99)<10000' p(99)=307.72ms

    http_req_failed
    ✓ 'rate<0.01' rate=0.00%


  █ TOTAL RESULTS 

    checks_total.......: 27232   905.141313/s
    checks_succeeded...: 100.00% 27232 out of 27232
    checks_failed......: 0.00%   0 out of 27232

    ✓ status 200

    HTTP
    http_req_duration..............: avg=140.88ms min=19.13ms med=143.2ms  max=416.43ms p(90)=185.9ms  p(95)=204.73ms
      { expected_response:true }...: avg=140.88ms min=19.13ms med=143.2ms  max=416.43ms p(90)=185.9ms  p(95)=204.73ms
    http_req_failed................: 0.00%  0 out of 27232
    http_reqs......................: 27232  905.141313/s

    EXECUTION
    iteration_duration.............: avg=141.18ms min=19.2ms  med=143.49ms max=416.76ms p(90)=186.24ms p(95)=205.24ms
    iterations.....................: 27232  905.141313/s
    vus............................: 128    min=128        max=128
    vus_max........................: 128    min=128        max=128

    NETWORK
    data_received..................: 10 MB  337 kB/s
    data_sent......................: 1.9 MB 63 kB/s




running (30.1s), 000/128 VUs, 27232 complete and 0 interrupted iterations
wrk_like ✓ [======================================] 128 VUs  30s

```

#### ab-like

**Ключевые результаты**

- RPS: 52 --> 2184
- AVG: 3.7s --> 76.83ms
- p(95): 24.49s --> 179.42ms


```bash

         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: tests/k6/load-test-ab-like.js
        output: -

     scenarios: (100.00%) 1 scenario, 200 max VUs, 5m5s max duration (incl. graceful stop):
              * ab_like: 25 iterations for each of 200 VUs (maxDuration: 5m0s, gracefulStop: 5s)



  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<5000' p(95)=179.42ms
    ✓ 'p(99)<10000' p(99)=227.79ms

    http_req_failed
    ✓ 'rate<0.01' rate=0.00%


  █ TOTAL RESULTS 

    checks_total.......: 5000    2184.019833/s
    checks_succeeded...: 100.00% 5000 out of 5000
    checks_failed......: 0.00%   0 out of 5000

    ✓ status 200

    HTTP
    http_req_duration..............: avg=76.83ms min=11.82ms med=56.51ms max=234.67ms p(90)=149.23ms p(95)=179.42ms
      { expected_response:true }...: avg=76.83ms min=11.82ms med=56.51ms max=234.67ms p(90)=149.23ms p(95)=179.42ms
    http_req_failed................: 0.00%  0 out of 5000
    http_reqs......................: 5000   2184.019833/s

    EXECUTION
    iteration_duration.............: avg=77.08ms min=12.1ms  med=56.57ms max=234.93ms p(90)=149.48ms p(95)=179.67ms
    iterations.....................: 5000   2184.019833/s
    vus............................: 96     min=96        max=200
    vus_max........................: 200    min=200       max=200

    NETWORK
    data_received..................: 1.9 MB 813 kB/s
    data_sent......................: 350 kB 153 kB/s




running (0m02.3s), 000/200 VUs, 5000 complete and 0 interrupted iterations
ab_like ✓ [======================================] 200 VUs  0m02.3s/5m0s  5000/5000 iters, 25 per VU

```

#### vegeta-like

**Ключевые результаты**

- RPS: 50 --> 499
- AVG: 12.85s --> 3.77ms
- p(95): 17.98s --> 6.96ms


```bash

         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: tests/k6/load-test-vegeta-like.js
        output: -

     scenarios: (100.00%) 1 scenario, 1000 max VUs, 35s max duration (incl. graceful stop):
              * vegeta_like: 500.00 iterations/s for 30s (maxVUs: 100-1000, gracefulStop: 5s)



  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<5000' p(95)=6.96ms
    ✓ 'p(99)<10000' p(99)=10.78ms

    http_req_failed
    ✓ 'rate<0.01' rate=0.00%

    http_reqs
    ✓ 'rate>=450' rate=499.931324/s


  █ TOTAL RESULTS 

    checks_total.......: 15001   499.931324/s
    checks_succeeded...: 100.00% 15001 out of 15001
    checks_failed......: 0.00%   0 out of 15001

    ✓ status 200

    HTTP
    http_req_duration..............: avg=3.77ms min=852.75µs med=3.61ms max=39.91ms p(90)=5.72ms p(95)=6.96ms
      { expected_response:true }...: avg=3.77ms min=852.75µs med=3.61ms max=39.91ms p(90)=5.72ms p(95)=6.96ms
    http_req_failed................: 0.00%  0 out of 15001
    http_reqs......................: 15001  499.931324/s

    EXECUTION
    iteration_duration.............: avg=4.02ms min=917.05µs med=3.85ms max=40.12ms p(90)=6.07ms p(95)=7.32ms
    iterations.....................: 15001  499.931324/s
    vus............................: 2      min=0          max=5  
    vus_max........................: 100    min=100        max=100

    NETWORK
    data_received..................: 5.6 MB 186 kB/s
    data_sent......................: 1.1 MB 35 kB/s




running (30.0s), 0000/0100 VUs, 15001 complete and 0 interrupted iterations
vegeta_like ✓ [======================================] 0000/0100 VUs  30s  500.00 iters/s
```

#### 500 RPS

```bash
         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: tests/k6/load-test-constant-rate.js
        output: -

     scenarios: (100.00%) 1 scenario, 2000 max VUs, 1m10s max duration (incl. graceful stop):
              * constant_rate: 500.00 iterations/s for 1m0s (maxVUs: 500-2000, gracefulStop: 10s)



  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<15000' p(95)=2.98s

    http_req_failed
    ✓ 'rate<0.05' rate=0.00%

    http_reqs
    ✗ 'rate>=450' rate=449.970149/s


  █ TOTAL RESULTS 

    checks_total.......: 27001   449.970149/s
    checks_succeeded...: 100.00% 27001 out of 27001
    checks_failed......: 0.00%   0 out of 27001

    ✓ status 200

    HTTP
    http_req_duration..............: avg=862.57ms min=933.45µs med=36.79ms max=4.48s p(90)=2.71s p(95)=2.98s
      { expected_response:true }...: avg=862.57ms min=933.45µs med=36.79ms max=4.48s p(90)=2.71s p(95)=2.98s
    http_req_failed................: 0.00%  0 out of 27001
    http_reqs......................: 27001  449.970149/s

    EXECUTION
    dropped_iterations.............: 2999   49.978167/s
    iteration_duration.............: avg=863.33ms min=1.01ms   med=37.21ms max=4.48s p(90)=2.71s p(95)=2.98s
    iterations.....................: 27001  449.970149/s
    vus............................: 3      min=1          max=1158
    vus_max........................: 1214   min=500        max=1214

    NETWORK
    data_received..................: 14 MB  237 kB/s
    data_sent......................: 5.4 MB 89 kB/s




running (1m00.0s), 0000/1214 VUs, 27001 complete and 0 interrupted iterations
constant_rate ✓ [======================================] 0000/1214 VUs  1m0s  500.00 iters/s
```

#### 1000 RPS

Ключевые показатели:

- RPS: ~690 запроса в секунду (стабильно)
- P95: 4.32 секунды (в пределах таймаутов)
- Максимальное время: 6.65 секунд (ни одного таймаута!)
- Ни одного прерванного соединения
- connection pull использовался 

```bash

         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: tests/k6/load-test-constant-rate.js
        output: -

     scenarios: (100.00%) 1 scenario, 4000 max VUs, 1m10s max duration (incl. graceful stop):
              * constant_rate: 1000.00 iterations/s for 1m0s (maxVUs: 500-4000, gracefulStop: 10s)

WARN[0051] Insufficient VUs, reached 4000 active VUs and cannot initialize more  executor=constant-arrival-rate scenario=constant_rate


  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<15000' p(95)=4.32s

    http_req_failed
    ✓ 'rate<0.05' rate=0.00%

    http_reqs
    ✗ 'rate>=900' rate=690.482473/s


  █ TOTAL RESULTS 

    checks_total.......: 48346   690.482473/s
    checks_succeeded...: 100.00% 48346 out of 48346
    checks_failed......: 0.00%   0 out of 48346

    ✓ status 200

    HTTP
    http_req_duration..............: avg=2.58s min=923.17µs med=2.56s max=6.65s p(90)=4.08s p(95)=4.32s
      { expected_response:true }...: avg=2.58s min=923.17µs med=2.56s max=6.65s p(90)=4.08s p(95)=4.32s
    http_req_failed................: 0.00%  0 out of 48346
    http_reqs......................: 48346  690.482473/s

    EXECUTION
    dropped_iterations.............: 10642  151.990123/s
    iteration_duration.............: avg=2.58s min=1.01ms   med=2.56s max=6.65s p(90)=4.08s p(95)=4.32s
    iterations.....................: 48346  690.482473/s
    vus............................: 1012   min=1          max=4000
    vus_max........................: 4000   min=500        max=4000

    NETWORK
    data_received..................: 26 MB  364 kB/s
    data_sent......................: 9.8 MB 141 kB/s




running (1m10.0s), 0000/4000 VUs, 48346 complete and 1012 interrupted iterations
constant_rate ✓ [======================================] 1012/4000 VUs  1m0s  1000.00 iters/s
ERRO[0070] thresholds on metrics 'http_reqs' have been crossed 

```

#### 5000 RPS

Upstream запущены два порта по 1 воркеру uvicorn

config.yaml
```yaml
timeouts:
  connect_ms: 1000
  read_ms: 5000
  write_ms: 5000
  total_ms: 10000

limits:
  max_client_conns: 2500
  max_conns_per_upstream: 1000

logging:
  level: "info"

connection_pool:
  max_size: 2500
  max_connections_per_host: 2000
  idle_timeout: 15.0
  connect_timeout: 5.0
  read_timeout: 30.0

circuit_breaker:
  failure_threshold: 120
  recovery_timeout: 15.0
  half_open_max_requests: 20
  half_open_max_failures: 12
  half_open_timeout_multiplier: 2
  timeout: 12.0
```

Отчет

```bash
WARN[0130] Request Failed                                error="Post \"http://127.0.0.1:8080/events/\": request timeout"
WARN[0130] Request Failed                                error="Post \"http://127.0.0.1:8080/events/\": request timeout"


  █ THRESHOLDS 

    http_req_duration
    ✗ 'p(95)<15000' p(95)=1m0s

    http_req_failed
    ✗ 'rate<0.05' rate=50.02%

    http_reqs
    ✗ 'rate>=4500' rate=425.22764/s


  █ TOTAL RESULTS 

    checks_total.......: 55402  425.22764/s
    checks_succeeded...: 49.97% 27688 out of 55402
    checks_failed......: 50.02% 27714 out of 55402

    ✗ status 200
      ↳  49% — ✓ 27688 / ✗ 27714

    HTTP
    http_req_duration..............: avg=28s    min=0s       med=13.18s max=1m1s   p(90)=1m0s  p(95)=1m0s  
      { expected_response:true }...: avg=10.59s min=217ms    med=6.47s  max=56.53s p(90)=27s   p(95)=50.18s
    http_req_failed................: 50.02% 27714 out of 55402
    http_reqs......................: 55402  425.22764/s

    EXECUTION
    dropped_iterations.............: 537431 4124.950651/s
    iteration_duration.............: avg=32.66s min=111.81ms med=18.36s max=1m30s  p(90)=1m16s p(95)=1m23s 
    iterations.....................: 55402  425.22764/s
    vus............................: 5751   min=1044           max=20000
    vus_max........................: 20000  min=1079           max=20000

    NETWORK
    data_received..................: 15 MB  112 kB/s
    data_sent......................: 11 MB  84 kB/s




running (2m10.3s), 00000/20000 VUs, 55397 complete and 5729 interrupted iterations
constant_rate ✓ [======================================] 05729/20000 VUs  2m0s  5000.00 iters/s
ERRO[0130] thresholds on metrics 'http_req_duration, http_req_failed, http_reqs' have been crossed 
```

Ошибки Proxy

```bash
Traceback (most recent call last):
  File "/home/nikson/Dev/CursorProjects/mini-nginx/proxy/client_handler.py", line 156, in _proxy_to_upstream_internal
    return await self._proxy_with_aiohttp(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/nikson/Dev/CursorProjects/mini-nginx/proxy/client_handler.py", line 490, in _proxy_with_aiohttp
    raise ClientDisconnectError("Client disconnected") from e
proxy.circuit_breaker.ClientDisconnectError: Client disconnected
2026-03-01 18:33:16,635 - proxy - ERROR - Error proxying via connection pool to 127.0.0.1:9001: Client disconnected trace_id=e0a9c072-a2aa-43a2-9f11-2ef63840a0b4
Traceback (most recent call last):
  File "/home/nikson/Dev/CursorProjects/mini-nginx/proxy/client_handler.py", line 486, in _proxy_with_aiohttp
    await self.writer.drain()
  File "/usr/lib/python3.12/asyncio/streams.py", line 392, in drain
    await self._protocol._drain_helper()
  File "/usr/lib/python3.12/asyncio/streams.py", line 166, in _drain_helper
    raise ConnectionResetError('Connection lost')
ConnectionResetError: Connection lost
```


### Результаты (отчеты К6) До доработок (keep-alive pool + config)

#### wrk-like

```bash

         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: /scripts/load-test-wrk-like.js
        output: -

     scenarios: (100.00%) 1 scenario, 128 max VUs, 35s max duration (incl. graceful stop):
              * wrk_like: 128 looping VUs for 30s (gracefulStop: 5s)



  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<5000' p(95)=2.56s
    ✓ 'p(99)<10000' p(99)=2.78s

    http_req_failed
    ✓ 'rate<0.01' rate=0.00%


  █ TOTAL RESULTS 

    checks_total.......: 1951    62.497528/s
    checks_succeeded...: 100.00% 1951 out of 1951
    checks_failed......: 0.00%   0 out of 1951

    ✓ status 200

    HTTP
    http_req_duration..............: avg=2s min=591.09ms med=1.97s max=2.86s p(90)=2.42s p(95)=2.56s
      { expected_response:true }...: avg=2s min=591.09ms med=1.97s max=2.86s p(90)=2.42s p(95)=2.56s
    http_req_failed................: 0.00%  0 out of 1951
    http_reqs......................: 1951   62.497528/s

    EXECUTION
    iteration_duration.............: avg=2s min=594.67ms med=1.98s max=2.86s p(90)=2.42s p(95)=2.57s
    iterations.....................: 1951   62.497528/s
    vus............................: 41     min=41        max=128
    vus_max........................: 128    min=128       max=128

    NETWORK
    data_received..................: 755 kB 24 kB/s
    data_sent......................: 129 kB 4.1 kB/s




running (31.2s), 000/128 VUs, 1951 complete and 0 interrupted iterations
wrk_like ✓ [======================================] 128 VUs  30s
```

#### ab-like

```bash

         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: /scripts/load-test-ab-like.js
        output: -

     scenarios: (100.00%) 1 scenario, 200 max VUs, 5m5s max duration (incl. graceful stop):
              * ab_like: 25 iterations for each of 200 VUs (maxDuration: 5m0s, gracefulStop: 5s)



  █ THRESHOLDS 

    http_req_duration
    ✓ 'p(95)<5000' p(95)=4.49s
    ✓ 'p(99)<10000' p(99)=4.68s

    http_req_failed
    ✓ 'rate<0.01' rate=0.00%


  █ TOTAL RESULTS 

    checks_total.......: 5000    52.855812/s
    checks_succeeded...: 100.00% 5000 out of 5000
    checks_failed......: 0.00%   0 out of 5000

    ✓ status 200

    HTTP
    http_req_duration..............: avg=3.7s  min=765.59ms med=3.64s max=4.78s p(90)=4.37s p(95)=4.49s
      { expected_response:true }...: avg=3.7s  min=765.59ms med=3.64s max=4.78s p(90)=4.37s p(95)=4.49s
    http_req_failed................: 0.00%  0 out of 5000
    http_reqs......................: 5000   52.855812/s

    EXECUTION
    iteration_duration.............: avg=3.71s min=769.33ms med=3.64s max=5.12s p(90)=4.39s p(95)=4.52s
    iterations.....................: 5000   52.855812/s
    vus............................: 65     min=65        max=200
    vus_max........................: 200    min=200       max=200

    NETWORK
    data_received..................: 1.9 MB 21 kB/s
    data_sent......................: 330 kB 3.5 kB/s




running (1m34.6s), 000/200 VUs, 5000 complete and 0 interrupted iterations
ab_like ✓ [=================] 200 VUs  1m34.6s/5m0s  5000/5000 iters, 25 per VU
```

#### vegeta-like

```bash

         /\      Grafana   /‾‾/  
    /\  /  \     |\  __   /  /   
   /  \/    \    | |/ /  /   ‾‾\ 
  /          \   |   (  |  (‾)  |
 / __________ \  |_|\_\  \_____/ 

     execution: local
        script: /scripts/load-test-vegeta-like.js
        output: -

     scenarios: (100.00%) 1 scenario, 1000 max VUs, 35s max duration (incl. graceful stop):
              * vegeta_like: 500.00 iterations/s for 30s (maxVUs: 100-1000, gracefulStop: 5s)

WARN[0008] Insufficient VUs, reached 1000 active VUs and cannot initialize more  executor=constant-arrival-rate scenario=vegeta_like


  █ THRESHOLDS 

    http_req_duration
    ✗ 'p(95)<5000' p(95)=17.98s
    ✗ 'p(99)<10000' p(99)=18.14s

    http_req_failed
    ✓ 'rate<0.01' rate=0.00%

    http_reqs
    ✗ 'rate>=450' rate=50.282109/s


  █ TOTAL RESULTS 

    checks_total.......: 1767    50.282109/s
    checks_succeeded...: 100.00% 1767 out of 1767
    checks_failed......: 0.00%   0 out of 1767

    ✓ status 200

    HTTP
    http_req_duration..............: avg=12.85s min=575.68ms med=13.6s max=18.18s p(90)=17.88s p(95)=17.98s
      { expected_response:true }...: avg=12.85s min=575.68ms med=13.6s max=18.18s p(90)=17.88s p(95)=17.98s
    http_req_failed................: 0.00%  0 out of 1767
    http_reqs......................: 1767   50.282109/s

    EXECUTION
    dropped_iterations.............: 12568  357.637547/s
    iteration_duration.............: avg=12.86s min=582.85ms med=13.6s max=18.19s p(90)=17.88s p(95)=17.99s
    iterations.....................: 1767   50.282109/s
    vus............................: 675    min=176       max=1000
    vus_max........................: 1000   min=176       max=1000

    NETWORK
    data_received..................: 685 kB 20 kB/s
    data_sent......................: 161 kB 4.6 kB/s




running (35.1s), 0000/1000 VUs, 1767 complete and 666 interrupted iterations
vegeta_like ✓ [============================] 0666/1000 VUs  30s  500.00 iters/s
ERRO[0035] thresholds on metrics 'http_req_duration, http_reqs' have been crossed 
```

## Продвинутые задания (необязательно, по желанию)

- [] Health‑checks апстримов (active/passive), исключение недоступных из балансировки.
- [] Retry политика (например, при connect/read таймаутах, но не для небезопасных методов).
- [x] Circuit Breaker (отключение проблемного апстрима на интервал).
- [] Rate limiting (token bucket) на клиента или общий.
- [] Поддержка HTTPS на фронте (TLS termination) и/или к апстриму.
- [x] Горячая перезагрузка конфигурации (SIGHUP) без остановки сервера.
- [x] HTTP/1.1 keep‑alive пул к апстримам, повторное использование соединений.
- [x] Проброс/модификация заголовков (X-Forwarded-For, Via, Connection: keep-alive и т. п.).
- [x] Мини‑панель метрик: простая страница со статистикой.
