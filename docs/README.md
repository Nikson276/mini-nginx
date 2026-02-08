# Спецификация Мини‑Nginx на asyncio (reverse proxy)

## Запуск сервера

### Docker Compose (рекомендуется: всё в одной команде)

Поднимает прокси + два upstream. Прокси слушает порт **8080** на хосте и доступен из сети Docker (для k6 и других контейнеров по имени `proxy`).

```bash
# Сборка и запуск
docker compose up -d

# Просмотр логов
docker compose logs -f proxy

# остановить и удалить данные
docker compose down -v        

# смотреть метрики контейнеров
docker compose stats          

# Запуск нагрузочного теста К6 в отдельном контейнере
docker compose run --rm k6
```

Проверка с хоста: `curl http://127.0.0.1:8080/`  
Из контейнера (например, будущий k6): `http://proxy:8080`

В образ прокси уже встроен конфиг `config.docker.yaml` (копируется как `/app/config.yaml`) с `listen: 0.0.0.0:8080`, upstream'ами `upstream1:9001`, `upstream2:9002`. Переменные окружения при наличии файла не используются.

**Горячая перезагрузка конфига в Docker:**
1. В корне проекта создайте `config.yaml` (скопируйте из `config.docker.yaml` или `config.example.yaml`).
2. В `docker-compose.yml` раскомментируйте секцию `volumes` у сервиса `proxy`:
   ```yaml
   volumes:
     - ./config.yaml:/app/config.yaml
   ```
3. Запустите: `docker compose up -d proxy`.
4. Отредактируйте `config.yaml` на хосте (например, измените `logging.level` на `debug`).
5. Отправьте процессу прокси сигнал SIGHUP — конфиг перечитается без перезапуска контейнера:
   ```bash
   docker compose kill -s HUP proxy
   ```
6. В логах должно появиться: `Config reloaded from /app/config.yaml (logging level=debug)`.

Переменные окружения для прокси (если конфиг не используется):
- `UPSTREAM_HOSTS` — список upstream (по умолчанию `upstream1:9001,upstream2:9002`)
- `PROXY_LISTEN_HOST` / `PROXY_LISTEN_PORT` — хост/порт прокси
- `METRICS_LISTEN_HOST` / `METRICS_LISTEN_PORT` — хост/порт для `/metrics`

#### ПРИОРИТЕТ конфигов (от высокого к низкому):

1. Volume (./config.yaml:/app/config.yaml) ← ЕСЛИ подключен
2. Файл в образе (/app/config.yaml из COPY config.docker.yaml)
3. Переменные окружения (если поддерживается)

### Локально (без Docker)

```bash
# Запуск с параметрами по умолчанию (127.0.0.1:8080) или из config.yaml, если файл есть
python3 -m proxy.main

# С указанием хоста и порта (переопределяют конфиг)
python3 -m proxy.main 127.0.0.1 8080

# С конфигом из файла
python3 -m proxy.main /path/to/config.yaml
CONFIG_PATH=/path/to/config.yaml python3 -m proxy.main
```

При отсутствии `config.yaml` (и `CONFIG_PATH`) параметры берутся из переменных окружения. Для round-robin поднимите два upstream вручную (см. раздел «Тестирование»).

## Текущий статус

- ✅ TCP сервер принимает соединения
- ✅ Парсер HTTP-запросов (метод, путь, версия, заголовки, тело как raw-stream)
- ✅ Проксирование к одному upstream с двунаправленным стримингом
- ✅ Backpressure через `drain()` для предотвращения переполнения буферов
- ✅ Таймауты на все операции (connect, read, write, total)
- ✅ Балансировка round-robin по нескольким upstream
- ✅ Лимиты на количество одновременных соединений через Semaphore
- ✅ Добавил [unit tests pytest](../tests/README.md) по основным частям функционала
- ✅ Переход в контейнеры Docker для упрощения поднятия и тестов
- ✅ Добавил pyroscope для сбора метрик cpu по компонентам
- ✅ Логирование с trace_id и метрики: ручка `/metrics` на отдельном порту (Prometheus-формат)
- ✅ Конфиг из файла (YAML, Pydantic). Горячая перезагрузка конфигурации (SIGHUP) без остановки сервера.
- (В разработке) HTTP/1.1 keep‑alive пул к апстримам, повторное использование соединений.

## Что реализовано

### Проксирование запросов

