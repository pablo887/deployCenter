.PHONY: instalar test lint validar demo limpiar

PY ?= python

instalar:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests

# Lo mismo que corre el pipeline: valida todos los manifiestos versionados.
validar: test

# Circuito completo contra el cliente de ejemplo, sin tocar nada real.
demo:
	@mkdir -p .demo
	@cp ejemplos/cliente-demo/.env.ejemplo .demo/.env
	$(PY) -m deploycenter.cli validar productos/mep/releases/4.7.0/manifiesto.json
	$(PY) -m deploycenter.cli variables productos/mep/releases/4.7.0/manifiesto.json --entorno .demo/.env
	$(PY) -m deploycenter.cli render --producto mep --release 4.7.0 \
		--entorno .demo/.env --salida .demo/docker-compose.yml
	@echo
	@echo "compose generado en .demo/docker-compose.yml"
	@cd .demo && docker compose config --quiet && echo "docker compose lo acepta"

limpiar:
	rm -rf .demo .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
