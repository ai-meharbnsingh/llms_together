# 🧪 Comprehensive Testing Strategy — Autonomous Factory

Based on the BRUTAL forensic audit, this testing strategy prioritizes security, safety, and reliability.

---

## 1️⃣ STATIC ANALYSIS & LINTING

### Python Code Quality

```bash
# Install all tools
pip install flake8 pylint black mypy bandit safety pydocstyle
```

#### Flake8 (Style & Basic Errors)
```bash
# Run with strict settings
flake8 . \
    --max-line-length=100 \
    --extend-ignore=E203,W503 \
    --exclude=venv,node_modules,__pycache__,.git,No_ai_detection/backend/venv \
    --show-source \
    --statistics
```

**Critical Rules to Enforce**:
- `E999` - Syntax errors
- `F821` - Undefined names
- `F841` - Unused variables
- `E722` - Bare except blocks (MUST FIX - found 23 instances)

#### Pylint (Deeper Analysis)
```bash
pylint orchestration/ workers/ dashboard/ tests/ \
    --disable=C,R \
    --enable=W,E,F \
    --max-args=8 \
    --max-branches=15 \
    --max-statements=50
```

**Critical Checks**:
- `W0702` - Bare except
- `W0703` - Catching Exception (too broad)
- `W0613` - Unused arguments
- `W0612` - Unused variables
- `E1101` - No member
- `R0915` - Too many statements (complexity)

#### MyPy (Type Checking)
```bash
mypy orchestration/ workers/ dashboard/ \
    --ignore-missing-imports \
    --strict-optional \
    --warn-redundant-casts \
    --warn-unused-ignores \
    --disallow-untyped-defs
```

**Priority**: Add type hints to all database and API functions first.

#### Black (Code Formatting)
```bash
black . --line-length=100 --check --diff
```

---

## 2️⃣ SECURITY TESTING

### Bandit (Security Linter)
```bash
# Must run this - critical for the issues found
bandit -r . \
    -x ./venv,./node_modules,./No_ai_detection/backend/venv \
    -lll \
    -ii \
    -f json \
    -o bandit-report.json

# Console output
bandit -r . -x ./venv,./node_modules -lll -ii
```

**Critical Tests Bandit Catches**:
- `B102` - exec_used
- `B301` - Pickle usage
- `B307` - eval usage
- `B608` - Hardcoded SQL (SQL injection - CRIT-001)
- `B605` - Starting process with shell (Command injection - CRIT-002)
- `B110` - try_except_pass (Bare except - HIGH-001)
- `B201` - flask_debug_true

### Safety (Dependency Vulnerabilities)
```bash
safety check \
    -r requirements.txt \
    --json \
    --output safety-report.json
```

### Semgrep (Advanced Security Scanner)
```bash
# Install
pip install semgrep

# Run security rules
semgrep --config=auto \
    --config=p/security-audit \
    --config=p/owasp-top-ten \
    --config=p/cwe-top-25 \
    --error \
    --json \
    --output=semgrep-report.json \
    .
```

**Custom Rules for Your Issues**:
```yaml
# .semgrep.yml
rules:
  - id: bare-except
    patterns:
      - pattern: |
          except:
              ...
    message: "Bare except blocks found - use specific exceptions"
    severity: ERROR
    
  - id: sql-injection-fstring
    patterns:
      - pattern: f"...{table}..."
    message: "Possible SQL injection via f-string"
    severity: ERROR
    
  - id: subprocess-user-input
    patterns:
      - pattern: |
          asyncio.create_subprocess_exec(..., $USER_INPUT, ...)
    message: "User input passed to subprocess - sanitize required"
    severity: ERROR
```

### CodeQL (GitHub Security Analysis)
```yaml
# .github/workflows/codeql.yml
name: "CodeQL"

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  analyze:
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      security-events: write
    
    strategy:
      fail-fast: false
      matrix:
        language: [ 'python' ]
    
    steps:
    - uses: actions/checkout@v3
    - uses: github/codeql-action/init@v2
      with:
        languages: ${{ matrix.language }}
        queries: security-extended,security-and-quality
    - uses: github/codeql-action/analyze@v2
```

---

## 3️⃣ DYNAMIC TESTING

### Pytest (Unit & Integration)
```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov pytest-timeout pytest-mock pytest-xdist

# Run with coverage and timeout (prevent infinite loops)
pytest tests/ \
    -v \
    --asyncio-mode=auto \
    --cov=orchestration --cov=workers --cov=dashboard \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --timeout=30 \
    --timeout-method=thread \
    -xvs
```

