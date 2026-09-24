# Imagen del agente. Corre en el host Docker del cliente, una por host.
#
# Se le monta el socket de Docker y el directorio raíz de los stacks. El
# --raiz-permitida acota qué puede tocar: el agente no opera fuera de ahí,
# y eso es verificable leyendo el compose con el que se lo levanta.
#
#   docker build -t registry.accusys.com.ar/deploycenter/agente:0.1.0 .

FROM python:3.12-slim AS base

# docker-cli y el plugin de compose: el agente no habla con el socket a mano,
# usa los mismos comandos que usaría una persona.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && apt-get purge -y gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

# El agente necesita el socket de Docker, que es root en el host. No hay forma
# de evitarlo con este modelo de despliegue; lo que sí se acota es qué puede
# hacer con él, y eso está en el catálogo cerrado de operaciones de
# deploycenter/agente/docker.py.
ENV DC_RAIZ_PERMITIDA=/stacks

ENTRYPOINT ["dc-agent"]
CMD ["--help"]
