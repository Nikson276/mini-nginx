# Тесты для mini-nginx

Этот каталог содержит юнит-тесты и интеграционные тесты для прокси-сервера.

## Структура тестов

- `test_timeouts.py` - Тесты для TimeoutPolicy и работы таймаутов
- `test_upstream_pool.py` - Тесты для UpstreamPool и round-robin балансировки
- `test_limits.py` - Тесты для ConnectionLimitManager и Semaphore лимитов
- `test_limits_integration.sh` - Интеграционный тест для проверки лимитов с реальными upstream
- `echo_app.py` - Тестовый upstream сервер для интеграционных тестов
- `conftest.py` - Конфигурация pytest

## Запуск тестов

### Установка зависимостей

```bash
pip install pytest pytest-asyncio
```

### Запуск всех тестов

```bash
# Из корня проекта
pytest tests/

# С подробным выводом
pytest tests/ -v

# С выводом print statements
pytest tests/ -v -s
```

### Запуск конкретного теста

```bash
# Тесты для таймаутов
pytest tests/test_timeouts.py -v

# Тесты для upstream pool
pytest tests/test_upstream_pool.py -v

# Тесты для лимитов
pytest tests/test_limits.py -v
```

### Интеграционный тест лимитов

```bash
# 1. Запустите прокси сервер (в одном терминале)
python3 -m proxy.main

# 2. Запустите два upstream сервера (в других терминалах)
uvicorn tests.echo_app:app --host 127.0.0.1 --port 9001
uvicorn tests.echo_app:app --host 127.0.0.1 --port 9002

# 3. Запустите интеграционный тест
./tests/test_limits_integration.sh
```

## Что тестируется

### test_timeouts.py
- ✅ Значения таймаутов по умолчанию
- ✅ Конвертация миллисекунд в секунды
- ✅ Успешное выполнение операций в пределах таймаута
- ✅ TimeoutError при превышении таймаута
- ✅ Все типы таймаутов (connect, read, write, total)

### test_upstream_pool.py
- ✅ Создание pool с несколькими upstream
- ✅ Round-robin распределение запросов
- ✅ Циклический переход между upstream
- ✅ Работа с concurrent запросами
- ✅ Обработка ошибок (пустой список upstream)

### test_limits.py
- ✅ Создание ConnectionLimitManager
- ✅ Лимит клиентских соединений через Semaphore
- ✅ Лимит соединений к upstream через Semaphore
- ✅ Разные семафоры для разных upstream
- ✅ Освобождение семафоров при выходе из контекста
- ✅ Статистика соединений

### test_limits_integration.sh
- ✅ Параллельные запросы к прокси
- ✅ Проверка работы лимитов с реальными upstream
- ✅ Проверка round-robin распределения

## Написание новых тестов

При добавлении новых компонентов создавайте соответствующие тесты:

```python
"""Unit tests for NewComponent."""

import pytest
from proxy.new_component import NewComponent


class TestNewComponent:
    """Tests for NewComponent."""
    
    def test_basic_functionality(self):
        """Test basic functionality."""
        component = NewComponent()
        assert component is not None
    
    @pytest.mark.asyncio
    async def test_async_operation(self):
        """Test async operation."""
        component = NewComponent()
        result = await component.async_method()
        assert result == expected_value
```

## Best Practices

1. **Именование**: Тесты должны иметь понятные имена, описывающие что тестируется
2. **Изоляция**: Каждый тест должен быть независимым
3. **Покрытие**: Тестируйте основные сценарии и граничные случаи
4. **Документация**: Добавляйте docstrings к тестам для понимания их назначения
5. **Не на каждый чих**: Пишите тесты для крупных блоков и декомпозированных задач
