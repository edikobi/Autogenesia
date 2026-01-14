# scripts/test_generator_models.py
"""
🧪 COMPREHENSIVE GENERATOR MODEL TEST

Tests REAL code generation functions with different models:
- generate_code() for ASK mode
- generate_code_agent_mode() for AGENT mode

Models tested:
- DeepSeek Chat (deepseek-chat)
- GLM 4.7 (z-ai/glm-4.7)  
- Claude Haiku 4.5 (anthropic/claude-haiku-4.5)
- Gemini 3.0 Flash (google/gemini-3-flash-preview)
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax
from rich import box

# ============================================================================
# PROJECT IMPORTS - THE REAL FUNCTIONS WE'RE TESTING
# ============================================================================

from config.settings import cfg
from app.agents.code_generator import (
    generate_code,              # ASK mode
    generate_code_agent_mode,   # AGENT mode
    CodeGeneratorResult,
    CodeBlock,
)
from app.services.file_modifier import ParsedCodeBlock
from app.utils.token_counter import TokenCounter

# ============================================================================
# CONFIGURATION
# ============================================================================

console = Console()

# Models to test (model_id, display_name, provider)
GENERATOR_MODELS = [
    ("deepseek-chat", "DeepSeek Chat", "DeepSeek Direct"),
    ("z-ai/glm-4.7", "GLM 4.7", "OpenRouter"),
    ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5", "OpenRouter"),
    ("google/gemini-3-flash-preview", "Gemini 3.0 Flash", "OpenRouter"),
]

# Enable AI Code Reviewer (uses additional tokens)
ENABLE_CODE_REVIEWER = False  # Set True to enable
CODE_REVIEWER_MODEL = "deepseek-chat"

# Log directory
LOG_DIR = PROJECT_ROOT / "logs" / "generator_tests"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TEST_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

# ============================================================================
# LOGGING
# ============================================================================

log_file = LOG_DIR / f"generator_test_{TEST_TIMESTAMP}.log"
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
))

logger = logging.getLogger("generator_test")
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)

# Console handler for errors
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
logger.addHandler(console_handler)


# =========================================================================
# ВАЖНО: Добавляем логгер api_client чтобы видеть finish_reason
# =========================================================================
api_client_logger = logging.getLogger("app.llm.api_client")
api_client_logger.setLevel(logging.DEBUG)
api_client_logger.addHandler(file_handler)
api_client_logger.addHandler(console_handler)

# Также добавляем логгер code_generator
code_gen_logger = logging.getLogger("app.agents.code_generator")
code_gen_logger.setLevel(logging.DEBUG)
code_gen_logger.addHandler(file_handler)


def log_section(title: str):
    """Log section separator"""
    separator = "=" * 70
    logger.info(f"\n{separator}\n{title}\n{separator}")
    console.print(f"\n[bold cyan]{'='*60}[/]")
    console.print(f"[bold cyan]{title}[/]")
    console.print(f"[bold cyan]{'='*60}[/]\n")


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class TestCase:
    """Test case configuration"""
    name: str
    mode: str  # "ask" or "agent"
    instruction: str
    existing_code: Optional[str] = None
    file_contents: Dict[str, str] = field(default_factory=dict)  # For agent mode
    target_file: Optional[str] = None
    expected_elements: List[str] = field(default_factory=list)


@dataclass 
class TestResult:
    """Result of a single test"""
    model_id: str
    model_name: str
    test_case: str
    mode: str
    success: bool
    duration_ms: float
    code_blocks_count: int = 0
    total_code_lines: int = 0
    error: Optional[str] = None
    raw_output_length: int = 0
    missing_elements: List[str] = field(default_factory=list)


# ============================================================================
# TEST CASES
# ============================================================================

def get_test_cases() -> List[TestCase]:
    """Define test cases"""
    
    # =========================================================================
    # TEST 1: ASK Mode - Simple method modification
    # =========================================================================
    test_ask_simple = TestCase(
        name="ASK_Add_Validation",
        mode="ask",
        instruction="""
## Instruction for Code Generator

**Task:** Add input validation to the calculate_discount method

### FILE: `app/services/pricing.py`

#### MODIFY_METHOD: `PricingService.calculate_discount`

**Location:** Lines 15-25

**Modification type:** ADD logic at BEGINNING

**Logic to add:**
1. Check if price < 0, raise ValueError("Price cannot be negative")
2. Check if discount_percent < 0 or > 100, raise ValueError("Invalid discount")
3. Keep existing calculation unchanged