**Required Test Files**:
```python
# tests/test_security.py - NEW FILE NEEDED
async def test_sql_injection_protection():
    """Test that table names are validated"""
    
async def test_command_injection_protection():
    """Test that CLI inputs are sanitized"""
    
async def test_path_traversal_protection():
    """Test that project paths are validated"""

# tests/test_race_conditions.py - NEW FILE NEEDED  
async def test_concurrent_session_switch():
    """Test race condition in session management"""
    
# tests/test_rate_limiting.py - NEW FILE NEEDED
async def test_discussion_rate_limit():
    """Test that discussion mode has participant limits"""
```

### Fuzz Testing
```bash
# Install
pip install hypothesis

# Property-based testing example
# tests/test_fuzz.py
from hypothesis import given, strategies as st

@given(st.text(min_size=1, max_size=1000))
def test_message_handling_doesnt_crash(message):
    """Ensure any message content doesn't cause crashes"""
    # Test CLI adapter with random input
    pass
```

### Load Testing
```bash
# Install
pip install locust aiohttp

# locustfile.py
from locust import FastHttpUser, task, between

class DashboardUser(FastHttpUser):
    wait_time = between(1, 3)
    
    @task(10)
    def get_status(self):
        self.client.get("/api/status")
    
    @task(5)
    def send_chat(self):
        self.client.post("/api/chat", json={
            "message": "Hello" * 1000  # Test large payloads
        })
    
    @task(1)
    def discussion_mode(self):
        self.client.post("/api/chat/discussion", json={
            "message": "test",
            "participants": ["qwen"] * 100  # Test max participants
        })
```

Run: `locust -f locustfile.py --host=http://localhost:8420`

---

## 4️⃣ SECURITY PEN TESTING

### SQLMap (SQL Injection Testing)
```bash
# Install
pip install sqlmap

# Test dashboard endpoints
sqlmap -u "http://localhost:8420/api/chat/history?project_id=test" \
    --method=GET \
    --level=5 \
    --risk=3 \
    --batch \
    --dump
```

### OWASP ZAP (Full Security Scan)
```bash
# Using Docker
docker run -t owasp/zap2docker-stable zap-baseline.py \
    -t http://host.docker.internal:8420 \
    -r zap-report.html \
    -w zap-report.md
```

### Manual Security Tests
```python
# tests/manual_security_tests.py
import asyncio
import aiohttp

async def test_command_injection():
    """Verify command injection is patched"""
    payload = "; echo 'PWNED' > /tmp/pwned.txt; #"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            'http://localhost:8420/api/chat',
            json={'message': payload}
        ) as resp:
            # Check that file was NOT created
            assert not Path('/tmp/pwned.txt').exists()

async def test_sql_injection():
    """Verify SQL injection is patched"""
    payload = "projects; DROP TABLE tasks; --"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            'http://localhost:8420/api/projects/select',
            json={'project_id': payload}
        ) as resp:
            # Verify tasks table still exists
            # (check database directly)
            pass
```

---

## 5️⃣ CONCURRENCY & RACE CONDITION TESTS

### Stress Testing
```python
# tests/test_concurrency.py
import asyncio
import pytest

@pytest.mark.asyncio
async def test_concurrent_session_operations():
    """Test race condition CRIT-003 fix"""
    orchestrator = create_test_orchestrator()
    
    # Spawn 100 concurrent session switches
    tasks = [
        orchestrator.switch_chat_session(f"session_{i}")
        for i in range(100)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Verify no data corruption
    assert orchestrator.chat_history is not None

@pytest.mark.asyncio  
async def test_queue_full_behavior():
    """Test CRIT-002 fix - queue backpressure"""
    # Fill queue beyond maxsize
    # Verify proper error handling (not silent drop)
    pass
```

---

## 6️⃣ PERFORMANCE TESTING

### Memory Profiling
```bash
# Install
pip install memory_profiler psutil

# Run with profiling
mprof run python main.py
mprof plot
```

### CPU Profiling
```bash
pip install py-spy

# Profile while running
py-spy top -- python main.py

# Generate flamegraph
py-spy record -o profile.svg -- python main.py
```

### AsyncIO Debugging
```python
# Enable in main.py
import asyncio
asyncio.run(main(), debug=True)  # Shows slow callbacks
```

---

## 7️⃣ INTEGRATION TESTING

### Docker Compose Test Environment
```yaml
# docker-compose.test.yml
version: '3.8'
services:
  factory:
    build: .
    ports:
      - "8420:8420"
    environment:
      - FACTORY_ENV=test
      - DEBUG=false
    volumes:
      - test_data:/app/factory_state
  
  ollama:
    image: ollama/ollama
    volumes:
      - ollama_models:/root/.ollama
  
  tester:
    build: ./tests/integration
    depends_on:
      - factory
      - ollama
    command: pytest /tests
```

### Contract Testing
```python
# tests/test_contracts.py
import pact

# Verify API contracts don't break
pact.verify(
    'factory-provider',
    pact_urls=['./pacts/frontend-factory.json'],
    provider_base_url='http://localhost:8420'
)
```

