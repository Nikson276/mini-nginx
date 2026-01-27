# Мини‑Nginx на asyncio (reverse proxy)

## Запуск сервера

### Из корня проекта:

```bash
# Запуск с параметрами по умолчанию (127.0.0.1:8080)
python3 -m proxy.main

# Или с указанием хоста и порта
python3 -m proxy.main 127.0.0.1 8080
```

## Текущий статус

- ✅ TCP сервер принимает соединения
- ✅ Парсер HTTP-запросов (метод, путь, версия, заголовки, тело как raw-stream)
- ✅ Проксирование к одному upstream с двунаправленным стримингом
- ✅ Backpressure через `drain()` для предотвращения переполнения буферов
- ✅ Таймауты на все операции (connect, read, write, total)
- ⏳ Балансировка round-robin по нескольким upstream (в разработке)

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

Прокси-сервер использует систему таймаутов для защиты от зависших соединений и медленных upstream серверов. Все таймауты настраиваются через класс `TimeoutPolicy`:

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

**Подробное объяснение:** см. [docs/timeouts_explanation.md](../docs/timeouts_explanation.md)  
**Рабочий пример:** запустите `python3 docs/timeout_example.py`

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

## Тестирование

### 1. Запустить upstream сервер

Сначала нужно запустить тестовый upstream сервер (например, из папки `tests/`):

```bash
# В одном терминале запустить upstream
cd tests
uvicorn echo_app:app --host 127.0.0.1 --port 9001 --workers 1

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

#### Ошибки и фиксы

- При отправке запроса на прокси, в ответ получал зависание сессии и текст: 
`* Request completely sent off`
- echo текст не возвращался от апстрима

> Две проблемы:

> Для GET-запросов без тела не нужно читать тело до EOF.
> Чтение ответа от upstream до EOF может зависнуть при keep-alive.

##### Исправления:

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

#### Результаты

##### GET запрос

```bash
*   Trying 127.0.0.1:8080...
* Established connection to 127.0.0.1 (127.0.0.1 port 8080) from 127.0.0.1 port 33496 
* using HTTP/1.x
> GET / HTTP/1.1
> Host: 127.0.0.1:8080
> User-Agent: curl/8.18.0
> Accept: */*
> 
* Request completely sent off
< HTTP/1.1 200 OK
< date: Wed, 21 Jan 2026 23:36:43 GMT
< server: uvicorn
< content-length: 204
< content-type: application/json
< connection: close
< 
{
  "method": "GET",
  "path": "/",
  "headers": {
    "host": "127.0.0.1:8080",
    "user-agent": "curl/8.18.0",
    "accept": "*/*",
    "connection": "close"
  },
  "body": null,
  "query_params": {}
* shutting down connection #0
```

##### POST запрос

```bash
*   Trying 127.0.0.1:8080...
* Established connection to 127.0.0.1 (127.0.0.1 port 8080) from 127.0.0.1 port 54302 
* using HTTP/1.x
> POST /test HTTP/1.1
> Host: 127.0.0.1:8080
> User-Agent: curl/8.18.0
> Accept: */*
> Content-Type: text/plain
> Content-Length: 11
> 
* upload completely sent off: 11 bytes
< HTTP/1.1 200 OK
< date: Wed, 21 Jan 2026 23:43:26 GMT
< server: uvicorn
< content-length: 280
< content-type: application/json
< connection: close
< 
{
  "method": "POST",
  "path": "/test",
  "headers": {
    "host": "127.0.0.1:8080",
    "user-agent": "curl/8.18.0",
    "accept": "*/*",
    "content-type": "text/plain",
    "content-length": "11",
    "connection": "close"
  },
  "body": "hello world",
  "query_params": {}
* shutting down connection #0
```

##### POST with file

```bash
*   Trying 127.0.0.1:8080...
* Established connection to 127.0.0.1 (127.0.0.1 port 8080) from 127.0.0.1 port 47826 
* using HTTP/1.x
> POST /echo HTTP/1.1
> Host: 127.0.0.1:8080
> User-Agent: curl/8.18.0
> Accept: */*
> Content-Length: 10
> Content-Type: application/x-www-form-urlencoded
> 
* upload completely sent off: 10 bytes
< HTTP/1.1 200 OK
< date: Wed, 21 Jan 2026 23:46:58 GMT
< server: uvicorn
< content-length: 303
< content-type: application/json
< connection: close
< 
{
  "method": "POST",
  "path": "/echo",
  "headers": {
    "host": "127.0.0.1:8080",
    "user-agent": "curl/8.18.0",
    "accept": "*/*",
    "content-length": "10",
    "content-type": "application/x-www-form-urlencoded",
    "connection": "close"
  },
  "body": "test data\n",
  "query_params": {}
* shutting down connection #0
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

### Конфигурация upstream

По умолчанию прокси направляет запросы на `127.0.0.1:9001`. 
Чтобы изменить, отредактируйте константы в `proxy/proxy_server.py`:

```python
UPSTREAM_HOST = '127.0.0.1'
UPSTREAM_PORT = 9001
```

### Конфигурация таймаутов

Таймауты настраиваются через `TimeoutPolicy` в `proxy/proxy_server.py`:

```python
from proxy.timeouts import TimeoutPolicy

# Кастомная политика таймаутов
TIMEOUT_POLICY = TimeoutPolicy(
    connect_ms=1000,   # 1 секунда на подключение
    read_ms=15000,     # 15 секунд на чтение
    write_ms=15000,    # 15 секунд на запись
    total_ms=30000     # 30 секунд общий таймаут
)
```

По умолчанию используются значения из `DEFAULT_TIMEOUT_POLICY`:
- `connect_ms=1000` (1 секунда)
- `read_ms=15000` (15 секунд)
- `write_ms=15000` (15 секунд)
- `total_ms=30000` (30 секунд)