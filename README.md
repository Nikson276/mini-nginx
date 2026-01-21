# Мини‑Nginx на asyncio (reverse proxy)

## Запуск сервера

### Из корня проекта:

```bash
# Запуск с параметрами по умолчанию (127.0.0.1:8080)
python3 -m proxy.main

# Или с указанием хоста и порта
python3 -m proxy.main 127.0.0.1 8080
```

### Альтернативный способ:

```bash
# Установить PYTHONPATH и запустить
export PYTHONPATH=/home/nikson/Dev/CursorProjects/mini-nginx:$PYTHONPATH
python3 proxy/main.py
```

## Текущий статус

- ✅ TCP сервер принимает соединения
- ✅ Парсер HTTP-запросов (метод, путь, версия, заголовки, тело как raw-stream)
- ✅ Проксирование к одному upstream с двунаправленным стримингом
- ✅ Backpressure через `drain()` для предотвращения переполнения буферов
- ⏳ Таймауты (следующий шаг)
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

## Тестирование

### 1. Запустите upstream сервер

Сначала нужно запустить тестовый upstream сервер (например, из папки `tests/`):

```bash
# В одном терминале запустите upstream
cd tests
uvicorn echo_app:app --host 127.0.0.1 --port 9001 --workers 1

# Или используйте простой HTTP сервер
python3 -m http.server 9001
```

### 2. Запустите proxy сервер

```bash
# В другом терминале
python3 -m proxy.main
```

### 3. Протестируйте проксирование

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

- При отправке запроса на прокси, в ответ получал зависание сессии и текст: `* Request completely sent off`
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

### Конфигурация upstream

По умолчанию прокси направляет запросы на `127.0.0.1:9001`. 
Чтобы изменить, отредактируйте константы в `proxy/proxy_server.py`:

```python
UPSTREAM_HOST = '127.0.0.1'
UPSTREAM_PORT = 9001
```