Прокси-сервер теперь:
1. **Парсит HTTP-запросы** от клиента (метод, путь, версия, заголовки)
2. **Подключается к upstream** через `asyncio.open_connection`
3. **Стримит запрос** к upstream:
   - Отправляет стартовую строку и заголовки
   - Потоково передает тело запроса (не буферизуя полностью в памяти)
4. **Стримит ответ** от upstream к клиенту:
   - Читает ответ чанками и сразу отправляет клиенту
   - Использует `drain()` для обработки backpressure
5. **Корректно закрывает соединения** при ошибках или завершении

### Backpressure (контроль давления)

`await writer.drain()` используется для:
- **Предотвращения переполнения буферов**: если получатель (upstream или клиент) не успевает обрабатывать данные, мы ждем, вместо того чтобы накапливать данные в памяти
- **Эффективного использования памяти**: большие запросы/ответы не загружаются полностью в память
- **Синхронизации потоков**: гарантирует, что данные действительно отправлены перед продолжением

### Таймауты (Timeout Policy)

Прокси-сервер использует систему таймаутов для защиты от зависших соединений и медленных upstream серверов. Все таймауты настраиваются через класс `TimeoutPolicy`

#### Типы таймаутов:

1. **Connect timeout (1 секунда по умолчанию)**
   - Применяется к операции `asyncio.open_connection()`
   - Защищает от зависших подключений к upstream
   - Если upstream недоступен или медленно отвечает на подключение, запрос завершится с ошибкой 504 Gateway Timeout

2. **Read timeout (15 секунд по умолчанию)**
   - Применяется к каждой операции чтения данных от upstream (`reader.read()`)
   - Защищает от медленных upstream, которые не отправляют данные
   - Если upstream не отправляет данные в течение таймаута, запрос завершится с ошибкой 504

3. **Write timeout (15 секунд по умолчанию)**
   - Применяется к операции отправки запроса к upstream (`write_to_upstream()`)
   - Защищает от upstream, которые медленно принимают данные
   - Если upstream не принимает данные в течение таймаута, запрос завершится с ошибкой 504

4. **Total timeout (30 секунд по умолчанию)**
   - Применяется ко всему процессу проксирования запроса
   - Абсолютный лимит времени на обработку одного запроса
   - Даже если отдельные операции укладываются в свои таймауты, общий таймаут гарантирует, что запрос не будет обрабатываться бесконечно

#### Как это работает:

**Ключевая концепция:** `asyncio.wait_for(coro, timeout)` принимает **корутину** (объект асинхронной операции, еще не выполненный), запускает её выполнение и ждет с таймаутом.

```python
# Пример использования таймаутов в коде:

# 1. Подключение с таймаутом
# asyncio.open_connection() возвращает корутину (еще не выполненную!)
upstream_reader, upstream_writer = await timeout_policy.with_connect_timeout(
    asyncio.open_connection(host, port)  # Передаем корутину, не результат!
)

# 2. Отправка запроса с таймаутом
# request.write_to_upstream() возвращает корутину
await timeout_policy.with_write_timeout(
    request.write_to_upstream(upstream_writer)  # Корутина
)

# 3. Чтение ответа с таймаутом (на каждую операцию read)
# upstream_reader.read() возвращает корутину
chunk = await timeout_policy.with_read_timeout(
    upstream_reader.read(chunk_size)  # Корутина
)

# 4. Весь процесс обернут в общий таймаут
await timeout_policy.with_total_timeout(
    proxy_to_upstream_internal(...)  # Корутина
)
```

**Что происходит внутри `with_connect_timeout()`:**

```python
async def with_connect_timeout(self, coro):
    # coro - это корутина от asyncio.open_connection()
    # wait_for запускает выполнение корутины и ждет максимум 1 секунду
    return await asyncio.wait_for(coro, timeout=1.0)
    # Если подключение за 1 секунду → возвращает (reader, writer)
    # Если больше 1 секунды → отменяет операцию, выбрасывает TimeoutError
```

**Подробное объяснение:** см. [docs/timeouts_explanation.md](../docs/info/timeouts_explanation.md)  
**Рабочий пример:** запустите `python3 docs/info/timeout_example.py`

#### Настройка таймаутов:

Таймауты можно настроить, создав свой экземпляр `TimeoutPolicy`:

