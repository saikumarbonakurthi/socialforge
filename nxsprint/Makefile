.PHONY: env up down test lint migrate install demo

install:
	cd core && pip install -e '.[dev]'

env:
	@test -f .env || { cp .env.example .env; \
	  sed -i "s/^NXSPRINT_API_SECRET=.*/NXSPRINT_API_SECRET=$$(openssl rand -hex 32)/; s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$(openssl rand -hex 16)/" .env; \
	  echo "created .env with generated secrets"; }
	@test -f config/projects.yaml || { cp config/projects.example.yaml config/projects.yaml; \
	  echo "created config/projects.yaml from SAMPLE values (placeholder: true, dry_run only)"; }

up: env
	docker compose up --build

down:
	docker compose down

test:
	cd core && python -m pytest --cov=app --cov-report=term-missing

lint:
	cd core && ruff check . && ruff format --check .

demo:
	cd core && python -m app.demo

migrate:
	cd core && alembic upgrade head
