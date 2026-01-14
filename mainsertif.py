import uuid
import requests
import json
import logging
import os
import time
from rich.console import Console
from rich.table import Table
from rich import box
from config.settings import cfg

# Настройка логирования ошибок в файл
logging.basicConfig(
    filename='api_errors.log', 
    level=logging.ERROR, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

console = Console()

def download_russia_cert(target_path):
    """Автоматически скачивает сертификат Минцифры, если его нет"""
    url = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
    
    if os.path.exists(target_path):
        if os.path.getsize(target_path) > 0:
            return True
        else:
            console.print(f"[yellow]⚠️ Файл {target_path} пустой. Перекачиваю...[/yellow]")
    
    console.print(f"[cyan]⬇️ Скачиваю сертификат Минцифры с {url}...[/cyan]")
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        response = requests.get(url, verify=False, timeout=10)
        if response.status_code == 200:
            with open(target_path, 'wb') as f:
                f.write(response.content)
            console.print(f"[green]✅ Сертификат сохранен: {target_path}[/green]")
            return True
        else:
            console.print(f"[red]❌ Ошибка скачивания: {response.status_code}[/red]")
            return False
    except Exception as e:
        console.print(f"[red]❌ Ошибка сети при скачивании: {e}[/red]")
        logging.error(f"Cert download error: {e}", exc_info=True)
        return False

def test_openai_compatible(model_name, api_key, base_url, provider_label):
    """
    Универсальный тест для DeepSeek, OpenRouter и RouterAI.
    Делает реальный запрос: 'Что такое любовь?'
    """
    if not api_key:
        return "[bold red]❌ NO KEY[/bold red]"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost", # Для OpenRouter
        "X-Title": "AI Agent Test"          # Для OpenRouter
    }
    
    # Реальный вопрос
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": "Что такое любовь? (ответь философски, но кратко - 1 предложение)"}
        ],
        "max_tokens": 100,
        "temperature": 0.7
    }
    
    start_time = time.time()
    try:
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=20)
        elapsed = time.time() - start_time
        
        if resp.status_code == 200:
            data = resp.json()
            try:
                reply = data['choices'][0]['message']['content'].strip()
                # Обрезаем длинный ответ для таблицы
                display_reply = (reply[:70] + '...') if len(reply) > 70 else reply
                return f"[bold green]OK ({elapsed:.2f}s):[/bold green] {display_reply}"
            except (KeyError, IndexError) as e:
                logging.error(f"JSON Parse Error for {model_name}: {data}", exc_info=True)
                return f"[red]⚠️ JSON Format Error[/red]"
        else:
            error_msg = resp.text[:100]
            logging.error(f"API Error {model_name} ({resp.status_code}): {resp.text}")
            return f"[bold red]ERR {resp.status_code}:[/bold red] {error_msg}"
            
    except Exception as e:
        logging.error(f"Connection Exception for {model_name}: {e}", exc_info=True)
        return f"[bold red]CONN FAIL:[/bold red] {str(e)[:30]}"