```python
from proxy.timeouts import TimeoutPolicy

# Кастомная политика таймаутов
custom_timeouts = TimeoutPolicy(
    connect_ms=2000,   # 2 секунды на подключение
    read_ms=30000,     # 30 секунд на чтение
    write_ms=30000,    # 30 секунд на запись
    total_ms=60000     # 60 секунд общий таймаут
)
```

#### Что изменилось после добавления таймаутов:

**До:**
- Запросы могли зависать навсегда, если upstream был недоступен
- Медленные upstream могли блокировать обработку запросов неограниченно долго
- Не было защиты от зависших соединений

**После:**
- ✅ Все операции имеют таймауты - запросы не могут зависнуть навсегда
- ✅ Если upstream недоступен, клиент получит 504 Gateway Timeout через 1 секунду (connect timeout)
- ✅ Если upstream медленно отправляет данные, клиент получит 504 через 15 секунд (read timeout)
- ✅ Если upstream медленно принимает данные, клиент получит 504 через 15 секунд (write timeout)
- ✅ Абсолютный лимит - даже при медленных операциях, запрос завершится через 30 секунд (total timeout)
- ✅ Защита от утечек ресурсов - зависшие соединения автоматически закрываются при таймауте


### Балансировка нагрузки (Round-Robin)

Прокси-сервер поддерживает балансировку нагрузки по нескольким upstream серверам с использованием алгоритма **round-robin**.

#### Как работает Round-Robin:

Round-robin распределяет запросы равномерно по всем upstream серверам последовательно:
- **Первый запрос** → первый upstream
- **Второй запрос** → второй upstream  
- **Третий запрос** → третий upstream
- **Четвертый запрос** → снова первый upstream (циклический переход)

Это простой и эффективный способ балансировки, который:
- ✅ Равномерно распределяет нагрузку между всеми upstream
- ✅ Не требует сложной логики выбора
- ✅ Работает хорошо, когда все upstream имеют одинаковую производительность
- ✅ Thread-safe (использует asyncio.Lock для защиты от race conditions)

#### Пример работы:

```python
# Пусть у нас есть 2 upstream сервера:
pool = UpstreamPool([
    Upstream(host='127.0.0.1', port=9001),
    Upstream(host='127.0.0.1', port=9002),
])

# Запрос 1 → upstream 9001
upstream = pool.get_next()  # Upstream(host='127.0.0.1', port=9001)

# Запрос 2 → upstream 9002
upstream = pool.get_next()  # Upstream(host='127.0.0.1', port=9002)

# Запрос 3 → upstream 9001 (снова первый)
upstream = pool.get_next()  # Upstream(host='127.0.0.1', port=9001)
```

#### Конфигурация upstream pool

Настройка upstream серверов в `proxy/proxy_server.py`:

```python
from proxy.upstream_pool import UpstreamPool, Upstream

# Создаем pool с несколькими upstream серверами
UPSTREAM_POOL = UpstreamPool([
    Upstream(host='127.0.0.1', port=9001),
    Upstream(host='127.0.0.1', port=9002),
    # Можно добавить больше upstream серверов
    # Upstream(host='127.0.0.1', port=9003),
])
```
#### Что изменилось после добавления балансировки:

**До:**
- Все запросы шли на один upstream сервер
- Не было распределения нагрузки
- При падении upstream все запросы падали

**После:**
- ✅ Запросы распределяются равномерно между всеми upstream
- ✅ Можно добавить несколько upstream для повышения надежности
- ✅ При падении одного upstream, другие продолжают работать (хотя сейчас нет автоматического исключения недоступных upstream - это можно добавить позже)
- ✅ Простое добавление новых upstream серверов через конфигурацию

### Лимиты соединений (Connection Limits)

Прокси-сервер использует `asyncio.Semaphore` для ограничения количества одновременных соединений. Это защищает сервер от перегрузки и контролирует использование ресурсов.

#### Что такое Semaphore?

**Semaphore (семафор)** - это примитив синхронизации, который контролирует доступ к ограниченному ресурсу. Представьте его как счетчик разрешений:

- `Semaphore(5)` означает, что одновременно может быть 5 "разрешений"
- Когда корутина хочет использовать ресурс, она вызывает `await semaphore.acquire()`
- Если есть свободное разрешение - корутина продолжает выполнение
- Если разрешений нет - корутина **ждет** (await), пока кто-то освободит разрешение
- Когда корутина закончила работу, она вызывает `semaphore.release()`

