.PHONY: up down logs test clean help

help:
	@echo "Available commands:"
	@echo "  make up     - Build and run services in background"
	@echo "  make down   - Stop all running services"
	@echo "  make logs   - Follow container log output"
	@echo "  make test   - Run backend test suite"
	@echo "  make clean  - Stop containers and remove volumes"

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

test:
	pytest backend/tests -v

clean:
	docker compose down -v
