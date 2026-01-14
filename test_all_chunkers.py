# test_all_chunkers.py
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from app.services.python_chunker import SmartPythonChunker
from app.services.go_chunker import SmartGoChunker
from app.services.sql_chunker import SmartSQLChunker
from app.utils.token_counter import TokenCounter

console = Console()


def test_python():
    """Тест Python чанкера на примере."""
    console.print("\n[bold cyan]═══ Тест Python Chunker ═══[/bold cyan]\n")
    
    sample = '''
import os
from pathlib import Path

MAX_SIZE = 1000

class User:
    """Модель пользователя."""
    
    def __init__(self, name: str):
        self.name = name
    
    def greet(self) -> str:
        return f"Hello, {self.name}"

def main():
    user = User("Alice")
    print(user.greet())
'''
    
    # Сохраняем временный файл
    test_file = Path("_test_sample.py")
    test_file.write_text(sample, encoding="utf-8")
    
    chunker = SmartPythonChunker()
    chunks = chunker.chunk_file(str(test_file))
    
    table = Table(title="🐍 Python Chunks")
    table.add_column("Тип", style="yellow")
    table.add_column("Имя", style="white")
    table.add_column("Родитель", style="dim")
    table.add_column("Строки", justify="center")
    table.add_column("Токены", justify="right", style="green")
    
    for ch in chunks:
        if ch.kind != "file":
            table.add_row(ch.kind, ch.name, ch.parent or "-", f"{ch.start_line}-{ch.end_line}", str(ch.tokens))
    
    console.print(table)
    test_file.unlink()


def test_go():
    """Тест Go чанкера на примере."""
    console.print("\n[bold cyan]═══ Тест Go Chunker ═══[/bold cyan]\n")
    
    sample = '''
package main

import (
    "fmt"
    "strings"
)

const MaxRetries = 3

type User struct {
    Name  string
    Email string
}

type Logger interface {
    Log(message string)
}

func NewUser(name, email string) *User {
    return &User{Name: name, Email: email}
}

func (u *User) Greet() string {
    return fmt.Sprintf("Hello, %s!", u.Name)
}

func (u *User) ValidateEmail() bool {
    return strings.Contains(u.Email, "@")
}

func main() {
    user := NewUser("Alice", "alice@example.com")
    fmt.Println(user.Greet())
}
'''
    
    test_file = Path("_test_sample.go")
    test_file.write_text(sample, encoding="utf-8")
    
    chunker = SmartGoChunker()
    chunks = chunker.chunk_file(str(test_file))
    
    table = Table(title="🔵 Go Chunks")
    table.add_column("Тип", style="yellow")
    table.add_column("Имя", style="white")
    table.add_column("Receiver", style="dim")
    table.add_column("Строки", justify="center")
    table.add_column("Токены", justify="right", style="green")
    
    for ch in chunks:
        if ch.kind != "file":
            table.add_row(ch.kind, ch.name, ch.receiver or "-", f"{ch.start_line}-{ch.end_line}", str(ch.tokens))
    
    console.print(table)
    test_file.unlink()


def test_sql():
    """Тест SQL чанкера на примере."""
    console.print("\n[bold cyan]═══ Тест SQL Chunker ═══[/bold cyan]\n")
    
    sample = '''
-- Таблица пользователей
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс по email
CREATE INDEX idx_users_email ON users(email);

-- Вставка тестовых данных
INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com');
INSERT INTO users (name, email) VALUES ('Bob', 'bob@example.com');

-- Выборка активных пользователей
SELECT * FROM users WHERE created_at > '2024-01-01';

-- Обновление email
UPDATE users SET email = 'newemail@example.com' WHERE id = 1;

-- Хранимая процедура
CREATE PROCEDURE GetUserById(IN userId INT)
BEGIN
    SELECT * FROM users WHERE id = userId;
END;
'''
    
    test_file = Path("_test_sample.sql")
    test_file.write_text(sample, encoding="utf-8")
    
    chunker = SmartSQLChunker()
    chunks = chunker.chunk_file(str(test_file))
    
    table = Table(title="🗃️ SQL Chunks")
    table.add_column("Тип", style="yellow")
    table.add_column("Объект", style="white")
    table.add_column("Строки", justify="center")
    table.add_column("Токены", justify="right", style="green")
    
    for ch in chunks:
        if ch.kind != "file":
            table.add_row(ch.kind, ch.name, f"{ch.start_line}-{ch.end_line}", str(ch.tokens))
    
    console.print(table)
    
    # Показываем группировку по таблицам
    from app.services.sql_chunker import group_sql_by_table
    groups = group_sql_by_table(chunks)
    
    console.print("\n[bold]📊 Группировка по таблицам:[/bold]")
    for table_name, table_chunks in groups.items():
        ops = [f"{c.kind}" for c in table_chunks]
        console.print(f"  • {table_name}: {', '.join(ops)}")
    
    test_file.unlink()


if __name__ == "__main__":
    console.print(Panel.fit(
        "[bold green]🧪 Тест всех чанкеров: Python, Go, SQL[/bold green]",
        border_style="green"
    ))
    
    test_python()
    test_go()
    test_sql()
    
    console.print("\n[bold green]✅ Все тесты завершены![/bold green]\n")