**Preserve:** Keep the existing discount calculation at the end
""",
        existing_code='''"""Pricing service."""

class PricingService:
    """Handles pricing calculations."""
    
    def __init__(self, tax_rate: float = 0.2):
        self.tax_rate = tax_rate
    
    def calculate_discount(self, price: float, discount_percent: float) -> float:
        """Calculate discounted price."""
        discount_amount = price * (discount_percent / 100)
        return price - discount_amount
    
    def calculate_tax(self, price: float) -> float:
        """Calculate tax amount."""
        return price * self.tax_rate
''',
        target_file="app/services/pricing.py",
        expected_elements=["ValueError", "price", "discount_percent", "raise"]
    )
    
    # =========================================================================
    # TEST 2: ASK Mode - Add new method  
    # =========================================================================
    test_ask_add_method = TestCase(
        name="ASK_Add_Method",
        mode="ask",
        instruction="""
## Instruction for Code Generator

**Task:** Add a bulk discount calculation method

### FILE: `app/services/pricing.py`

#### ADD_METHOD: `PricingService.calculate_bulk_discount`

**Insert after:** `calculate_discount` method

**Signature:** `def calculate_bulk_discount(self, items: List[Tuple[float, int]]) -> float`

**Docstring:** "Calculate total with quantity-based discounts. qty>=10: 15% off, qty>=5: 10% off"

**Logic:**
1. Initialize total = 0.0
2. For each (price, quantity) in items:
   - If quantity >= 10: discount = 0.15
   - Elif quantity >= 5: discount = 0.10  
   - Else: discount = 0
3. item_total = price * quantity * (1 - discount)
4. Add to total
5. Return total
""",
        existing_code='''"""Pricing service."""
from typing import List, Tuple

class PricingService:
    def __init__(self, tax_rate: float = 0.2):
        self.tax_rate = tax_rate
    
    def calculate_discount(self, price: float, discount_percent: float) -> float:
        """Calculate discounted price."""
        if price < 0:
            raise ValueError("Price cannot be negative")
        return price * (1 - discount_percent / 100)
''',
        target_file="app/services/pricing.py",
        expected_elements=["calculate_bulk_discount", "List", "Tuple", "quantity", "0.15", "0.10"]
    )
    
    # =========================================================================
    # TEST 3: AGENT Mode - Create new file
    # =========================================================================
    test_agent_new_file = TestCase(
        name="AGENT_New_File",
        mode="agent",
        instruction="""
## Instruction for Code Generator

**Task:** Create a configuration manager module

### FILE: `CREATE: app/core/config_manager.py`

**Operation:** CREATE new file

**Required components:**

1. **Imports:**
   - os, json from stdlib
   - Path from pathlib
   - Dict, Any, Optional from typing
   - dataclass from dataclasses

2. **Constant:**
   - DEFAULT_CONFIG_PATH = Path(".config.json")

3. **Dataclass ConfigValue:**
   - key: str
   - value: Any  
   - source: str = "default"

4. **Class ConfigManager:**
   - __init__(self, config_path: Optional[Path] = None)
   - load(self) -> None
   - get(self, key: str, default: Any = None) -> Any
   - _load_from_env(self) -> None (private, reads APP_* env vars)
   - _load_from_file(self) -> None (private, reads JSON)

5. **Function get_config() -> ConfigManager:**
   - Singleton pattern with global _config_instance
""",
        file_contents={},  # New file, no existing content
        target_file="app/core/config_manager.py",
        expected_elements=["ConfigManager", "ConfigValue", "@dataclass", "get_config", "_load_from_env", "os.environ"]
    )
    
    # =========================================================================
    # TEST 4: AGENT Mode - Modify existing file
    # =========================================================================
    test_agent_modify = TestCase(
        name="AGENT_Modify_Existing",
        mode="agent",
        instruction="""
## Instruction for Code Generator

**Task:** Add retry logic to API client

### FILE: `app/services/api_client.py`

**Changes:**

#### ADD imports at top:

import time
from functools import lru_cache


#### ADD_METHOD: `APIClient._make_request_with_retry`

**Insert after:** `_make_request` method

**Signature:** `def _make_request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs) -> Dict`

**Logic:**
1. For attempt in range(max_retries):
   - Try self._make_request(method, url, **kwargs)
   - On success, return response
   - On RequestException: delay = 2 ** attempt, sleep, continue
2. Raise last exception or RuntimeError

#### MODIFY_METHOD: `APIClient.get`

