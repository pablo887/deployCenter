#!/usr/bin/env bash
# Prueba de punta a punta del agente contra Docker real.
#
# Despliega un stack de nginx, lo actualiza, y después despliega una versión que
# no levanta para ver la vuelta atrás automática. No toca nada del repo ni de
# ningún cliente: todo ocurre en un directorio temporal.
#
#   bash ejemplos/e2e-agente.sh
#
# Necesita el engine de Docker corriendo, no solo el CLI instalado.
set -u

if ! docker info >/dev/null 2>&1; then
  echo "el engine de Docker no responde: levantalo y volvé a correr esto" >&2
  exit 2
fi
cd /c/Users/pablo.ortiz/source/repos/deploycenter
export PYTHONPATH=src
W=$(mktemp -d)
STACK="$W/stack/demo"
mkdir -p "$STACK" "$W/pkg-1" "$W/pkg-2" "$W/pkg-3"

echo "=== 0. resolver digests reales de nginx ==="
D1=$(python -c "from deploycenter import digests; print(digests.resolver('nginx:1.27-alpine'))")
D2=$(python -c "from deploycenter import digests; print(digests.resolver('nginx:1.26-alpine'))")
echo "  v1 -> $D1"
echo "  v2 -> $D2"

plantilla() { # $1 = archivo destino, $2 = test de healthcheck de api
cat > "$1" <<PLANTILLA
name: {{ producto }}
services:
  api:
    image: {{ imagenes.api }}
    ports:
      - "127.0.0.1:\${DEMO_PUERTO_API}:80"
    healthcheck:
      test: ["CMD-SHELL", "$2"]
      interval: 2s
      timeout: 3s
      retries: 4
      start_period: 1s
  web:
    image: {{ imagenes.web }}
    ports:
      - "127.0.0.1:\${DEMO_PUERTO_WEB}:80"
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1"]
      interval: 2s
      timeout: 3s
      retries: 4
      start_period: 1s
PLANTILLA
}

manifiesto() { # $1 destino  $2 release  $3 digest  $4 desde
python - "$1" "$2" "$3" "$4" <<'PY'
import json, sys
destino, release, digest, desde = sys.argv[1:5]
json.dump({
  "producto": "demo", "release": release, "publicado": "2026-09-01",
  "canal": "estable",
  "imagenes": {"api": digest, "web": digest},
  "desde_version": desde, "db_migrations": False, "rollback_seguro": True,
  "variables_nuevas": [],
  "healthchecks": [
    {"servicio": "api", "url": "http://127.0.0.1:18081/", "espera": 200, "timeout_s": 60},
    {"servicio": "web", "url": "http://127.0.0.1:18080/", "espera": 200, "timeout_s": 60}],
  "changelog": "x.md",
}, open(destino, "w"), indent=2)
PY
}

OK_HC='wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1'
MAL_HC='exit 1'

manifiesto "$W/pkg-1/manifiesto.json" 1.0.0 "$D1" "*"        ; plantilla "$W/pkg-1/compose.plantilla.yaml" "$OK_HC"
manifiesto "$W/pkg-2/manifiesto.json" 2.0.0 "$D2" ">=1.0.0"  ; plantilla "$W/pkg-2/compose.plantilla.yaml" "$OK_HC"
manifiesto "$W/pkg-3/manifiesto.json" 3.0.0 "$D2" ">=1.0.0"  ; plantilla "$W/pkg-3/compose.plantilla.yaml" "$MAL_HC"

cat > "$STACK/.env" <<'ENV'
DEMO_PUERTO_API=18081
DEMO_PUERTO_WEB=18080
ENV

AG="python -m deploycenter.agente.cli --sin-color"

echo; echo "=== 1. instalación inicial (1.0.0) ==="
$AG desplegar --instalacion "$STACK" --paquete "$W/pkg-1" --espera-cancelacion 0
echo "rc=$?"

echo; echo "=== 2. estado ==="
$AG estado --instalacion "$STACK" --servicios

echo; echo "=== 3. upgrade a 2.0.0 ==="
$AG desplegar --instalacion "$STACK" --paquete "$W/pkg-2" --espera-cancelacion 0
echo "rc=$?"
echo "  responde el puerto publicado: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/)"

echo; echo "=== 4. 3.0.0 no levanta -> vuelta atras automatica ==="
$AG desplegar --instalacion "$STACK" --paquete "$W/pkg-3" --espera-cancelacion 0
echo "rc=$?"

echo; echo "=== 5. estado despues del rollback ==="
$AG estado --instalacion "$STACK" --servicios
echo "  sigue respondiendo: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/)"

echo; echo "=== 6. historial ==="
$AG historial --instalacion "$STACK"

echo; echo "=== limpieza ==="
(cd "$STACK" && docker compose down -v >/dev/null 2>&1)
rm -rf "$W"
echo "listo"
