.PHONY: instalar test lint validar demo e2e ui hub limpiar

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
# Es el estándar de MEP (todavía en borrador, por tag): el núcleo y el stack de
# un banco, cada uno en su directorio, como van en el servidor del cliente.
MEP_BORRADOR := productos/mep/borradores/2026.10.0/manifiesto.json
BANCO_BORRADOR := productos/mep-naranjax/borradores/2026.10.0/manifiesto.json

demo:
	@mkdir -p .demo/mep .demo/mep-naranjax
	@cp ejemplos/cliente-demo/.env.ejemplo .demo/mep/.env
	@cp ejemplos/cliente-demo/.env.banco.ejemplo .demo/mep-naranjax/.env
	$(PY) -m deploycenter.cli validar --sin-pin $(MEP_BORRADOR)
	$(PY) -m deploycenter.cli variables $(MEP_BORRADOR) --entorno .demo/mep/.env
	$(PY) -m deploycenter.cli render --sin-pin --manifiesto $(MEP_BORRADOR) \
		--entorno .demo/mep/.env --salida .demo/mep/docker-compose.yml
	$(PY) -m deploycenter.cli validar --sin-pin $(BANCO_BORRADOR)
	$(PY) -m deploycenter.cli render --sin-pin --manifiesto $(BANCO_BORRADOR) \
		--entorno .demo/mep-naranjax/.env --salida .demo/mep-naranjax/docker-compose.yml
	@echo
	@cd .demo/mep && docker compose config --quiet && echo "docker compose acepta el núcleo"
	@cd .demo/mep-naranjax && docker compose config --quiet && echo "docker compose acepta el stack del banco"

limpiar:
	rm -rf .demo .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# Despliegue real contra Docker: levanta un stack de nginx, lo actualiza y
# prueba la vuelta atrás. Necesita el engine de Docker corriendo.
e2e:
	bash ejemplos/e2e-agente.sh

# UI local contra un entorno de demo en /tmp. Ctrl-C para terminar.
ui:
	$(PY) -m deploycenter.agente.cli ui --raiz .demo/stacks --paquetes .demo/paquetes

# Hub local contra un SQLite en .demo/. Ctrl-C para terminar.
hub:
	@mkdir -p .demo
	DC_HUB_DB=sqlite:///.demo/hub.db $(PY) -m deploycenter.hub.cli servir
