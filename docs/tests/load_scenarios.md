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
k6 run tests/k6/load-test.js
k6 run tests/k6/load-test-wrk-like.js   # 128 VU, 30 с
k6 run tests/k6/load-test-ab-like.js    # 5000 запросов, 200 одновременных
k6 run tests/k6/load-test-vegeta-like.js # 500 RPS, 30 с
k6 run tests/k6/load-test-constant-rate.js -e RPS=1000   # целевой RPS (500 по умолчанию)
```

В Docker Compose:

```bash
docker compose --profile load-test run --rm k6 run /scripts/load-test.js
docker compose --profile load-test run --rm k6 run /scripts/load-test-wrk-like.js
docker compose --profile load-test run --rm k6 run /scripts/load-test-ab-like.js
docker compose --profile load-test run --rm k6 run /scripts/load-test-vegeta-like.js
docker compose --profile load-test run --rm k6 run /scripts/load-test-constant-rate.js -e RPS=1000
```

Расшифровка:

- wrk: wrk -t4 -c128 -d30s → 4 потока, 128 соединений, 30 секунд
- ab: ab -n5000 -c200 → 5000 запросов, 200 одновременных соединений
- vegeta: -duration=30s -rate=500 → 30 секунд, 500 запросов в секунду

### VUs и RPS: почему 1500 VUs дают ~100 RPS и как тестировать целевой RPS

В сценариях по **stages (target: VUs)** RPS не задаётся напрямую. Справедлива формула:

- **RPS ≈ VUs / avg_iteration_duration**
- Одна итерация = один запрос (в нашем скрипте — один POST). Если в среднем ответ приходит за 7 с, то при 1500 VUs каждый виртуальный пользователь делает примерно 1 запрос за 7 с → 1500/7 ≈ 214 запросов/с в идеале. На практике часть времени уходит на очереди, таймауты, отказы → наблюдаем ~95–100 RPS.

Чтобы **тестировать именно целевой RPS** (1000, 5000 и т.д.), в k6 нужно использовать сценарий **constant-arrival-rate**: задаётся число запросов в секунду, k6 сам поднимает нужное количество VUs.

```bash
# 500 RPS по умолчанию, 60 с
k6 run tests/k6/load-test-constant-rate.js

# 1000 RPS
k6 run tests/k6/load-test-constant-rate.js -e RPS=1000

