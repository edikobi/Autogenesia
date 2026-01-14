
"""
Утилиты для работы с данными.
"""

def format_name(first: str, last: str) -> str:
    """Форматирование полного имени"""
    return f"{first} {last}".title()


def validate_email(email: str) -> bool:
    """Валидация email адреса"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


class Singleton:
    """Реализация паттерна Singleton"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self.data = {}
    
    def set(self, key, value):
        self.data[key] = value
    
    def get(self, key):
        return self.data.get(key)
