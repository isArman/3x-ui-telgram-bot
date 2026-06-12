#!/bin/bash

echo "=========================================="
echo "Running VPN Bot Tests"
echo "=========================================="

# Install test dependencies
echo "Installing test dependencies..."
pip install -q -r tests/requirements.txt

# Create test database directory
mkdir -p data

# Run tests with coverage
echo ""
echo "Running tests..."
pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html

# Show summary
echo ""
echo "=========================================="
echo "Test Results Summary"
echo "=========================================="
echo "Coverage report generated in htmlcov/index.html"
echo ""