**Replace body** to use `_make_request_with_retry` instead of `_make_request`
""",
        file_contents={
            "app/services/api_client.py": '''"""API Client."""
import requests
from typing import Dict, Any

class APIClient:
    def __init__(self, base_url: str, api_key: str = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"
    
    def _make_request(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        """Make HTTP request."""
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    
    def get(self, endpoint: str) -> Dict[str, Any]:
        """GET request."""
        url = f"{self.base_url}/{endpoint}"
        return self._make_request("GET", url)
'''
        },
        target_file="app/services/api_client.py",
        expected_elements=["_make_request_with_retry", "max_retries", "time.sleep", "2 **", "lru_cache"]
    )
    
    # =========================================================================
    # TEST 5: AGENT Mode - Multiple files with cross-imports
    # Проверка: Генерация нескольких файлов с корректными взаимными импортами
    # =========================================================================
    test_agent_multi_file_imports = TestCase(
        name="AGENT_Multi_File_Imports",
        mode="agent",
        instruction="""
## Instruction for Code Generator

**Task:** Create a user authentication system with multiple modules

### FILES TO CREATE:

#### FILE 1: `CREATE: app/auth/models.py`
Create User model and related data classes:

from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class User:
    id: int
    username: str
    email: str
    password_hash: str
    created_at: datetime
    is_active: bool = True

@dataclass  
class AuthToken:
    token: str
    user_id: int
    expires_at: datetime

#### FILE 2: `CREATE: app/auth/repository.py`
Create UserRepository that uses models:

from typing import Optional, List, Dict
from .models import User, AuthToken  # <-- MUST import from models

class UserRepository:
    def __init__(self):
        self._users: Dict[int, User] = {}
        self._tokens: Dict[str, AuthToken] = {}
    
    def get_by_id(self, user_id: int) -> Optional[User]:
        return self._users.get(user_id)
    
    def get_by_username(self, username: str) -> Optional[User]:
        for user in self._users.values():
            if user.username == username:
                return user
        return None
    
    def save(self, user: User) -> User:
        self._users[user.id] = user
        return user
    
    def save_token(self, token: AuthToken) -> None:
        self._tokens[token.token] = token

#### FILE 3: `CREATE: app/auth/service.py`
Create AuthService that uses both models and repository:

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from .models import User, AuthToken  # <-- import models
from .repository import UserRepository  # <-- import repository

class AuthService:
    def __init__(self, repository: UserRepository):
        self.repository = repository
    
    def register(self, username: str, email: str, password: str) -> User:
        password_hash = self._hash_password(password)
        user = User(
            id=self._generate_id(),
            username=username,
            email=email,
            password_hash=password_hash,
            created_at=datetime.now()
        )
        return self.repository.save(user)
    
    def login(self, username: str, password: str) -> Optional[AuthToken]:
        user = self.repository.get_by_username(username)
        if not user or not self._verify_password(password, user.password_hash):
            return None
        token = AuthToken(
            token=secrets.token_urlsafe(32),
            user_id=user.id,
            expires_at=datetime.now() + timedelta(hours=24)
        )
        self.repository.save_token(token)
        return token
    
    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _verify_password(self, password: str, hash: str) -> bool:
        return self._hash_password(password) == hash
    
    def _generate_id(self) -> int:
        return int(datetime.now().timestamp() * 1000)

**IMPORTANT:** All imports between modules MUST be relative imports (from .module import ...)
""",
        file_contents={},  # All new files
        target_file="app/auth/service.py",
        expected_elements=[
            # Cross-imports check
            "from .models import",
            "from .repository import",
            # Models
            "User",
            "AuthToken",
            "@dataclass",
            # Repository
            "UserRepository",
            "get_by_id",
            "get_by_username",
            # Service
            "AuthService",
            "register",
            "login",
            "secrets.token_urlsafe",
        ]
    )
    
    # =========================================================================
    # TEST 6: AGENT Mode - Complex multi-entity modification
    # Проверка: Комплексное изменение кодовой базы, затрагивающее несколько сущностей
    # =========================================================================
    test_agent_complex_modification = TestCase(
        name="AGENT_Complex_Multi_Entity",
        mode="agent",
        instruction="""
## Instruction for Code Generator

**Task:** Add audit logging to order processing system

This requires modifying multiple existing files to add consistent audit logging.

### FILE 1: `app/models/order.py`

**MODIFY:** Add `audit_log` field and `add_audit_entry` method to Order class

Current Order class needs:
- New field: `audit_log: List[AuditEntry] = field(default_factory=list)`
- New method: `add_audit_entry(self, action: str, user_id: int, details: str = "") -> None`
- New dataclass AuditEntry with fields: timestamp, action, user_id, details

### FILE 2: `app/services/order_service.py`

**MODIFY:** Add audit logging calls to all state-changing methods

