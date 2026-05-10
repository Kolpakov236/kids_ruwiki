#!/usr/bin/env python3
"""
Скрипт для проверки здоровья системы Рувик Kids.
Проверяет:
1. Доступность Рувики API
2. Настройку LLM провайдера
3. Работоспособность LLM API
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем путь к модулям приложения
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from app.settings import settings


async def check_ruwiki_api():
    """Проверяет доступность API Рувики."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Простой запрос к API Рувики
            response = await client.get(
                f"{settings.ruwiki_api_base}/page/html/Фотосинтез",
                follow_redirects=True
            )
            if response.status_code == 200:
                return True, "✅ API Рувики доступен"
            else:
                return False, f"❌ API Рувики недоступен (статус: {response.status_code})"
    except Exception as e:
        return False, f"❌ Ошибка подключения к API Рувики: {str(e)}"


async def check_llm_config():
    """Проверяет конфигурацию LLM провайдера."""
    provider = settings.llm_provider
    model = settings.llm_model
    base_url = settings.llm_base_url
    
    # Проверяем наличие API ключа для некоторых провайдеров
    api_key_configured = False
    if provider == "gemini":
        api_key_configured = bool(settings.llm_api_key or settings.gemini_api_key or settings.google_api_key)
    elif provider == "openai_compatible":
        api_key_configured = bool(settings.llm_api_key)
    elif provider == "ollama":
        api_key_configured = True  # Ollama не требует ключа
    
    config_status = f"✅ Конфигурация LLM: {provider} ({model})"
    if not api_key_configured and provider in ["gemini", "openai_compatible"]:
        config_status = f"⚠️  Конфигурация LLM: {provider} ({model}) - API ключ не настроен"
    
    return api_key_configured, config_status


async def check_llm_api():
    """Проверяет доступность LLM API (базовый тест)."""
    provider = settings.llm_provider
    
    if provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{settings.llm_base_url.rstrip('/')}/api/tags")
                if response.status_code == 200:
                    return True, "✅ Ollama API доступен"
                else:
                    return False, f"❌ Ollama API недоступен (статус: {response.status_code})"
        except Exception as e:
            return False, f"❌ Ошибка подключения к Ollama: {str(e)}"
    
    elif provider in ["gemini", "openai_compatible"]:
        # Для облачных провайдеров просто проверяем конфигурацию
        api_key_configured, _ = await check_llm_config()
        if api_key_configured:
            return True, f"✅ {provider} API настроен (требуется тестовый запрос для проверки)"
        else:
            return False, f"❌ {provider} API не настроен (отсутствует API ключ)"
    
    return False, f"❌ Неизвестный провайдер: {provider}"


async def main():
    """Основная функция проверки здоровья."""
    print("=" * 60)
    print("Проверка здоровья системы Рувик Kids")
    print("=" * 60)
    
    # Загружаем настройки
    print(f"\n📋 Загруженные настройки:")
    print(f"  • Провайдер LLM: {settings.llm_provider}")
    print(f"  • Модель: {settings.llm_model}")
    print(f"  • Base URL: {settings.llm_base_url}")
    print(f"  • API ключ настроен: {'Да' if settings.llm_api_key else 'Нет'}")
    
    # Проверяем компоненты
    print("\n🔍 Проверка компонентов:")
    
    # 1. Проверка Рувики API
    print("\n1. Проверка API Рувики...")
    ruwiki_ok, ruwiki_msg = await check_ruwiki_api()
    print(f"   {ruwiki_msg}")
    
    # 2. Проверка конфигурации LLM
    print("\n2. Проверка конфигурации LLM...")
    llm_config_ok, llm_config_msg = await check_llm_config()
    print(f"   {llm_config_msg}")
    
    # 3. Проверка LLM API
    print("\n3. Проверка LLM API...")
    llm_api_ok, llm_api_msg = await check_llm_api()
    print(f"   {llm_api_msg}")
    
    # Итог
    print("\n" + "=" * 60)
    print("ИТОГ:")
    
    all_ok = ruwiki_ok and llm_config_ok and llm_api_ok
    
    if all_ok:
        print("✅ Все системы работают нормально!")
        print("\nСледующие шаги:")
        print("1. Запустите бэкенд: uvicorn app.main:app --reload --port 8000")
        print("2. Откройте frontend в браузере")
        print("3. Протестируйте с демо-темами")
    else:
        print("⚠️  Есть проблемы с настройкой:")
        if not ruwiki_ok:
            print("   • API Рувики недоступен")
        if not llm_config_ok:
            print("   • Конфигурация LLM неполная")
        if not llm_api_ok:
            print("   • LLM API недоступен")
        
        print("\nРекомендации:")
        print("1. Проверьте файл .env в папке backend")
        print("2. Убедитесь, что API ключи корректны")
        print("3. Проверьте подключение к интернету")
        print("4. Для Ollama: убедитесь, что сервер запущен")
    
    print("=" * 60)
    
    return 0 if all_ok else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)