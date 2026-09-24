# Cliente demo

Un cliente ficticio para poder correr el toolchain de punta a punta sin tocar
nada real. No hay datos de ningún cliente acá adentro.

```bash
cp ejemplos/cliente-demo/.env.ejemplo /tmp/demo/.env

# ¿alcanza el entorno para el release 4.7.0?
dc variables productos/mep/releases/4.7.0/manifiesto.json --entorno /tmp/demo/.env

# generar el compose de ese cliente
dc render --producto mep --release 4.7.0 \
          --entorno /tmp/demo/.env \
          --salida /tmp/demo/docker-compose.yml
```

El `.env` se llama así, con punto, porque es el nombre que docker compose lee
solo: el mismo archivo alimenta el `env_file:` de los servicios y la
interpolación `${VARIABLE}` del compose.

Un host con dos productos tiene dos directorios, cada uno con su `.env` y su
`docker-compose.yml`. El agente los atiende a los dos.
