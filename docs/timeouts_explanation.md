# Как работают таймауты через asyncio.wait_for()

## Что такое корутина?

Корутина (coroutine) - это специальный объект в Python, который представляет асинхронную операцию. Когда вы вызываете асинхронную функцию с `await`, она возвращает корутину.

```python
# Пример: asyncio.open_connection() возвращает корутину
coro = asyncio.open_connection('127.0.0.1', 9001)
# coro - это корутина, еще не выполненная!

# Чтобы выполнить корутину, нужно использовать await
reader, writer = await coro
```

## Как работает asyncio.wait_for()

`asyncio.wait_for(coro, timeout=seconds)` - это функция, которая:
1. **Принимает корутину** (не результат, а саму корутину!)
2. **Запускает её выполнение** в event loop
3. **Отслеживает время выполнения**
4. **Если выполнение превышает timeout** - отменяет корутину и выбрасывает `asyncio.TimeoutError`
5. **Если выполнение завершилось вовремя** - возвращает результат

### Внутренняя работа wait_for (упрощенно):

```python
async def wait_for(coro, timeout):
    # 1. Создаем задачу (Task) из корутины
    task = asyncio.create_task(coro)
    
    # 2. Ждем либо завершения задачи, либо таймаута
    try:
        # Ждем с таймаутом
        result = await asyncio.wait_for_task(task, timeout)
        return result
    except asyncio.TimeoutError:
        # Если таймаут - отменяем задачу
        task.cancel()
        raise asyncio.TimeoutError("Operation timed out")
```

## Примеры из нашего кода

### Пример 1: Connect Timeout

```python
# В client_handler.py:
try:
    # asyncio.open_connection() возвращает корутину
    # Мы передаем эту корутину в with_connect_timeout()
    upstream_reader, upstream_writer = await self.timeout_policy.with_connect_timeout(
        asyncio.open_connection(upstream_host, upstream_port)
    )
except asyncio.TimeoutError:
    # Если подключение заняло больше 1 секунды
    logger.error('Connection timed out')
    raise
```

**Что происходит пошагово:**

1. `asyncio.open_connection(host, port)` вызывается и возвращает **корутину** (еще не выполненную!)
2. Эта корутина передается в `with_connect_timeout(coro)`
3. Внутри `with_connect_timeout()`:
   ```python
   async def with_connect_timeout(self, coro):
       # coro - это корутина от open_connection()
       # wait_for запускает выполнение корутины и ждет максимум 1 секунду
       return await asyncio.wait_for(coro, timeout=1.0)
   ```
4. `asyncio.wait_for()`:
   - Запускает выполнение корутины `open_connection()`
   - Начинает отсчет времени (1 секунда)
   - Если подключение успешно за 1 секунду → возвращает `(reader, writer)`
   - Если подключение занимает больше 1 секунды → отменяет операцию и выбрасывает `TimeoutError`

### Пример 2: Read Timeout

```python
# В client_handler.py:
try:
    # upstream_reader.read() возвращает корутину
    # Каждая операция чтения обернута в таймаут
    chunk = await self.timeout_policy.with_read_timeout(
        upstream_reader.read(chunk_size)
    )
except asyncio.TimeoutError:
    # Если чтение заняло больше 15 секунд
    logger.error('Read timed out')
    raise
```

**Что происходит:**

1. `upstream_reader.read(chunk_size)` возвращает корутину (операция чтения еще не началась!)
2. Корутина передается в `with_read_timeout()`
3. `wait_for()` запускает чтение и ждет максимум 15 секунд
4. Если данные пришли за 15 секунд → возвращает данные
5. Если данных нет 15 секунд → отменяет чтение и выбрасывает `TimeoutError`

### Пример 3: Write Timeout

```python
# В client_handler.py:
try:
    # request.write_to_upstream() - это async функция, возвращает корутину
    await self.timeout_policy.with_write_timeout(
        request.write_to_upstream(upstream_writer)
    )
except asyncio.TimeoutError:
    logger.error('Write timed out')
    raise
```