def test_gigachat_full():
    """Тест GigaChat: Авторизация + Реальный диалог"""
    if not cfg.GIGACHAT_AUTH_KEY: 
        return "[bold red]❌ NO KEY[/bold red]"
    
    # Проверка сертификата
    verify_ssl = cfg.GIGACHAT_CA_BUNDLE
    if not verify_ssl or not os.path.exists(verify_ssl):
        # Пробуем скачать
        if cfg.GIGACHAT_CA_BUNDLE:
            download_russia_cert(cfg.GIGACHAT_CA_BUNDLE)
        else:
            return "[red]⚠️ Cert path not set[/red]"

    # 1. Получаем токен
    auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    auth_headers = {
        'RqUID': str(uuid.uuid4()),
        'Authorization': f'Basic {cfg.GIGACHAT_AUTH_KEY}'
    }
    
    try:
        # АВТОРИЗАЦИЯ
        auth_resp = requests.post(
            auth_url, 
            headers=auth_headers, 
            data={'scope': cfg.GIGACHAT_SCOPE}, 
            verify=verify_ssl, 
            timeout=10
        )
        
        if auth_resp.status_code != 200:
            logging.error(f"GigaChat Auth Error: {auth_resp.text}")
            return f"[red]Auth Err {auth_resp.status_code}[/red]"
            
        access_token = auth_resp.json()['access_token']
        
        # 2. ОТПРАВКА СООБЩЕНИЯ
        chat_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        chat_headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        payload = {
            "model": "GigaChat",
            "messages": [{"role": "user", "content": "Что такое любовь? (1 фраза)"}],
            "max_tokens": 50
        }
        
        start_time = time.time()
        chat_resp = requests.post(chat_url, headers=chat_headers, json=payload, verify=verify_ssl, timeout=15)
        elapsed = time.time() - start_time
        
        if chat_resp.status_code == 200:
            reply = chat_resp.json()['choices'][0]['message']['content']
            return f"[bold green]OK ({elapsed:.2f}s):[/bold green] {reply[:60]}..."
        else:
            logging.error(f"GigaChat API Error: {chat_resp.text}")
            return f"[red]Chat Err {chat_resp.status_code}[/red]"
        
    except requests.exceptions.SSLError:
        logging.error("GigaChat SSL Error", exc_info=True)
        return f"[bold red]SSL VERIFY FAIL[/bold red]"
    except Exception as e:
        logging.error(f"GigaChat Exception: {e}", exc_info=True)
        return f"[red]Ex:[/red] {str(e)[:30]}"

def run_checks():
    console.print("\n[bold yellow]🚀 ЗАПУСК ДИАГНОСТИКИ ПОДКЛЮЧЕНИЙ...[/bold yellow]")
    console.print(f"[dim]Логи ошибок пишутся в 'api_errors.log'[/dim]\n")

    table = Table(title="📊 Результаты теста моделей (Вопрос: 'Что такое любовь?')", box=box.ROUNDED)
    table.add_column("Провайдер / Модель", style="cyan", no_wrap=True)
    table.add_column("Статус и Ответ", style="white")
    
    # Список тестов
    tasks = [
        # 1. DeepSeek (Direct)
        ("DeepSeek V3 (Direct)", cfg.MODEL_NORMAL, cfg.DEEPSEEK_API_KEY, cfg.DEEPSEEK_BASE_URL),
        
        # 2. Новые модели генератора (OpenRouter)
        ("GLM 4.7 (OpenRouter)", cfg.MODEL_GLM_4_7, cfg.OPENROUTER_API_KEY, cfg.OPENROUTER_BASE_URL),
        ("Claude Haiku 4.5 (OpenRouter)", cfg.MODEL_HAIKU_4_5, cfg.OPENROUTER_API_KEY, cfg.OPENROUTER_BASE_URL),
        
        # 3. Существующие (для проверки)
        ("Qwen (OpenRouter)", cfg.MODEL_QWEN or "Not Set", cfg.OPENROUTER_API_KEY, cfg.OPENROUTER_BASE_URL),
    ]

    with console.status("[bold green]🔄 Опрос нейросетей... Пожалуйста, подождите.[/bold green]") as status:
        
        # Проходим по стандартным моделям
        for label, model_id, key, url in tasks:
            if model_id == "Not Set":
                table.add_row(label, "[dim]Пропущено (не задана модель)[/dim]")
                continue
                
            status.update(f"[bold green]📞 Звоним в {label}...[/bold green]")
            result = test_openai_compatible(model_id, key, url, label)
            table.add_row(label, result)
            
        # Отдельный тест GigaChat
        status.update("[bold green]🛡️ Проверка GigaChat (SSL)...[/bold green]")
        sb_res = test_gigachat_full()
        table.add_row("GigaChat (Sber)", sb_res)

    console.print(table)
    console.print("\n[dim]✅ Диагностика завершена.[/dim]")

if __name__ == "__main__":
    run_checks()