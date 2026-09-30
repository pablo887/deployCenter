# Cliente demo

Un cliente ficticio para poder correr el toolchain de punta a punta sin tocar
nada real. No hay datos de ningún cliente acá adentro.

```bash
mkdir -p /tmp/demo/mep /tmp/demo/mep-naranjax
cp ejemplos/cliente-demo/.env.ejemplo       /tmp/demo/mep/.env
cp ejemplos/cliente-demo/.env.banco.ejemplo /tmp/demo/mep-naranjax/.env

# ¿alcanza el entorno para el núcleo de MEP?
dc variables productos/mep/borradores/2026.10.0/manifiesto.json --entorno /tmp/demo/mep/.env

# generar los dos compose (son borradores: todavía van por tag)
dc render --sin-pin --manifiesto productos/mep/borradores/2026.10.0/manifiesto.json \
          --entorno /tmp/demo/mep/.env --salida /tmp/demo/mep/docker-compose.yml
dc render --sin-pin --manifiesto productos/mep-naranjax/borradores/2026.10.0/manifiesto.json \
          --entorno /tmp/demo/mep-naranjax/.env --salida /tmp/demo/mep-naranjax/docker-compose.yml
```

El `.env` se llama así, con punto, porque es el nombre que docker compose lee
solo para la interpolación `${VARIABLE}` del compose. La plantilla de MEP no usa
`env_file:`: cada servicio recibe solo las variables que nombra.

Un host con dos productos tiene dos directorios, cada uno con su `.env` y su
`docker-compose.yml`. El agente los atiende a los dos.