- In `create_order`: call `order.add_audit_entry("CREATED", user_id)`
- In `update_order`: call `order.add_audit_entry("UPDATED", user_id, f"Changed: {changes}")`
- In `cancel_order`: call `order.add_audit_entry("CANCELLED", user_id, reason)`
- In `complete_order`: call `order.add_audit_entry("COMPLETED", user_id)`

### FILE 3: `app/api/order_routes.py`

**MODIFY:** Add endpoint to retrieve audit log

- Add new route: `GET /orders/{order_id}/audit`
- Returns list of audit entries for the order
- Requires authentication (get user_id from request)
""",
        file_contents={
            "app/models/order.py": '''"""Order models."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from enum import Enum

class OrderStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

@dataclass
class OrderItem:
    product_id: int
    quantity: int
    price: float

@dataclass
class Order:
    id: int
    user_id: int
    items: List[OrderItem]
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    total: float = 0.0
    
    def calculate_total(self) -> float:
        self.total = sum(item.price * item.quantity for item in self.items)
        return self.total
''',
            "app/services/order_service.py": '''"""Order service."""
from typing import Optional, List
from app.models.order import Order, OrderItem, OrderStatus

class OrderService:
    def __init__(self, repository):
        self.repository = repository
    
    def create_order(self, user_id: int, items: List[OrderItem]) -> Order:
        order = Order(
            id=self._generate_id(),
            user_id=user_id,
            items=items
        )
        order.calculate_total()
        return self.repository.save(order)
    
    def update_order(self, order_id: int, user_id: int, **changes) -> Optional[Order]:
        order = self.repository.get(order_id)
        if not order:
            return None
        for key, value in changes.items():
            if hasattr(order, key):
                setattr(order, key, value)
        return self.repository.save(order)
    
    def cancel_order(self, order_id: int, user_id: int, reason: str = "") -> bool:
        order = self.repository.get(order_id)
        if not order or order.status == OrderStatus.COMPLETED:
            return False
        order.status = OrderStatus.CANCELLED
        self.repository.save(order)
        return True
    
    def complete_order(self, order_id: int, user_id: int) -> bool:
        order = self.repository.get(order_id)
        if not order or order.status != OrderStatus.PROCESSING:
            return False
        order.status = OrderStatus.COMPLETED
        self.repository.save(order)
        return True
    
    def _generate_id(self) -> int:
        import time
        return int(time.time() * 1000)
''',
            "app/api/order_routes.py": '''"""Order API routes."""
from flask import Blueprint, request, jsonify
from app.services.order_service import OrderService

order_bp = Blueprint("orders", __name__, url_prefix="/orders")

# Injected service
_order_service: OrderService = None

def init_routes(order_service: OrderService):
    global _order_service
    _order_service = order_service

@order_bp.route("/", methods=["POST"])
def create_order():
    data = request.json
    user_id = request.headers.get("X-User-ID", type=int)
    order = _order_service.create_order(user_id, data["items"])
    return jsonify({"id": order.id, "total": order.total}), 201

@order_bp.route("/<int:order_id>", methods=["GET"])
def get_order(order_id: int):
    order = _order_service.repository.get(order_id)
    if not order:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"id": order.id, "status": order.status.value})

@order_bp.route("/<int:order_id>/cancel", methods=["POST"])
def cancel_order(order_id: int):
    user_id = request.headers.get("X-User-ID", type=int)
    reason = request.json.get("reason", "")
    success = _order_service.cancel_order(order_id, user_id, reason)
    return jsonify({"success": success})