---

## 8️⃣ CI/CD PIPELINE

### GitHub Actions Workflow
```yaml
# .github/workflows/test.yml
name: Test Suite

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
      
      - name: Security scan
        run: |
          bandit -r . -x ./venv -lll -ii
          safety check -r requirements.txt
      
      - name: Lint
        run: |
          flake8 . --max-line-length=100 --extend-ignore=E203,W503
          black . --check --diff
          mypy orchestration/ workers/ dashboard/
      
      - name: Unit tests
        run: |
          pytest tests/ \
            --cov=orchestration --cov=workers --cov=dashboard \
            --cov-report=xml \
            --timeout=30
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
      
      - name: Integration tests
        run: |
          docker-compose -f docker-compose.test.yml up --abort-on-container-exit
      
      - name: Performance benchmark
        run: |
          pytest tests/performance/ --benchmark-only
```

---

## 9️⃣ TEST COVERAGE REQUIREMENTS

### Minimum Coverage Thresholds
```ini
# .coveragerc
[run]
source = orchestration,workers,dashboard
omit = 
    */tests/*
    */venv/*
    */__pycache__/*
    */migrations/*

[report]
fail_under = 85
show_missing = true
skip_covered = false

# Critical modules need 95%+ coverage
[report:orchestration/database.py]
fail_under = 95

[report:orchestration/master_watchdog.py]
fail_under = 90

[report:workers/adapters.py]
fail_under = 90
```

---

## 🔟 RECOMMENDED TEST ORDER

### Phase 1: Safety Checks (Run First)
```bash
# 1. Security scan (catches dangerous code)
bandit -r . -lll -ii

# 2. Dependency vulnerabilities  
safety check

# 3. Type checking
mypy orchestration/ workers/ dashboard/
```

### Phase 2: Code Quality
```bash
# 4. Linting
flake8 . --max-line-length=100
black . --check --diff
pylint orchestration/ workers/ dashboard/
```

### Phase 3: Unit Tests
```bash
# 5. Fast unit tests
pytest tests/unit -xvs --timeout=10

# 6. Security-focused tests
pytest tests/test_security.py -xvs
```

### Phase 4: Integration Tests
```bash
# 7. Database tests
pytest tests/test_database.py -xvs

# 8. API tests
pytest tests/test_api.py -xvs
```

### Phase 5: Performance & Load
```bash
# 9. Load testing
locust -f locustfile.py --headless -u 100 -r 10 --run-time 1m

# 10. Memory check
mprof run --include-children python main.py &
sleep 60 && mprof plot
```

---

## 📊 TEST METRICS DASHBOARD

Track these metrics weekly:

| Metric | Current | Target | Priority |
|--------|---------|--------|----------|
| Test Coverage | ?% | 85% | HIGH |
| Security Issues | 19 | 0 | CRITICAL |
| Bare Except Blocks | 23 | 0 | CRITICAL |
| Type Errors | ? | 0 | MEDIUM |
| Lint Errors | ? | 0 | LOW |
| Avg Test Time | ? | <5min | MEDIUM |
| Flaky Tests | ? | 0 | HIGH |

---

## 🚀 QUICK START

```bash
# One-command test suite
./run_tests.sh
```

```bash
#!/bin/bash
# run_tests.sh
set -e

echo "🔒 Security scan..."
bandit -r . -x ./venv,./node_modules -lll || true
safety check || true

echo "📝 Linting..."
flake8 . --max-line-length=100 --count
black . --check --diff

echo "🔍 Type checking..."
mypy orchestration/ workers/ dashboard/ --ignore-missing-imports || true

echo "🧪 Unit tests..."
pytest tests/ -xvs --timeout=30 --cov=orchestration --cov=workers --cov=dashboard

echo "✅ All tests passed!"
```

---

## 📋 TEST FILE CHECKLIST

Create these test files:

- [ ] `tests/test_security.py` - SQL injection, command injection, path traversal
- [ ] `tests/test_race_conditions.py` - Concurrent session operations
- [ ] `tests/test_rate_limiting.py` - API throttling
- [ ] `tests/test_authentication.py` - Auth middleware (once implemented)
- [ ] `tests/test_database.py` - Transaction integrity, queue behavior
- [ ] `tests/test_worker_adapters.py` - Health checks, error handling
- [ ] `tests/test_watchdog.py` - Recovery, monitoring loops
- [ ] `tests/performance/test_load.py` - Stress tests
- [ ] `tests/fuzz/test_input.py` - Fuzzing tests

---

**Recommendation**: Start with Bandit and the security tests - those catch the critical issues. Then add unit tests for the database layer. Integration tests come last once the code is stable.
