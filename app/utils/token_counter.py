# app/utils/token_counter.py
import tiktoken

class TokenCounter:
    """
    Универсальный счетчик токенов для текста.
    По умолчанию используем cl100k_base (подходит для DeepSeek / Qwen / GPT-4-семейства).
    """
    def __init__(self, encoding_name: str = "cl100k_base"):
        self.encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        """Подсчитать количество токенов в строке."""
        if not text:
            return 0
        return len(self.encoding.encode(text))

    def count_list(self, texts: list[str]) -> list[int]:
        """Подсчитать токены для списка строк."""
        return [self.count(t) for t in texts]
