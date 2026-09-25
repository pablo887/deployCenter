#!/bin/sh
# Agente de demostración, para probar el circuito completo en una sola máquina.
#
# Lo levanta docker-compose.yml con el perfil agente-demo. La primera vez se
# enrola con el código de DC_AGENTE_CODIGO; después ya tiene credenciales y se
# conecta directo. Prepara la instalación "demo" (producto demo, puerto 18080)
# para que el hub la vea y se pueda desplegar desde la web.
#
# Diferencias con un agente de un cliente, a propósito y solo para esta demo:
#   --sin-firma       la imagen no trae cosign y la demo no tiene clave de firma
#   --permitir-http   el hub está en la misma red de Docker, sin TLS
set -eu

TRABAJO=/var/lib/deploycenter
HUB="${DC_HUB_URL:-http://hub:8000}"
INSTALACION=/stacks/demo

if [ ! -f "$TRABAJO/credenciales.json" ]; then
  if [ -z "${DC_AGENTE_CODIGO:-}" ]; then
    echo "Falta el código de enrolamiento. Generalo con:"
    echo "  docker compose exec hub dc-hub codigo <cliente> --host demo-01"
    echo "ponelo en DC_AGENTE_CODIGO del .env y volvé a levantar el agente."
    sleep 60
    exit 1
  fi
  dc-agent --sin-color enrolar --hub "$HUB" --codigo "$DC_AGENTE_CODIGO" \
    --host "${DC_AGENTE_HOST:-demo-01}" --trabajo "$TRABAJO" --permitir-http
fi

# La instalación inicial la prepara Accusys: el directorio con el producto y el
# .env del cliente. Acá, lo mínimo para el producto demo.
if [ ! -d "$INSTALACION/.deploycenter" ]; then
  mkdir -p "$INSTALACION/.deploycenter"
  echo '{"producto": "demo"}' > "$INSTALACION/.deploycenter/estado.json"
fi
[ -f "$INSTALACION/.env" ] || echo "DEMO_PUERTO=${DEMO_PUERTO:-18080}" > "$INSTALACION/.env"

exec dc-agent --sin-color --raiz-permitida=/stacks conectar --raiz /stacks \
  --trabajo "$TRABAJO" --sin-firma --permitir-http \
  --espera-cancelacion "${DC_ESPERA_CANCELACION:-60}"