'''
        },
        target_file="app/services/order_service.py",
        expected_elements=[
            # AuditEntry dataclass
            "AuditEntry",
            "timestamp",
            "action",
            # Order modifications
            "audit_log",
            "add_audit_entry",
            # Service audit calls
            "CREATED",
            "UPDATED",
            "CANCELLED",
            "COMPLETED",
            # New route
            "/audit",
            "audit",
        ]
    )
    
    # =========================================================================
    # TEST 7: AGENT Mode - Refactoring with new module extraction
    # Проверка: Рефакторинг с выделением логики в новый модуль
    # =========================================================================
    test_agent_refactor_extract = TestCase(
        name="AGENT_Refactor_Extract_Module",
        mode="agent",
        instruction="""
## Instruction for Code Generator

**Task:** Extract validation logic from UserService into a separate ValidationService module

### CURRENT STATE:
The UserService has validation logic mixed with business logic. We need to:
1. Create a new `app/services/validation_service.py` module
2. Move all validation methods there
3. Update UserService to use the new ValidationService

### FILE 1: `CREATE: app/services/validation_service.py`

Extract and create ValidationService with these methods:
- `validate_email(email: str) -> Tuple[bool, Optional[str]]` - validates email format
- `validate_password(password: str) -> Tuple[bool, Optional[str]]` - validates password strength
- `validate_username(username: str) -> Tuple[bool, Optional[str]]` - validates username format
- `validate_user_data(data: Dict) -> Tuple[bool, List[str]]` - validates all user fields

Each method returns (is_valid, error_message) or (is_valid, list_of_errors)

**Validation rules to implement:**
- Email: must contain @, valid domain, max 255 chars
- Password: min 8 chars, at least 1 uppercase, 1 lowercase, 1 digit
- Username: 3-30 chars, alphanumeric + underscore only, must start with letter

### FILE 2: `app/services/user_service.py`

**MODIFY:** Remove validation methods and use ValidationService instead

Changes needed:
1. Add import: `from app.services.validation_service import ValidationService`
2. Add `validation_service: ValidationService` parameter to `__init__`
3. Remove methods: `_validate_email`, `_validate_password`, `_validate_username`
4. Update `create_user` to use `self.validation_service.validate_user_data()`
5. Update `update_user` to use validation service for changed fields

**IMPORTANT:** 
- Keep all other UserService methods unchanged
- ValidationService should be stateless (no __init__ state needed)
- Add proper type hints everywhere
""",
        file_contents={
            "app/services/user_service.py": '''"""User service with embedded validation."""
import re
from typing import Optional, Dict, List
from dataclasses import dataclass

@dataclass
class User:
    id: int
    username: str
    email: str
    password_hash: str

class UserService:
    def __init__(self, repository):
        self.repository = repository
    
    def create_user(self, username: str, email: str, password: str) -> User:
        # Validate all fields
        if not self._validate_username(username):
            raise ValueError("Invalid username")
        if not self._validate_email(email):
            raise ValueError("Invalid email")
        if not self._validate_password(password):
            raise ValueError("Invalid password")
        
        # Check uniqueness
        if self.repository.get_by_email(email):
            raise ValueError("Email already exists")
        if self.repository.get_by_username(username):
            raise ValueError("Username already exists")
        
        user = User(
            id=self._generate_id(),
            username=username,
            email=email,
            password_hash=self._hash_password(password)
        )
        return self.repository.save(user)
    
    def update_user(self, user_id: int, **changes) -> Optional[User]:
        user = self.repository.get(user_id)
        if not user:
            return None
        
        if "email" in changes and not self._validate_email(changes["email"]):
            raise ValueError("Invalid email")
        if "username" in changes and not self._validate_username(changes["username"]):
            raise ValueError("Invalid username")
        
        for key, value in changes.items():
            if hasattr(user, key):
                setattr(user, key, value)
        
        return self.repository.save(user)
    
    def get_user(self, user_id: int) -> Optional[User]:
        return self.repository.get(user_id)
    
    def delete_user(self, user_id: int) -> bool:
        return self.repository.delete(user_id)
    
    # === VALIDATION METHODS (to be extracted) ===
    
    def _validate_email(self, email: str) -> bool:
        if not email or len(email) > 255:
            return False
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$"
        return bool(re.match(pattern, email))
    
    def _validate_password(self, password: str) -> bool:
        if len(password) < 8:
            return False
        if not re.search(r"[A-Z]", password):
            return False
        if not re.search(r"[a-z]", password):
            return False
        if not re.search(r"[0-9]", password):
            return False
        return True
    
    def _validate_username(self, username: str) -> bool:
        if not username or len(username) < 3 or len(username) > 30:
            return False
        if not username[0].isalpha():
            return False
        pattern = r"^[a-zA-Z][a-zA-Z0-9_]*$"
        return bool(re.match(pattern, username))
    
    # === HELPER METHODS ===
    
    def _hash_password(self, password: str) -> str:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _generate_id(self) -> int:
        import time
        return int(time.time() * 1000)
