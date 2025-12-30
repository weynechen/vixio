# run all unit tests
uv run pytest tests/ --ignore=tests/test_concurrent_xiaozhi.py -v

# run specify test
uv run pytest tests/test_control_bus.py -v

# view report
uv run pytest tests/ --cov=vixio --cov-report=html

# run intergrate tests
uv run python tests/test_concurrent_xiaozhi.py

default 5 clients

## specify clients counter(e.x. 10)
uv run python tests/test_concurrent_xiaozhi.py --clients 10

