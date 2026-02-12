# Deployment and tests

## Запуск сервера

### Docker Compose

Поднимает прокси + два upstream, k6 нагрузочные тесты можно запустить с профилем отдельно в той же сети

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

#### Тест лимитов соединений (локально)

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

#### 1. Запустить upstream сервер

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

#### 2. Запустить proxy сервер

```bash
# В другом терминале
python3 -m proxy.main
```

#### 3. Протестировать проксирование

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

#### Протестировать таймауты 

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

#### Тестирование балансировки

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