.PHONY: help install dev api web test lint format build sample docker clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Install backend and frontend dependencies
	uv sync
	cd frontend && npm install

dev:  ## Run the API (port 8000) and the frontend (port 5173) with reload
	@trap 'kill 0' INT TERM EXIT; \
	uv run uvicorn reach.api.app:app --reload --port 8000 & \
	(cd frontend && npm run dev) & \
	wait

api:  ## Run only the API
	uv run uvicorn reach.api.app:app --reload --port 8000

web:  ## Run only the frontend dev server
	cd frontend && npm run dev

test:  ## Run the backend tests
	uv run pytest

lint:  ## Lint and type-check
	uv run ruff check src tests
	uv run ruff format --check src tests
	cd frontend && npm run lint

format:  ## Format the Python code
	uv run ruff format src tests
	uv run ruff check --fix src tests

build:  ## Build the frontend into frontend/dist (served by the API)
	cd frontend && npm run build

sample:  ## Optimize the bundled sample kitchen from the command line
	uv run reach optimize

docker:  ## Build the Docker image
	docker build -t reach .

clean:
	rm -rf frontend/dist .pytest_cache .ruff_cache dist
