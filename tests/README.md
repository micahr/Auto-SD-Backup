# SnapSync Test Suite

Comprehensive test suite for the SnapSync SD card backup service.

## Overview

This test suite provides comprehensive coverage of all major components of SnapSync, including:

- **Database Operations** - Schema creation, CRUD operations, deduplication
- **Configuration Management** - YAML loading, environment variables, validation
- **Backup Engine** - File scanning, upload orchestration, retry logic
- **Client Integrations** - Immich API, Unraid/SMB, MQTT
- **Service Management** - Lifecycle, approval workflow, signal handling
- **Web UI** - API endpoints, status reporting, configuration updates

## Installation

Install test dependencies:

```bash
pip install -r requirements-test.txt
```

## Running Tests

### Run all tests

```bash
pytest
```

### Run with coverage report

```bash
pytest --cov=src --cov-report=html --cov-report=term-missing
```

Coverage reports will be generated in the `htmlcov/` directory.

### Run specific test categories

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run only database tests
pytest -m db

# Run only configuration tests
pytest -m config

# Run only backup engine tests
pytest -m backup

# Run only client tests
pytest -m client

# Run only web UI tests
pytest -m web
```

### Run specific test files

```bash
# Test database module
pytest tests/test_database.py

# Test configuration module
pytest tests/test_config.py

# Test backup engine
pytest tests/test_backup_engine.py

# Test Immich client
pytest tests/test_immich_client.py
```

### Run with verbose output

```bash
pytest -v
```

### Run tests in parallel (faster)

```bash
pytest -n auto
```

## Test Structure

```
tests/
├── __init__.py              # Test package initialization
├── conftest.py              # Shared fixtures and utilities
├── test_database.py         # Database operations tests
├── test_config.py           # Configuration management tests
├── test_backup_engine.py    # Backup engine tests
├── test_immich_client.py    # Immich API client tests
├── test_web_ui.py          # Web UI API tests
└── README.md               # This file
```

## Test Fixtures

Common fixtures available in `conftest.py`:

### Temporary Resources
- `temp_dir` - Temporary directory for test files
- `temp_db_path` - Temporary database path
- `test_db` - Initialized test database instance

### Configuration
- `sample_config_dict` - Sample configuration dictionary
- `sample_config` - Sample Config object
- `sample_yaml_config` - Temporary YAML config file
- `sample_env_file` - Temporary .env file

### Mock Clients
- `mock_immich_client` - Mocked Immich API client
- `mock_unraid_client` - Mocked Unraid/SMB client
- `mock_mqtt_client` - Mocked MQTT client
- `mock_sd_detector` - Mocked SD card detector

### Test Data
- `sample_files` - Collection of test files
- `sample_file_record` - Sample database file record
- `sample_session_record` - Sample backup session record

## Test Markers

Tests are organized using pytest markers:

- `@pytest.mark.unit` - Unit tests for individual components
- `@pytest.mark.integration` - Integration tests requiring external services
- `@pytest.mark.slow` - Tests that take a long time
- `@pytest.mark.db` - Database-related tests
- `@pytest.mark.config` - Configuration tests
- `@pytest.mark.backup` - Backup engine tests
- `@pytest.mark.client` - Client integration tests
- `@pytest.mark.web` - Web UI tests
- `@pytest.mark.mqtt` - MQTT tests

## Writing New Tests

### Test Organization

1. Create a new test file: `test_<module_name>.py`
2. Import required modules and fixtures
3. Organize tests into classes by functionality
4. Use descriptive test names that explain what is being tested

### Example Test Structure

```python
"""
Tests for the example module.

Tests cover:
- Feature 1
- Feature 2
- Error handling
"""
import pytest
from src.example_module import ExampleClass


@pytest.mark.unit
class TestExampleInitialization:
    """Test ExampleClass initialization."""

    def test_default_initialization(self):
        """Test that ExampleClass initializes with defaults."""
        obj = ExampleClass()
        assert obj.property is not None

    @pytest.mark.asyncio
    async def test_async_method(self):
        """Test async method execution."""
        obj = ExampleClass()
        result = await obj.async_method()
        assert result is True
```

### Best Practices

1. **Use descriptive names** - Test names should clearly describe what they test
2. **One assertion per test** - Keep tests focused (where practical)
3. **Use fixtures** - Reuse common setup code via fixtures
4. **Mock external dependencies** - Use mocks for API calls, file I/O, etc.
5. **Test edge cases** - Don't just test the happy path
6. **Keep tests fast** - Minimize I/O and use in-memory databases
7. **Clean up resources** - Use fixtures with cleanup or context managers
8. **Document complex tests** - Add comments explaining non-obvious test logic

### Async Tests

For testing async functions, use the `@pytest.mark.asyncio` decorator:

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result is not None
```

### Mocking HTTP Requests

Use `respx` for mocking HTTP requests:

```python
import respx
import httpx

@pytest.mark.asyncio
@respx.mock
async def test_api_call():
    # Mock the endpoint
    respx.get("http://api.example.com/endpoint").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    # Test code that makes the request
    result = await make_api_call()
    assert result['status'] == 'ok'
```

### Database Tests

Database tests use an in-memory SQLite database for speed:

```python
@pytest.mark.asyncio
async def test_database_operation(test_db):
    # test_db fixture provides initialized database
    file_id = await test_db.add_file(file_info)
    assert file_id > 0
```

## Coverage Goals

Target coverage goals:

- Overall: **80%+**
- Core modules (database, config, backup_engine): **90%+**
- Client modules: **75%+**
- Web UI: **70%+**

View coverage report:

```bash
pytest --cov=src --cov-report=html
open htmlcov/index.html  # Or your browser command
```

## Continuous Integration

These tests are designed to run in CI/CD pipelines. Example GitHub Actions workflow:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      - run: pip install -r requirements.txt -r requirements-test.txt
      - run: pytest --cov=src --cov-report=xml
      - uses: codecov/codecov-action@v3
```

## Troubleshooting

### Tests Hang

If tests hang, they may have exceeded the timeout. Increase timeout:

```bash
pytest --timeout=60
```

### Import Errors

Ensure the `src` directory is in your Python path:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

Or install the package in development mode:

```bash
pip install -e .
pytest
```

### Database Lock Errors

SQLite database is locked - ensure tests properly close database connections:

```python
@pytest.fixture
async def test_db(temp_db_path):
    db = BackupDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()  # Important!
```

### Async Test Warnings

If you see warnings about event loops, ensure you're using `pytest-asyncio`:

```bash
pip install pytest-asyncio
```

And mark async tests with `@pytest.mark.asyncio`.

## Contributing

When adding new features to SnapSync:

1. Write tests for the new functionality
2. Ensure all existing tests still pass
3. Aim for >80% coverage on new code
4. Update this README if adding new test patterns

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio documentation](https://pytest-asyncio.readthedocs.io/)
- [respx documentation](https://lundberg.github.io/respx/)
- [pytest-cov documentation](https://pytest-cov.readthedocs.io/)
