.PHONY: install test cov lint demo report bench docker clean
install:
	pip install -e ".[dev]"
test:
	python -m pytest -q
cov:
	coverage run -m pytest -q && coverage report
lint:
	ruff check .
demo:
	lakehouseguard demo --root lakehouse_data --out site
report:
	lakehouseguard report --root lakehouse_data --out site
bench:
	python benchmarks/run_benchmark.py --seeds 20
docker:
	docker compose up --build
clean:
	rm -rf lakehouse_data site .coverage htmlcov .pytest_cache build dist *.egg-info