#### Пример работы Semaphore:

```python
import asyncio

# Создаем семафор с 3 разрешениями
sem = asyncio.Semaphore(3)

async def worker(name):
    async with sem:  # acquire() при входе, release() при выходе
        print(f"{name} работает")
        await asyncio.sleep(1)  # Симулируем работу
        print(f"{name} закончил")

# Запускаем 5 задач одновременно
await asyncio.gather(
    worker("A"), worker("B"), worker("C"), worker("D"), worker("E")
)

# Результат:
# A, B, C начинают работать одновременно (3 разрешения заняты)
# D и E ждут (нет свободных разрешений)
# Когда A закончит, D начнет работать (освободилось разрешение)
# Когда B закончит, E начнет работать
```

#### Зачем это нужно для прокси?

1. **Защита от перегрузки**: Если к прокси подключится 10000 клиентов одновременно, это может перегрузить сервер. Semaphore ограничивает количество одновременных соединений.

2. **Защита upstream**: Если прокси откроет 1000 соединений к одному upstream, это может перегрузить его. Semaphore ограничивает количество соединений к каждому upstream.

3. **Контроль ресурсов**: Semaphore помогает контролировать использование памяти и сетевых ресурсов.

#### Типы лимитов:

1. **Лимит клиентских соединений** (`max_client_conns`)
   - Ограничивает количество одновременных клиентских соединений
   - По умолчанию: 1000 соединений
   - Если лимит достигнут, новые клиенты будут ждать, пока не освободится место

2. **Лимит соединений к upstream** (`max_conns_per_upstream`)
   - Ограничивает количество соединений к каждому upstream серверу отдельно
   - По умолчанию: 100 соединений на upstream
   - Если лимит достигнут, прокси будет ждать перед подключением к upstream

#### Как это работает в коде:

```python
# Создаем менеджер лимитов
limits = ConnectionLimitManager(
    max_client_conns=100,      # Максимум 100 клиентских соединений
    max_conns_per_upstream=10   # Максимум 10 соединений к каждому upstream
)

# В обработчике клиента:
async with limits.client_connection():  # Ждем свободного места для клиента
    # Обрабатываем запрос клиента
    await handle_client()
    # При выходе из блока semaphore автоматически освобождается

# При подключении к upstream:
async with limits.upstream_connection(upstream):  # Ждем свободного места
    # Подключаемся к upstream
    await connect_to_upstream()
    # При выходе из блока semaphore автоматически освобождается
```

#### Конфигурация лимитов:

Лимиты настраиваются в `proxy/proxy_server.py`:

```python
from proxy.limits import ConnectionLimitManager, ConnectionLimits

# Создаем кастомные лимиты
custom_limits = ConnectionLimits(
    max_client_conns=500,        # 500 одновременных клиентских соединений
    max_conns_per_upstream=50     # 50 соединений к каждому upstream
)

CONNECTION_LIMITS = ConnectionLimitManager(custom_limits)
```

По умолчанию используются значения:
- `max_client_conns=1000` (1000 одновременных клиентских соединений)
- `max_conns_per_upstream=100` (100 соединений к каждому upstream)

#### Что изменилось после добавления лимитов:

**До:**
- Неограниченное количество одновременных соединений
- Риск перегрузки прокси при большом количестве запросов
- Риск перегрузки upstream серверов

**После:**
- ✅ Контролируемое количество одновременных соединений
- ✅ Защита прокси от перегрузки
- ✅ Защита upstream серверов от перегрузки
- ✅ Автоматическое ожидание при достижении лимита (не блокирует event loop)
- ✅ Автоматическое освобождение ресурсов при завершении соединения

### Логирование и метрики

Для каждого входящего запроса прокси генерирует **trace_id** (UUID) и прокидывает его по всему пути: в логах и в заголовке **X-Trace-ID** при запросе к upstream. По одному значению trace_id можно проследить путь запроса от клиента до upstream и обратно.

- **Логи**: асинхронное логирование через **aiologger** (не блокирует event loop). В каждой строке при наличии контекста добавляется суффикс ` trace_id=<uuid>`. Формат: `%(asctime)s - %(name)s - %(levelname)s - %(message)s trace_id=...`
- **Уровень логирования**: задаётся в конфиге (файл или env) — `logging.level` в YAML или переменная `LOG_LEVEL` (значения: debug, info, warning, error). При перезагрузке конфига (SIGHUP) уровень обновляется без перезапуска.
- **Метрики**: отдельный HTTP-сервер на порту **8081** (по умолчанию) отдаёт ручку **GET /metrics** в формате Prometheus (text/plain).

