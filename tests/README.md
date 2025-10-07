# Test Suite Documentation

## Running Tests

### Run all tests
```bash
pytest tests/
```

### Run specific test file
```bash
pytest tests/test_signatures.py
pytest tests/test_duplicates.py
```

### Run tests by marker
```bash
# Run only signature tests
pytest -m signature

# Run only duplicate tests
pytest -m duplicate

# Run only unit tests (no API required)
pytest -m unit

# Run integration tests (requires API access)
pytest -m integration
```

### Run with verbose output
```bash
pytest tests/ -v
```

### Run with coverage report
```bash
pytest tests/ --cov=. --cov-report=html
```

## Test Organization

### tests/test_signatures.py
Tests signature generation consistency. CRITICAL - these must always pass.

- Signature generation for different event types
- Consistency between sync.py and signature_utils.py
- All-day event handling

### tests/test_duplicates.py
Tests duplicate detection and prevention.

- Duplicate event signature matching
- Synced event detection
- Non-synced event handling

### tests/utils/cleanup_tools.py
Utility scripts for maintenance tasks.

- Duplicate cleanup tool
- Not run as part of test suite

## Test Markers

- `@pytest.mark.signature` - Signature generation tests
- `@pytest.mark.duplicate` - Duplicate detection tests
- `@pytest.mark.unit` - Unit tests (no external dependencies)
- `@pytest.mark.integration` - Integration tests (require API)

## Adding New Tests

1. Create test file: `tests/test_[feature].py`
2. Use pytest conventions: `test_*` function names
3. Add appropriate markers
4. Update this README

## CI/CD Integration

To add to CI pipeline:

```yaml
# Example GitHub Actions
- name: Run Tests
  run: |
    pip install -r requirements.txt
    pytest tests/ -v
```
