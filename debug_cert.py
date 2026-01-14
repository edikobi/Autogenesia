import os
import requests

# Жестко пропишите путь, где лежит ваш файл (проверьте глазами в проводнике!)
# Обратите внимание на двойные слеши для Windows
CERT_PATH = r"C:\Users\Admin\AI_Assistant_Pro\certs\russian_trusted_root_ca.crt"

print(f"1. Checking path: {CERT_PATH}")
if os.path.exists(CERT_PATH):
    print("   [OK] File exists.")
    print(f"   Size: {os.path.getsize(CERT_PATH)} bytes")
else:
    print("   [FAIL] File NOT found!")
    exit(1)

print("\n2. Trying to read file content...")
try:
    with open(CERT_PATH, 'rb') as f:
        content = f.read()
    print(f"   [OK] Read {len(content)} bytes.")
    if b"BEGIN CERTIFICATE" in content:
        print("   [OK] Content looks like a certificate.")
    else:
        print("   [FAIL] File content does not look like PEM!")
except Exception as e:
    print(f"   [FAIL] Reading error: {e}")
    exit(1)

print("\n3. Trying requests with this cert...")
try:
    # Пытаемся постучаться в Сбер
    r = requests.get("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", verify=CERT_PATH)
    print(f"   [OK] Request sent. Status: {r.status_code}")
except requests.exceptions.SSLError as e:
    print(f"   [SSL ERROR] {e}")
    print("   Это значит, файл читается, но сервер его не принимает (или он не тот).")
except OSError as e:
    print(f"   [OS ERROR] {e}")
    print("   Вот она, ваша ошибка 'Could not find bundle'. Requests не может открыть файл.")
except Exception as e:
    print(f"   [OTHER ERROR] {e}")