Переменные окружения:
- `METRICS_LISTEN_HOST` / `METRICS_LISTEN_PORT` — хост и порт для сервера метрик (по умолчанию 127.0.0.1:8081). В Docker можно задать `METRICS_LISTEN_HOST=0.0.0.0` и пробросить порт 8081.

Пример запроса метрик:
```bash
curl http://127.0.0.1:8081/metrics
```

Собираемые метрики (счётчики и сумма длительности запросов):
- `proxy_requests_total` — всего входящих запросов
- `proxy_requests_parse_errors_total` — ошибок разбора запроса
- `proxy_responses_total{status_class="2xx|3xx|4xx|5xx"}` — ответы по классу статуса
- `proxy_request_duration_seconds_sum` / `proxy_request_duration_seconds_count` — сумма и количество длительностей запросов (среднее = sum/count)
- `proxy_bytes_sent_total` — байт отправлено клиентам
- `proxy_upstream_requests_total{upstream="host:port"}` — запросов к каждому upstream
- `proxy_upstream_errors_total{upstream,type="timeout|connection_refused|other"}` — ошибок при обращении к upstream
- `proxy_timeout_errors_total{type="connect|read|write|total"}` — таймауты по типу

### Конфигурация из файла (YAML)

Параметры прокси можно задать в YAML-файле (валидация через Pydantic). При отсутствии файла используются переменные окружения (как раньше).

**Путь к конфигу:** переменная `CONFIG_PATH`, или первый аргумент командной строки (если не host:port), или файл `config.yaml` в текущей директории. Пример файла: `config.example.yaml` в корне проекта.

**Пример `config.yaml`:**
```yaml
listen: "127.0.0.1:8080"
metrics_listen: "127.0.0.1:8081"
upstreams:
  - host: "127.0.0.1"
    port: 9001
  - host: "127.0.0.1"
    port: 9002
timeouts:
  connect_ms: 1000
  read_ms: 15000
  write_ms: 15000
  total_ms: 30000
limits:
  max_client_conns: 1000
  max_conns_per_upstream: 100
logging:
  level: "info"
```

**Горячая перезагрузка (SIGHUP):** после изменения конфига отправьте процессу сигнал SIGHUP — конфиг будет перечитан без остановки сервера. Новые соединения используют обновлённые параметры.

```bash
kill -HUP $(cat proxy.pid)
# или по имени процесса
kill -HUP $(pgrep -f "python -m proxy.main")
```

## Тестирование

### [Нагрузочные тесты](./tests/load_scenarios.md)

### Юнит-тесты

Проект включает юнит-тесты для основных компонентов. Тесты находятся в каталоге `tests/`.

#### Установка зависимостей для тестов

```bash
pip install pytest pytest-asyncio
```

#### Запуск тестов

```bash
# Все тесты
pytest tests/ -v

# Конкретный тест
pytest tests/test_timeouts.py -v
pytest tests/test_upstream_pool.py -v
pytest tests/test_limits.py -v

# С подробным выводом
pytest tests/ -v -s
```

#### Что тестируется

- **test_timeouts.py**: TimeoutPolicy, работа таймаутов, TimeoutError
- **test_upstream_pool.py**: Round-robin балансировка, циклическое распределение
- **test_limits.py**: Semaphore лимиты, клиентские и upstream соединения

Подробнее см. [tests/README.md](../tests/README.md)

### Интеграционные тесты

#### Тест лимитов соединений

Bash скрипт для тестирования лимитов с реальными upstream серверами:

```bash
# 1. Запустите прокси сервер
python3 -m proxy.main

# 2. Запустите два upstream сервера (в разных терминалах)
uvicorn tests.echo_app:app --host 127.0.0.1 --port 9001
uvicorn tests.echo_app:app --host 127.0.0.1 --port 9002

# 3. Запустите интеграционный тест
./tests/test_limits_integration.sh
```

Скрипт делает:
- Параллельные запросы для проверки лимита соединений к upstream
- Последовательные запросы для проверки round-robin распределения
- Нагрузочный тест с множественными запросами

