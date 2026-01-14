
"""
Простой модуль с классами и функциями для тестирования индексации.
"""

import os
import json
from typing import List, Dict, Optional

API_KEY = "test_key_123"
CONFIG = {"debug": True, "timeout": 30}


class User:
    """Класс пользователя с базовой информацией"""
    
    def __init__(self, username: str, email: str):
        self.username = username
        self.email = email
        self.is_active = True
    
    def login(self, password: str) -> bool:
        """Аутентификация пользователя"""
        # Здесь должна быть реальная логика аутентификации
        hashed_password = self._hash_password(password)
        return hashed_password == "hashed_test"
    
    def _hash_password(self, password: str) -> str:
        """Приватный метод для хеширования пароля"""
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()
    
    def update_email(self, new_email: str) -> None:
        """Обновление email пользователя"""
        if "@" in new_email:
            self.email = new_email
            self._save_to_db()
    
    def _save_to_db(self):
        """Сохранение в базу данных"""
        print(f"Saving user {self.username} to database")


class Admin(User):
    """Администратор, наследуется от User"""
    
    def __init__(self, username: str, email: str, permissions: List[str]):
        super().__init__(username, email)
        self.permissions = permissions
    
    def ban_user(self, user: User) -> bool:
        """Блокировка пользователя"""
        print(f"Banning user {user.username}")
        return True


def calculate_stats(numbers: List[int]) -> Dict[str, float]:
    """Вычисление статистики по списку чисел"""
    if not numbers:
        return {}
    
    total = sum(numbers)
    average = total / len(numbers)
    maximum = max(numbers)
    
    return {
        "total": total,
        "average": average,
        "max": maximum,
        "count": len(numbers)
    }


async def fetch_data(url: str) -> Optional[Dict]:
    """Асинхронная функция для получения данных"""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return await response.json()
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None
