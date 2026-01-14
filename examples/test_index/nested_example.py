
"""
Пример с вложенными классами.
"""

class Outer:
    """Внешний класс"""
    
    class Inner:
        """Внутренний класс первого уровня"""
        
        def inner_method(self):
            return "Inner method"
        
        class DeeplyNested:
            """Вложенный класс второго уровня"""
            
            def deep_method(self):
                return "Deep method"
    
    def outer_method(self):
        inner = self.Inner()
        return inner.inner_method()
    

class Database:
    """Класс для работы с базой данных"""
    
    class Connection:
        """Вложенный класс соединения"""
        
        def __init__(self, host: str, port: int):
            self.host = host
            self.port = port
            self._connected = False
        
        def connect(self):
            self._connected = True
            print(f"Connected to {self.host}:{self.port}")
        
        def disconnect(self):
            self._connected = False
    
    def __init__(self, host: str, port: int):
        self.connection = self.Connection(host, port)
    
    def query(self, sql: str):
        if not self.connection._connected:
            self.connection.connect()
        print(f"Executing: {sql}")
