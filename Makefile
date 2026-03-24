.PHONY: install dev server loop-start loop-stop status test clean

install:
	pip install -e ".[api]"

dev:
	pip install -e ".[api,dev]"

server:
	uvicorn olympus.api.app:app --reload --port 8000

loop-start:
	bash scripts/core/auto-loop.sh start

loop-stop:
	bash scripts/core/auto-loop.sh stop

status:
	bash scripts/core/auto-loop.sh status

test:
	pytest tests/ -v --asyncio-mode=auto

web-install:
	cd web && npm install

web-dev:
	cd web && npm run dev

web-build:
	cd web && npm run build

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
