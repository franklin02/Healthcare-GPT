# Testing Guide for Healthcare-GPT

This directory contains test files for the Healthcare-GPT project.

## Test Files

- `test_gdelt_seeds.py` — Tests for GDELT data seed filtering and validation
- `test_helpers.py` — Tests for helper utility functions
- `test_scooper.py` — Tests for HTML parsing and scraping engine
- `test_runner.py` — Tests for the main GDELT runner

## Prerequisites

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Ensure you're in the project root directory:
   ```bash
   cd Healthcare-GPT
   ```

## Running Tests

### Run All Tests
```bash
pytest
```

### Run Specific Test File
```bash
pytest tests/test_gdelt_seeds.py
pytest tests/test_helpers.py
pytest tests/test_scooper.py
pytest tests/test_runner.py
```

### Run Specific Test Class or Function
```bash
pytest tests/test_gdelt_seeds.py::TestIsUsLocated
pytest tests/test_gdelt_seeds.py::TestIsUsLocated::test_us_location_present
```

### Run Coverage Report
```bash
pytest --cov --cov-report html
pytest --cov --cov-report html tests/test_gdelt_seeds.py
```

### View Coverage Report
http://127.0.0.1:3000/htmlcov
