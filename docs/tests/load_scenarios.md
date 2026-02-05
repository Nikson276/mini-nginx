# Нагрузочные тесты 

## Критерии приёмки (минимум)  
  
- curl -v 127.0.0.1:8080/anything возвращает ответ апстрима с правильными заголовками/статусом.
- Под нагрузкой (см. ниже) сервер не падает, корректно ограничивает одновременные соединения и не течёт памятью заметно.
- Таймауты срабатывают предсказуемо: зависший апстрим не вешает клиента навсегда.

### Нагрузка (k6):

```
wrk -t4 -c128 -d30s http://127.0.0.1:8080/
ab -n 5000 -c 200 http://127.0.0.1:8080/
vegeta attack -duration=30s -rate=500 | vegeta report
```

Локально:

```bash
k6 run tests/k6/load-test-wrk-like.js   # 128 VU, 30 с
k6 run tests/k6/load-test-ab-like.js    # 5000 запросов, 200 одновременных
k6 run tests/k6/load-test-vegeta-like.js # 500 RPS, 30 с
```

В Docker Compose:

```bash
docker compose --profile load-test run --rm k6 run /scripts/load-test-wrk-like.js
docker compose --profile load-test run --rm k6 run /scripts/load-test-ab-like.js
docker compose --profile load-test run --rm k6 run /scripts/load-test-vegeta-like.js
```

Расшифровка:

- wrk: wrk -t4 -c128 -d30s → 4 потока, 128 соединений, 30 секунд
- ab: ab -n5000 -c200 → 5000 запросов, 200 одновременных соединений
- vegeta: -duration=30s -rate=500 → 30 секунд, 500 запросов в секунду

Все эти тесты проверяют:

- Базовую производительность прокси (статичный контент)
- Устойчивость к высокому RPS (Requests Per Second)
- Обработку множества одновременных соединений

**Метрики по ТЗ** (что смотреть в отчёте k6):

- RPS — `http_reqs` (rate);
- latency p95/p99 — `http_req_duration` p(95), p(99);
- ошибки — `http_req_failed`, `checks_failed`;
- timeouts — в логах и по коду ответа;
- распределение по апстримам — в метриках/логах прокси. апстримам (round‑robin).

### Результаты (отчеты К6)

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

- Health‑checks апстримов (active/passive), исключение недоступных из балансировки.
- Retry политика (например, при connect/read таймаутах, но не для небезопасных методов).
- Circuit Breaker (отключение проблемного апстрима на интервал).
- Rate limiting (token bucket) на клиента или общий.
- Поддержка HTTPS на фронте (TLS termination) и/или к апстриму.
- Горячая перезагрузка конфигурации (SIGHUP) без остановки сервера.
- HTTP/1.1 keep‑alive пул к апстримам, повторное использование соединений.
- Проброс/модификация заголовков (X-Forwarded-For, Via, Connection: keep-alive и т. п.).
- Мини‑панель метрик: простая страница со статистикой.