# 5000 RPS, 2 минуты
k6 run tests/k6/load-test-constant-rate.js -e RPS=5000 -e DURATION=120s
```

Скрипт: `tests/k6/load-test-constant-rate.js` (POST /events/, как в основном load-test.js).

### Потянет ли прокси 1000 или 10 000 RPS? От чего это зависит

- **Латентность апстрима** — главный фактор. Если бэкенд отвечает за 10 ms, один keep-alive запрос может давать до 100 RPS на соединение; 10 соединений → 1000 RPS. Если апстрим отвечает за 100 ms, для 1000 RPS уже нужно порядка 100 одновременных запросов к нему.
- **Прокси**: размер пула (`max_connections_per_host`), лимиты (`max_conns_per_upstream`), один глобальный lock убран — при быстром апстриме десятки тысяч RPS на одном процессе возможны, но обычно упор в 10k+ RPS делают на горизонтальное масштабирование (несколько инстансов прокси + балансировщик).
- **Железо и окружение**: CPU, память, локальный loopback vs сеть. На одном хосте (прокси + апстримы на 127.0.0.1) 1000 RPS при адекватном апстриме — реалистичная цель; 10 000 RPS на одном процессе — уже тяжёлый режим, лучше проверять пошагово (1k → 2k → 5k) и смотреть на ошибки и p95.

**Итого**: цели в 1000 RPS для одного инстанса прокси и быстрого апстрима — реалистичны. 10 000 RPS на одном инстансе — возможны при очень быстром бэкенде и оптимизациях; для надёжности чаще целится в несколько тысяч RPS на инстанс и масштабирование по горизонтали.

### Как поднять RPS, если тест упирается в ~100 RPS

Типичная картина: constant-rate 500 RPS, а фактически получается ~100 RPS и p95 латентности растёт (15–17 с). Значит система не успевает обрабатывать запросы, они стоят в очередях.

**От чего зависит потолок:**

- **RPS ≈ (число одновременных запросов к апстримам) / (среднее время ответа)**  
  Пример: 200 соединений к апстримам (100 на хост × 2), среднее время ответа 2 с → 200/2 = 100 RPS. Совпадение с наблюдаемым ~100 — признак того, что лимит упирается либо в число соединений, либо в скорость апстрима.

**Что смотреть по порядку:**

1. **Апстрим: больше воркеров (главный рычаг)**  
   Сейчас: по одному процессу uvicorn на каждый порт (9001, 9002) → 2 процесса. При 500 RPS они перегружены, запросы копятся, латентность растёт.  
   Запуск с несколькими воркерами на каждый порт:
   ```bash
   uvicorn tests.echo_app:app --host 127.0.0.1 --port 9001 --workers 4
   uvicorn tests.echo_app:app --host 127.0.0.1 --port 9002 --workers 4
   ```
   Итого 8 процессов — пропускная способность апстрима вырастет в разы; после этого снова гнать constant-rate 500 RPS.

2. **Апстрим: меньше работы в hot path**  
   В тестовом echo_app на каждый запрос: логирование (в т.ч. INFO), обновление `stats`, сериализация JSON. Под нагрузкой это съедает CPU. Для теста можно временно поднять уровень логов до WARNING или отключить лишнее — чтобы оценить «чистый» предел без логирования.

3. **Прокси: пул соединений**  
   В `config.yaml`: `max_connections_per_host` (сейчас 100). При быстром апстриме можно поднять до 150–200, чтобы больше запросов шло параллельно. Важно: это помогает только если апстрим реально успевает отвечать быстрее.

4. **Горизонтальное масштабирование**  
   Если одного инстанса прокси и нескольких воркеров апстрима не хватает:
   - апстрим: несколько инстансов за прокси (добавить в `upstreams` в конфиге);
   - прокси: несколько инстансов за балансировщиком (nginx/haproxy), каждый даёт свой RPS.

**Как обычно поступают:** сначала увеличивают воркеры/инстансы апстрима и убирают лишнюю нагрузку (логи), замеряют RPS и латентность. Если нужно ещё — поднимают пул в прокси и добавляют инстансы (апстрим и/или прокси).

#### Лимит открытых файлов (ulimit) при целевых 1000+ RPS

При высоких лимитах в конфиге (`max_client_conns`, `max_connections_per_host`) процесс может упираться в системный лимит числа открытых файлов (дескрипторов). Симптомы:

- В логах прокси: `OSError: [Errno 24] Too many open files`
- Затем возможно: `ValueError: Invalid file descriptor: -1` в asyncio (сокет уже закрыт из‑за нехватки FDs)

**Что сделать:** поднять лимит до старта прокси. Ориентир: `max_client_conns + (число_апстримов × max_connections_per_host) + 100`. Для конфига на 1000 RPS (например 2500 клиентов, 600 на хост × 2) нужно не менее ~4000; рекомендуется 8192.

- **Локально:** `ulimit -n 8192` в той же оболочке, затем запуск прокси.
- **systemd:** в unit-файле `LimitNOFILE=8192` (или больше).
- **Docker:** в `docker-compose.yml` у сервиса прокси: `ulimits: nofile: { soft: 8192, hard: 8192 }`.

При старте прокси выводит предупреждение, если текущий ulimit меньше требуемого по конфигу.

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

#### 800VUs 

Ключевые показатели:

- RPS: ~92 запроса в секунду (стабильно)
- P95: 8.02 секунды (в пределах таймаутов)
- Максимальное время: 9.11 секунд (ни одного таймаута!)
- Ни одного прерванного соединения
- connection pull использовался 

```bash
     scenarios: (100.00%) 1 scenario, 800 max VUs, 5m30s max duration (incl. graceful stop):
              * default: Up to 800 looping VUs for 5m0s over 4 stages (gracefulRampDown: 30s, gracefulStop: 30s)



  █ THRESHOLDS 

    http_reqs
    ✓ 'count>=10000' count=27590


  █ TOTAL RESULTS 

    checks_total.......: 27590   91.965706/s
    checks_succeeded...: 100.00% 27590 out of 27590
    checks_failed......: 0.00%   0 out of 27590

    ✓ status equals 200

    HTTP
    http_req_duration..............: avg=4.07s min=1.81ms med=4.03s max=9.11s p(90)=7.13s p(95)=8.02s
      { expected_response:true }...: avg=4.07s min=1.81ms med=4.03s max=9.11s p(90)=7.13s p(95)=8.02s
    http_req_failed................: 0.00%  0 out of 27590
    http_reqs......................: 27590  91.965706/s

    EXECUTION
    iteration_duration.............: avg=4.07s min=1.94ms med=4.03s max=9.11s p(90)=7.13s p(95)=8.02s
    iterations.....................: 27590  91.965706/s
    vus............................: 1      min=1          max=800
    vus_max........................: 800    min=800        max=800

    NETWORK
    data_received..................: 18 MB  61 kB/s
    data_sent......................: 5.4 MB 18 kB/s




running (5m00.0s), 000/800 VUs, 27590 complete and 0 interrupted iterations
default ✓ [======================================] 000/800 VUs  5m0s

```

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
