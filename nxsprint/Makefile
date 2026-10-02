.PHONY: env up down test lint migrate install demo smoke backup restore

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
	cd core && python -m pytest --cov=app.domain --cov-fail-under=85 --cov-report=term-missing

lint:
	cd core && ruff check . && ruff format --check .

demo:
	cd core && python -m app.demo

migrate:
	cd core && alembic upgrade head

smoke:
	@S=$$(grep '^NXSPRINT_API_SECRET=' .env | cut -d= -f2-); \
	curl -fsS localhost:8000/health && echo && \
	curl -fsS -H "Authorization: Bearer $$S" localhost:8000/status

# Values in .env are literal (docker env_file format), so they are read by the containers, never sourced by a shell.
backup:
	@mkdir -p backups; f=backups/nxsprint-$$(date +%Y%m%d-%H%M%S).dump; \
	docker compose exec -T postgres sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc' > $$f.partial \
	  && test -s $$f.partial && mv $$f.partial $$f && echo "wrote $$f" \
	  || { rm -f $$f.partial; echo "backup failed, nothing was written"; exit 1; }

# make restore FILE=backups/nxsprint-20260101-020000.dump   (replaces the current data, stop core first)
restore:
	@test -s "$(FILE)" || { echo "usage: make restore FILE=backups/<dump>  (file must exist and not be empty)"; exit 1; }
	@docker compose exec -T postgres pg_restore --list < "$(FILE)" > /dev/null || { echo "not a valid dump, nothing restored"; exit 1; }
	docker compose exec -T postgres sh -c 'pg_restore -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --clean --if-exists' < "$(FILE)"