**Что проверить в логах прокси:**
- Распределение запросов между upstream (round-robin)
- Ожидание при достижении лимита соединений к upstream
- Временные метки начала и завершения обработки запросов

### 1. Запустить upstream сервер

Сначала нужно запустить тестовый upstream сервер (например, из папки `tests/`):

```bash
# В одном терминале запустить upstream
cd tests
uvicorn echo_app:app --host 127.0.0.1 --port 9001 --workers 1
# Второй
uvicorn echo_app:app --host 127.0.0.1 --port 9002 --workers 1

# Или  простой HTTP сервер
python3 -m http.server 9001
```

### 2. Запустить proxy сервер

```bash
# В другом терминале
python3 -m proxy.main
```

### 3. Протестировать проксирование

```bash
# GET запрос
curl -v http://127.0.0.1:8080/

# POST запрос с телом
curl -v -X POST http://127.0.0.1:8080/test -H "Content-Type: text/plain" -d 'hello world'

# Запрос с большим телом (проверка стриминга)
# Вариант 1: через файл (рекомендуется)
head -c 1000000 /dev/urandom | base64 > /tmp/large_body.txt
curl -v -X POST http://127.0.0.1:8080/echo -d @/tmp/large_body.txt

# Вариант 2: через pipe (для меньших размеров)
head -c 100000 /dev/urandom | base64 | curl -v -X POST http://127.0.0.1:8080/echo --data-binary @-

# Вариант 3: создать тестовый файл заранее
echo "test data" | head -c 10000 | curl -v -X POST http://127.0.0.1:8080/echo --data-binary @-
```

### Протестировать таймауты 

Выключим сервер апстрима на uvicorn и отправим запрос прокси, он должен вернуть 502 Bad Gateway из-за невозможности установить соединение.

```bash
  $ curl -v http://127.0.0.1:8080/
  *   Trying 127.0.0.1:8080...
  * Established connection to 127.0.0.1 (127.0.0.1 port 8080) from 127.0.0.1 port 39742 
  * using HTTP/1.x
  > GET / HTTP/1.1
  > Host: 127.0.0.1:8080
  > User-Agent: curl/8.18.0
  > Accept: */*
  > 
  * Request completely sent off
  < HTTP/1.1 502 Bad Gateway
  < Content-Type: text/plain
  < Connection: close
  < 
  * shutting down connection #0
  Upstream unavailable: [Errno 111] Connect call failed ('127.0.0.1', 9001)
```

### Тестирование балансировки

Для тестирования балансировки запустите несколько upstream серверов:

```bash
# Терминал 1: Первый upstream на порту 9001
cd tests
uvicorn echo_app:app --host 127.0.0.1 --port 9001

# Терминал 2: Второй upstream на порту 9002  
cd tests
uvicorn echo_app:app --host 127.0.0.1 --port 9002

# Терминал 3: Прокси сервер
python3 -m proxy.main

# Терминал 4: Тестирование
# Делайте несколько запросов и смотрите в логах прокси,
# какой upstream был выбран для каждого запроса
curl http://127.0.0.1:8080/
curl http://127.0.0.1:8080/
curl http://127.0.0.1:8080/
```

В логах прокси вы увидите:
```
Selected upstream 127.0.0.1:9001 for GET / (round-robin)
Selected upstream 127.0.0.1:9002 for GET / (round-robin)
Selected upstream 127.0.0.1:9001 for GET / (round-robin)
```

## Ошибки и фиксы

- При отправке запроса на прокси, в ответ получал зависание сессии и текст: 
`* Request completely sent off`
- echo текст не возвращался от апстрима

> Две проблемы:

> Для GET-запросов без тела не нужно читать тело до EOF.
> Чтение ответа от upstream до EOF может зависнуть при keep-alive.

### Исправления:

1. Обработка тела запроса (utils/http.py):
- Проверка наличия тела по Content-Length или Transfer-Encoding
- Для GET-запросов тело не читается
- Для POST/PUT/PATCH с известным размером читается ровно столько байт

2. Заголовок Connection: close (utils/http.py):
- Добавляется в запрос к upstream, чтобы соединение закрывалось после ответа
- Упрощает чтение ответа (до EOF)

3. Чтение ответа от upstream (client_handler.py):
- Добавлена проверка at_eof() для определения закрытия соединения
- Улучшено логирование для отладки
- Обработка случая, когда данных нет, но соединение еще открыто