**Что происходит:**

1. `request.write_to_upstream(upstream_writer)` возвращает корутину
2. Вся операция записи (headers + body) обернута в таймаут 15 секунд
3. Если запись завершилась за 15 секунд → OK
4. Если запись заняла больше 15 секунд → `TimeoutError`

## Важные моменты

### 1. Корутина передается, а не результат

```python
# ❌ НЕПРАВИЛЬНО - мы уже выполнили корутину!
result = await asyncio.open_connection(host, port)
await wait_for(result, timeout=1.0)  # result уже выполнен, таймаут не сработает!

# ✅ ПРАВИЛЬНО - передаем корутину
coro = asyncio.open_connection(host, port)  # корутина еще не выполнена
result = await wait_for(coro, timeout=1.0)  # wait_for запустит выполнение
```

### 2. wait_for отменяет задачу при таймауте

Когда происходит таймаут, `wait_for()`:
- Вызывает `task.cancel()` на задаче
- Это приводит к `CancelledError` внутри корутины
- Ресурсы должны быть освобождены в `finally` блоках

### 3. Вложенные таймауты

```python
# Total timeout оборачивает весь процесс
await timeout_policy.with_total_timeout(
    # Внутри могут быть другие таймауты
    await timeout_policy.with_connect_timeout(
        asyncio.open_connection(...)
    )
)
```

Если внутренний таймаут сработает раньше - он выбросит исключение.
Если внешний таймаут сработает раньше - он отменит всю внутреннюю операцию.

## Визуализация выполнения

```
Время →
0s    1s    2s    3s    4s    5s
|-----|-----|-----|-----|-----|
      ↑
      connect_timeout (1s)
      
      Если подключение заняло 0.5s → ✅ OK
      Если подключение заняло 1.5s → ❌ TimeoutError
```

```
Время →
0s    5s    10s   15s   20s
|-----|-----|-----|-----|
                    ↑
                    read_timeout (15s)
                    
      Если данные пришли за 2s → ✅ OK
      Если данных нет 16s → ❌ TimeoutError
```

## Практический пример

Давайте посмотрим на реальный код из `client_handler.py`:

```python
# Шаг 1: Создаем корутину подключения
connect_coro = asyncio.open_connection(upstream_host, upstream_port)

# Шаг 2: Оборачиваем в таймаут
try:
    # wait_for запустит выполнение connect_coro и будет ждать максимум 1 секунду
    upstream_reader, upstream_writer = await self.timeout_policy.with_connect_timeout(
        connect_coro
    )
except asyncio.TimeoutError:
    # Если прошло больше 1 секунды - подключение отменено, выбрасываем ошибку
    logger.error('Connection timed out')
    raise
```

**Что происходит внутри `with_connect_timeout()`:**

```python
async def with_connect_timeout(self, coro):
    # coro = корутина от open_connection()
    # timeout = 1.0 секунда
    
    # wait_for делает следующее:
    # 1. Создает задачу: task = create_task(coro)
    # 2. Ждет завершения задачи с таймаутом 1.0 секунда
    # 3. Если задача завершилась за 1.0s → возвращает результат
    # 4. Если прошло 1.0s и задача не завершилась → отменяет задачу и выбрасывает TimeoutError
    return await asyncio.wait_for(coro, timeout=self.connect_timeout())
```

## Итог

1. **Корутина** - это объект, представляющий асинхронную операцию (еще не выполненную)
2. **wait_for(coro, timeout)** принимает корутину, запускает её и ждет с таймаутом
3. **Если таймаут** - операция отменяется, выбрасывается `TimeoutError`
4. **Если успешно** - возвращается результат корутины
5. **Методы-обертки** (`with_connect_timeout`, etc.) - это удобные функции, которые применяют `wait_for` с нужным таймаутом
