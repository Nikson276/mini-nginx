"""
Простой пример, демонстрирующий как работают таймауты через asyncio.wait_for()

Запуск:
    python3 docs/timeout_example.py
"""

import asyncio


async def slow_operation(duration: float):
    """
    Медленная операция, которая занимает duration секунд.
    """
    print(f"Начало операции (займет {duration} секунд)...")
    await asyncio.sleep(duration)
    print(f"Операция завершена!")
    return f"Результат после {duration} секунд"


async def example_with_timeout():
    """
    Пример использования wait_for с таймаутом.
    """
    print("=" * 60)
    print("Пример 1: Операция завершается ДО таймаута")
    print("=" * 60)
    
    try:
        # Создаем корутину (операция еще не началась!)
        coro = slow_operation(2.0)  # Займет 2 секунды
        
        # Оборачиваем в wait_for с таймаутом 5 секунд
        # wait_for запустит выполнение корутины и будет ждать максимум 5 секунд
        result = await asyncio.wait_for(coro, timeout=5.0)
        print(f"✅ Успешно: {result}")
    except asyncio.TimeoutError:
        print("❌ Таймаут! Операция заняла больше 5 секунд")
    
    print("\n" + "=" * 60)
    print("Пример 2: Операция занимает БОЛЬШЕ таймаута")
    print("=" * 60)
    
    try:
        # Создаем корутину, которая займет 10 секунд
        coro = slow_operation(10.0)
        
        # Но таймаут всего 3 секунды
        result = await asyncio.wait_for(coro, timeout=3.0)
        print(f"✅ Успешно: {result}")
    except asyncio.TimeoutError:
        print("❌ Таймаут! Операция заняла больше 3 секунд")
        print("   wait_for отменил выполнение корутины")


async def example_coroutine_vs_result():
    """
    Демонстрация разницы между передачей корутины и результата.
    """
    print("\n" + "=" * 60)
    print("Пример 3: Корутина vs Результат")
    print("=" * 60)
    
    print("\n❌ НЕПРАВИЛЬНО - передаем результат (уже выполненный):")
    try:
        # Мы уже выполнили корутину с await
        result = await slow_operation(2.0)  # Операция уже выполнена!
        print(f"Результат: {result}")
        
        # Теперь пытаемся применить таймаут к результату (не к корутине!)
        # Это не сработает, потому что операция уже завершена
        await asyncio.wait_for(result, timeout=1.0)  # ❌ Ошибка!
    except TypeError as e:
        print(f"Ошибка: {e}")
        print("   Результат - это не корутина, wait_for не может его обработать")
    
    print("\n✅ ПРАВИЛЬНО - передаем корутину (еще не выполненную):")
    try:
        # Создаем корутину (операция еще НЕ началась!)
        coro = slow_operation(2.0)  # Это корутина, не результат
        
        # Передаем корутину в wait_for
        # wait_for запустит выполнение и будет ждать максимум 1 секунду
        result = await asyncio.wait_for(coro, timeout=1.0)
        print(f"Результат: {result}")
    except asyncio.TimeoutError:
        print("❌ Таймаут! Операция заняла больше 1 секунды")


async def example_nested_timeouts():
    """
    Пример вложенных таймаутов.
    """
    print("\n" + "=" * 60)
    print("Пример 4: Вложенные таймауты")
    print("=" * 60)
    
    async def inner_operation():
        """Внутренняя операция с таймаутом 2 секунды"""
        print("  Внутренняя операция началась...")
        await asyncio.sleep(1.5)
        print("  Внутренняя операция завершена")
        return "inner_result"
    
    async def outer_operation():
        """Внешняя операция с таймаутом 5 секунд"""
        print("Внешняя операция началась...")
        # Внутри внешней операции вызываем внутреннюю с таймаутом
        result = await asyncio.wait_for(inner_operation(), timeout=2.0)
        print("Внешняя операция завершена")
        return result
    
    try:
        # Внешний таймаут 5 секунд, внутренний 2 секунды
        result = await asyncio.wait_for(outer_operation(), timeout=5.0)
        print(f"✅ Успешно: {result}")
    except asyncio.TimeoutError:
        print("❌ Таймаут на внешнем или внутреннем уровне")


if __name__ == '__main__':
    print("Демонстрация работы asyncio.wait_for() с таймаутами\n")
    
    # Запускаем все примеры
    asyncio.run(example_with_timeout())
    asyncio.run(example_coroutine_vs_result())
    asyncio.run(example_nested_timeouts())
    
    print("\n" + "=" * 60)
    print("Ключевые моменты:")
    print("=" * 60)
    print("1. Корутина - это объект, представляющий асинхронную операцию")
    print("2. wait_for(coro, timeout) запускает корутину и ждет с таймаутом")
    print("3. Если таймаут - операция отменяется, выбрасывается TimeoutError")
    print("4. Передавать нужно корутину, а не результат await!")
