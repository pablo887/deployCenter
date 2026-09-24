# deployCenter — Fase 0

Toolchain de releases para los productos del área: MEP, Factnova, Pases & CRyL,
Repi, SML y CEDIN.

Esta es la **Fase 0** del plan: todavía no hay agente ni web. Lo que hay es lo que
convierte el release en algo ejecutable, que es el prerrequisito de todo lo demás.

| | Antes | Con Fase 0 |
|---|---|---|
| El release es | un documento en prosa | un manifiesto que una máquina valida y ejecuta |
| Las imágenes van | por tag | pinneadas por digest |
| El compose | se edita a mano en la ventana | se genera desde una plantilla versionada |
| Las variables nuevas | "agregale esta línea" | declaradas, detectadas antes de tocar nada |
| El rollback | volver a un tag que pudo cambiar | volver al mismo binario |

Nada de esto necesita la web. Con solo esto, la mitad del error humano de las
ventanas nocturnas desaparece.

## Instalación

```bash
python -m pip install -e ".[dev]"
```

Requiere Python 3.10+. Para resolver digests hace falta `docker` o `skopeo`; para
firmar, `cosign`.

## El circuito completo

```bash
make demo
```

Eso corre, contra un cliente de ejemplo y sin tocar nada real: validar el
manifiesto, comparar las variables, generar el compose y pasárselo a
`docker compose config` para confirmar que lo acepta.

## Comandos

```bash
# validar un manifiesto: schema + reglas semánticas
dc validar productos/mep/releases/4.7.0/manifiesto.json

# antes de publicar las imágenes, sin exigir digests
dc validar productos/mep/releases/4.7.0/manifiesto.json --sin-pin

# resolver los tags a digests contra el registry
dc pinear productos/mep/releases/4.7.0/manifiesto.json --escribir

# ¿el entorno de este cliente alcanza para esta versión?
dc variables productos/mep/releases/4.7.0/manifiesto.json --entorno /ruta/.env

# generar el docker-compose.yml de un cliente
dc render --producto mep --release 4.7.0 --entorno /ruta/.env --salida docker-compose.yml

# esqueleto de un release nuevo
dc nuevo-release --producto mep --version 4.7.1 --desde '>=4.5.0'

# firmar y verificar
dc firmar    productos/mep/releases/4.7.0/manifiesto.json --clave cosign.key
dc verificar productos/mep/releases/4.7.0/manifiesto.json --clave-publica cosign.pub

# el contrato, en JSON Schema
dc schema
```

Códigos de salida: `0` todo bien, `1` la validación encontró problemas, `2` error
de uso o falta una herramienta externa. El `1` es el que corta el pipeline.

## Cómo está armado el repo

```
productos/<codigo>/
├── producto.yaml            metadatos, servicios y sus healthchecks
├── compose.plantilla.yaml   plantilla Jinja, versionada por Accusys
└── releases/<version>/
    ├── manifiesto.json      el contrato del release
    └── changelog.md         lo que se le muestra al cliente

src/deploycenter/            el toolchain
ejemplos/cliente-demo/       un cliente ficticio para probar
registry/                    el registry privado: decisiones y compose de dev
```

**Embarcar un producto nuevo es agregar una carpeta en `productos/`.** No se toca
código del toolchain. Lo que sí hay que verificar antes es que el producto cumpla
el contrato de producto (corre en compose, imágenes con digest, healthcheck por
servicio, variables declarables, compose desde plantilla).

## Las tres reglas que sostienen todo

**Las imágenes van por digest, nunca por tag.** Volver a `api:4.6.0` es volver a
un nombre que pudo haber sido reescrito. Volver a `api@sha256:...` es volver al
mismo binario. Sin esto el rollback es una promesa que no se puede cumplir, y por
eso `dc validar` lo exige para publicar.

**Todo servicio publicado por Accusys tiene healthcheck.** Es el criterio objetivo
de éxito del despliegue y lo que dispara el rollback automático. Un release sin
healthchecks no se puede verificar, así que no se puede revertir solo.

**Los valores del cliente no salen del servidor del cliente.** El manifiesto
declara los *nombres* de las variables; los valores viven en el `.env` del host.
La plantilla los pasa con `env_file` y con `${VARIABLE}`, que resuelve docker
compose. El compose generado no lleva ningún valor del cliente adentro: se puede
leer, versionar y mandar por mail sin cuidado.

## Migraciones y rollback

Un manifiesto con `db_migrations: true` **no puede** declarar `rollback_seguro:
true`. El validador lo rechaza, y la razón es concreta: el rollback revierte la
imagen, no la base. Si la versión nueva ya migró el esquema y el agente restaura
la imagen anterior, queda una aplicación vieja contra una base migrada — un
estado que no existía antes del despliegue y que es peor que el fallo original.

La excepción es declarar `migraciones_compatibles: true`, que significa que las
migraciones están escritas expand/contract y la versión anterior sigue
funcionando contra el esquema nuevo. Eso vuelve el rollback seguro de verdad, y
el release vuelve al circuito autoservicio.

## La regla de mantenimiento

`manifiesto.habilitado_para(m, fecha)` implementa la regla comercial, y está acá
en vez de en el hub porque el agente también la necesita:

- Se compara contra la **fecha de publicación** del release, no contra el número
  de versión. Si el cliente pagó hasta el 31/12 tiene derecho a todo lo publicado
  hasta ese día, aunque lo instale en marzo.
- Los releases con `critico_seguridad: true` se habilitan aunque el mantenimiento
  esté vencido.
- El rollback **nunca** se bloquea por mantenimiento vencido. Esa regla no vive
  acá porque no es una propiedad del release: es del circuito de despliegue, y va
  en el agente.

## Tests

```bash
make test
```

Además de probar el código, el pipeline valida **el contenido del repo**:
`tests/test_repo.py` recorre todos los manifiestos versionados y falla si alguno
no está pinneado, le falta un healthcheck o su plantilla no renderiza. Un release
que no pasa por ahí no llega a main.

## Qué falta para cerrar la Fase 0

- [ ] **Relevar los compose reales de los 16 clientes de MEP.** Es el pendiente
      número uno del documento de arquitectura y el que puede mover el plazo:
      `productos/mep/producto.yaml` tiene hoy un inventario de servicios que es
      un punto de partida, no un dato confirmado.
- [ ] Levantar el registry privado (ver `registry/README.md`)
- [ ] Definir los dominios definitivos antes de pedirle nada a 16 áreas de seguridad
- [ ] Decidir dónde viven las claves de cosign, y recién ahí sumar el paso de
      firma al pipeline
- [ ] Publicar el primer release real de MEP y validarlo de punta a punta

## Documentación

`docs/arquitectura-deploy-center.html` — el documento de arquitectura completo:
el modelo hub/agente, la multitenencia, la parametría y el plan por fases.