'''
        },
        target_file="app/services/validation_service.py",
        expected_elements=[
            # New ValidationService
            "ValidationService",
            "validate_email",
            "validate_password", 
            "validate_username",
            "validate_user_data",
            "Tuple[bool",
            # Validation rules implemented
            "len(password) < 8",
            "[A-Z]",
            "[a-z]",
            "[0-9]",
            "isalpha",
            # Import in UserService
            "from app.services.validation_service import",
            "validation_service",
        ]
    )
    
    return [
        test_ask_simple, 
        test_ask_add_method, 
        test_agent_new_file, 
        test_agent_modify,
        test_agent_multi_file_imports,
        test_agent_complex_modification,
        test_agent_refactor_extract,
    ]

# ============================================================================
# TEST RUNNER
# ============================================================================

async def run_ask_mode_test(
    model_id: str,
    model_name: str,
    test_case: TestCase,
) -> TestResult:
    """
    Run ASK mode test using generate_code() function.
    
    This is the REAL function from code_generator.py
    """
    logger.info(f"[ASK] Testing {model_name} on {test_case.name}")
    console.print(f"\n[cyan]📝 ASK Mode:[/] {model_name} → {test_case.name}")
    
    start_time = time.time()
    
    try:
        # =====================================================
        # REAL CALL to generate_code()
        # =====================================================
        result: CodeGeneratorResult = await generate_code(
            instruction=test_case.instruction,
            file_code=test_case.existing_code,
            target_file=test_case.target_file,
            model=model_id,  # <-- THIS IS WHAT WE'RE TESTING
        )
        
        duration_ms = (time.time() - start_time) * 1000
        
        # Log result
        logger.info(f"Result: success={result.success}, blocks={len(result.code_blocks)}, model_used={result.model_used}")
        
        if result.error:
            logger.warning(f"Error from generator: {result.error}")
        
        # Check expected elements
        all_code = "\n".join(b.code for b in result.code_blocks)
        missing = [e for e in test_case.expected_elements if e.lower() not in all_code.lower()]
        
        # Calculate stats
        total_lines = sum(len(b.code.splitlines()) for b in result.code_blocks)
        
        # Display result
        success = result.success and len(missing) == 0 and len(result.code_blocks) > 0
        
        status = "[green]✅ PASS[/]" if success else "[red]❌ FAIL[/]"
        console.print(f"   {status} | {duration_ms:.0f}ms | {len(result.code_blocks)} blocks | {total_lines} lines")
        
        if missing:
            console.print(f"   [yellow]Missing:[/] {', '.join(missing)}")
        
        # Show code preview
        if result.code_blocks:
            preview = result.code_blocks[0].code[:400]
            console.print(Panel(
                Syntax(preview + ("..." if len(result.code_blocks[0].code) > 400 else ""), 
                       "python", theme="monokai", line_numbers=True),
                title=f"Code Preview ({result.model_used})",
                border_style="green" if success else "red",
                width=90
            ))
        
        # Log raw response
        logger.debug(f"Raw response ({len(result.raw_response)} chars):\n{result.raw_response[:2000]}")
        
        return TestResult(
            model_id=model_id,
            model_name=model_name,
            test_case=test_case.name,
            mode="ask",
            success=success,
            duration_ms=duration_ms,
            code_blocks_count=len(result.code_blocks),
            total_code_lines=total_lines,
            raw_output_length=len(result.raw_response),
            missing_elements=missing,
            error=result.error,
        )
        
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(f"Test failed: {error_msg}\n{traceback.format_exc()}")
        console.print(f"   [red]❌ ERROR:[/] {error_msg}")
        
        return TestResult(
            model_id=model_id,
            model_name=model_name,
            test_case=test_case.name,
            mode="ask",
            success=False,
            duration_ms=duration_ms,
            error=error_msg,
        )


async def run_agent_mode_test(
    model_id: str,
    model_name: str,
    test_case: TestCase,
) -> TestResult:
    """
    Run AGENT mode test using generate_code_agent_mode() function.
    
    This is the REAL function from code_generator.py
    """
    logger.info(f"[AGENT] Testing {model_name} on {test_case.name}")
    console.print(f"\n[cyan]🤖 AGENT Mode:[/] {model_name} → {test_case.name}")
    
    start_time = time.time()
    
    try:
        # Prepare file_contents dict
        file_contents = test_case.file_contents.copy() if test_case.file_contents else {}
        
        # =====================================================
        # DIAGNOSTIC: Log what we're sending
        # =====================================================
        instruction_length = len(test_case.instruction)
        files_total_length = sum(len(v) for v in file_contents.values())
        
        logger.info(f"[AGENT] Input stats: instruction={instruction_length} chars, "
                   f"files={len(file_contents)}, files_total={files_total_length} chars")
        console.print(f"   [dim]Input: {instruction_length} chars instruction, "
                     f"{len(file_contents)} files ({files_total_length} chars)[/]")
        
        # =====================================================
        # REAL CALL to generate_code_agent_mode()
        # Log the max_tokens we're using
        # =====================================================
        test_max_tokens = 16000  # Явно задаём для теста
        
        logger.info(f"[AGENT] Calling generate_code_agent_mode with max_tokens={test_max_tokens}")
        console.print(f"   [dim]Requesting max_tokens={test_max_tokens}[/]")
        
        blocks, raw_response = await generate_code_agent_mode(
            instruction=test_case.instruction,
            file_contents=file_contents,
            model=model_id,
            max_tokens=test_max_tokens,  # <-- ЯВНО ПЕРЕДАЁМ
        )
        
        duration_ms = (time.time() - start_time) * 1000
        
        # =====================================================
        # DIAGNOSTIC: Log response stats
        # =====================================================
        response_chars = len(raw_response)
        response_lines = len(raw_response.splitlines())
        
        logger.info(f"[AGENT] Response stats: {response_chars} chars, {response_lines} lines, "
                   f"{len(blocks)} blocks parsed")
        console.print(f"   [dim]Response: {response_chars} chars, {response_lines} lines[/]")
        
        # Check for truncation indicators
        truncation_indicators = [
            raw_response.rstrip().endswith(','),
            raw_response.rstrip().endswith('('),
            raw_response.rstrip().endswith('['),
            raw_response.rstrip().endswith('{'),
            raw_response.rstrip().endswith(':'),
            '```' in raw_response and raw_response.count('```') % 2 != 0,  # Unclosed fence
        ]
        
        if any(truncation_indicators):
            logger.warning(f"[AGENT] ⚠️ TRUNCATION DETECTED! Response ends with: "
                          f"'{raw_response[-50:]}' (last 50 chars)")
            console.print(f"   [red]⚠️ TRUNCATION DETECTED![/] Response may be incomplete")
            console.print(f"   [red]Last 100 chars: ...{repr(raw_response[-100:])}[/]")
        
        # Log result
        logger.info(f"Result: blocks={len(blocks)}, raw_response={len(raw_response)} chars")
        
        # Check expected elements
        all_code = "\n".join(b.code for b in blocks)
        missing = [e for e in test_case.expected_elements if e.lower() not in all_code.lower()]
        
        # Calculate stats
        total_lines = sum(len(b.code.splitlines()) for b in blocks)
        
        # Display result
        success = len(blocks) > 0 and len(missing) == 0
        
        status = "[green]✅ PASS[/]" if success else "[red]❌ FAIL[/]"
        console.print(f"   {status} | {duration_ms:.0f}ms | {len(blocks)} blocks | {total_lines} lines")
        
        if missing:
            console.print(f"   [yellow]Missing:[/] {', '.join(missing[:5])}"
                         f"{'...' if len(missing) > 5 else ''}")
            logger.warning(f"[AGENT] Missing elements: {missing}")
        
        # Show blocks info
        for i, block in enumerate(blocks[:3], 1):
            console.print(f"   Block {i}: [cyan]{block.file_path}[/] [{block.mode}] {len(block.code)} chars")
        
        if len(blocks) > 3:
            console.print(f"   [dim]... and {len(blocks) - 3} more blocks[/]")
        
        # Show code preview
        if blocks:
            preview = blocks[0].code[:400]
            console.print(Panel(
                Syntax(preview + ("..." if len(blocks[0].code) > 400 else ""),
                       "python", theme="monokai", line_numbers=True),
                title=f"Code Preview ({blocks[0].file_path})",
                border_style="green" if success else "red",
                width=90
            ))
        
        # Log raw response (full for debugging)
        logger.debug(f"Raw response:\n{raw_response}")
        
        return TestResult(
            model_id=model_id,
            model_name=model_name,
            test_case=test_case.name,
            mode="agent",
            success=success,
            duration_ms=duration_ms,
            code_blocks_count=len(blocks),
            total_code_lines=total_lines,
            raw_output_length=len(raw_response),
            missing_elements=missing,
        )
        
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(f"Test failed: {error_msg}\n{traceback.format_exc()}")
        console.print(f"   [red]❌ ERROR:[/] {error_msg}")
        
        return TestResult(
            model_id=model_id,
            model_name=model_name,
            test_case=test_case.name,
            mode="agent",
            success=False,
            duration_ms=duration_ms,
            error=error_msg,
        )


async def run_all_tests():
    """Run all tests on all models"""
    
    console.print(Panel(
        "[bold]🧪 GENERATOR MODEL COMPARISON TEST[/]\n\n"
        f"[cyan]Models:[/] {', '.join(m[1] for m in GENERATOR_MODELS)}\n"
        f"[cyan]Log file:[/] {log_file}\n"
        f"[cyan]Testing:[/] generate_code() and generate_code_agent_mode()",
        title="Test Configuration",
        border_style="cyan"
    ))
    
    logger.info(f"Starting generator model comparison")
    logger.info(f"Models: {[m[0] for m in GENERATOR_MODELS]}")
    
    test_cases = get_test_cases()
    all_results: List[TestResult] = []
    
    # Summary per model
    model_stats: Dict[str, Dict] = {
        m[0]: {"name": m[1], "passed": 0, "failed": 0, "total_ms": 0, "total_lines": 0}
        for m in GENERATOR_MODELS
    }
    
    # Run tests
    for test_case in test_cases:
        log_section(f"TEST: {test_case.name} ({test_case.mode.upper()} mode)")
        
        for model_id, model_name, provider in GENERATOR_MODELS:
            # Select test function based on mode
            if test_case.mode == "ask":
                result = await run_ask_mode_test(model_id, model_name, test_case)
            else:  # agent
                result = await run_agent_mode_test(model_id, model_name, test_case)
            
            all_results.append(result)
            
            # Update stats
            stats = model_stats[model_id]
            stats["total_ms"] += result.duration_ms
            stats["total_lines"] += result.total_code_lines
            if result.success:
                stats["passed"] += 1
            else:
                stats["failed"] += 1
            
            # Delay between models (rate limit)
            await asyncio.sleep(2)
        
        # Delay between test cases
        await asyncio.sleep(3)
    
    # =========================================================================
    # SUMMARY
    # =========================================================================
    log_section("📊 FINAL RESULTS")
    
    table = Table(title="Model Comparison Summary", box=box.ROUNDED)
    table.add_column("Model", style="cyan", no_wrap=True)
    table.add_column("Provider", style="dim")
    table.add_column("Passed", style="green", justify="center")
    table.add_column("Failed", style="red", justify="center")
    table.add_column("Avg Time", justify="right")
    table.add_column("Total Lines", justify="right")
    
    for model_id, model_name, provider in GENERATOR_MODELS:
        stats = model_stats[model_id]
        total_tests = stats["passed"] + stats["failed"]
        avg_time = stats["total_ms"] / max(1, total_tests)
        
        table.add_row(
            model_name,
            provider,
            str(stats["passed"]),
            str(stats["failed"]),
            f"{avg_time:.0f}ms",
            str(stats["total_lines"]),
        )
    
    console.print(table)
    
    # Log summary
    logger.info("FINAL SUMMARY:")
    for model_id, stats in model_stats.items():
        total = stats["passed"] + stats["failed"]
        logger.info(f"  {stats['name']}: {stats['passed']}/{total} passed, avg {stats['total_ms']/max(1,total):.0f}ms")
    
    # Detailed results JSON
    results_file = LOG_DIR / f"generator_results_{TEST_TIMESTAMP}.json"
    results_data = {
        "timestamp": TEST_TIMESTAMP,
        "models_tested": [m[0] for m in GENERATOR_MODELS],
        "test_cases": [tc.name for tc in test_cases],
        "results": [
            {
                "model": r.model_name,
                "test_case": r.test_case,
                "mode": r.mode,
                "success": r.success,
                "duration_ms": r.duration_ms,
                "code_blocks": r.code_blocks_count,
                "lines": r.total_code_lines,
                "missing_elements": r.missing_elements,
                "error": r.error,
            }
            for r in all_results
        ],
        "summary": {
            mid: {
                "name": stats["name"],
                "passed": stats["passed"],
                "failed": stats["failed"],
                "avg_ms": stats["total_ms"] / max(1, stats["passed"] + stats["failed"]),
                "total_lines": stats["total_lines"],
            }
            for mid, stats in model_stats.items()
        }
    }
    
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=2, ensure_ascii=False)
    
    console.print(f"\n[dim]📋 Results saved: {results_file}[/]")
    console.print(f"[dim]📋 Full log: {log_file}[/]")
    
    # Best model recommendation
    best_model = max(model_stats.items(), key=lambda x: (x[1]["passed"], -x[1]["total_ms"]))
    console.print(Panel(
        f"[bold green]🏆 Best model: {best_model[1]['name']}[/]\n\n"
        f"Passed: {best_model[1]['passed']}/{best_model[1]['passed']+best_model[1]['failed']}\n"
        f"Avg time: {best_model[1]['total_ms']/(best_model[1]['passed']+best_model[1]['failed']):.0f}ms\n"
        f"Total code lines: {best_model[1]['total_lines']}",
        title="Recommendation",
        border_style="green"
    ))


# ============================================================================
# ENTRY POINT
# ============================================================================

async def main():
    console.print("\n[bold cyan]🚀 Starting Generator Model Test[/]\n")
    
    try:
        await run_all_tests()
    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted[/]")
    except Exception as e:
        console.print(f"\n[red]Fatal error: {e}[/]")
        logger.error(f"Fatal: {e}\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
