# deployCenter — Fases 0 y 1

Toolchain de releases y agente de despliegue para los productos del área: MEP,
Factnova, Pases & CRyL, Repi, SML y CEDIN.

**Fase 0** convierte el release en algo ejecutable. **Fase 1** agrega el agente
que lo aplica en el servidor del cliente, lo verifica y lo revierte si falla.
Todavía no hay hub web: el despliegue lo dispara el CLI del agente.

| | Antes | Ahora |
|---|---|---|
| El release es | un documento en prosa | un manifiesto que una máquina valida y ejecuta |
| Las imágenes van | por tag | pinneadas por digest |
| El compose | se edita a mano en la ventana | se genera desde una plantilla versionada |
| Las variables nuevas | "agregale esta línea" | declaradas, detectadas antes de tocar nada |
| El rollback | volver a un tag que pudo cambiar | volver al mismo binario, automático si falla |
| El despliegue | comandos a mano en la ventana | `dc-agent desplegar`, con preflight y verificación |

Nada de esto necesita la web. Con solo esto, la ventana nocturna deja de
depender de que alguien tipee bien.

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

## El agente (Fase 1)

Corre en el host Docker del cliente, **uno por host**, y atiende todos los
productos instalados ahí. Cada producto tiene su directorio de stack con estado,
historial y punto de retorno independientes.

```bash
# ¿qué corre hoy acá?
dc-agent estado --instalacion /opt/accusys/mep --servicios

# comprobaciones previas, sin tocar nada de lo que está corriendo
dc-agent preflight --instalacion /opt/accusys/mep --paquete /tmp/mep-4.7.0

# desplegar: preflight, punto de retorno, up, verificación y vuelta atrás
dc-agent desplegar --instalacion /opt/accusys/mep --paquete /tmp/mep-4.7.0

# volver a la versión anterior a pedido
dc-agent rollback --instalacion /opt/accusys/mep --si

dc-agent historial --instalacion /opt/accusys/mep
dc-agent logs --instalacion /opt/accusys/mep --servicio api
```

El paquete lo arma la Fase 0 con `dc empaquetar --producto mep --release 4.7.0
--salida /tmp/mep-4.7.0`. Es la misma forma que va a entregar el hub, así que el
agente no cambia cuando el hub aparezca: cambia quién aprieta el botón.

### Qué hace el preflight

Corre a las tres de la tarde y no toca lo que está en producción. El compose
nuevo se prepara aparte y las imágenes se descargan apuntando ahí.

| Comprobación | Bloquea | Por qué |
|---|---|---|
| manifiesto | sí | pinneo por digest, healthchecks, coherencia |
| firma | si hay clave | un hub comprometido no puede hacer desplegar cualquier cosa |
| circuito | sí | un release con migraciones va al circuito asistido |
| ruta_upgrade | sí | y te dice por qué versión intermedia pasar |
| variables | sí | las obligatorias sin default, antes de la ventana |
| espacio | sí | antes de descargar, no a mitad |
| estado_actual | no | si el stack ya estaba caído, conviene saberlo antes |
| compose | sí | que el archivo generado sea válido |
| descarga | sí | pull anticipado y verificación de que quedó el digest exacto |

Si esto pasa, la ventana dura minutos: lo lento y lo falible ya ocurrió.

### Qué pasa cuando falla

```
aplicar → verificar → falla → AVISO → cuenta regresiva (60s) → rollback
```

Tres decisiones que están en `agente/despliegue.py` y conviene no perder:

**El aviso sale antes del rollback, no después.** Primero se notifica, después
arranca la cuenta regresiva. Un operador que sabe qué está pasando puede frenarla
con Ctrl-C y quedarse con el sistema roto para diagnosticarlo.

**Si el rollback no es seguro, no se revierte.** Con `rollback_seguro: false` el
agente corta, marca la instalación como degradada y escala. Revertir la imagen
contra una base ya migrada deja un estado peor que el fallo.

**La verificación no da por bueno lo que no comprobó.** El smoke test HTTP
necesita que el agente alcance la red del stack; cuando no puede resolver el
nombre, se reporta *omitido*, no exitoso. El criterio que siempre está disponible
es el estado de los contenedores.

### Probarlo de verdad

```bash
bash ejemplos/e2e-agente.sh
```

Levanta un stack real con nginx en un directorio temporal, lo actualiza, y
después despliega una versión que no levanta para ver la vuelta atrás. Necesita
el engine de Docker corriendo, no solo el CLI.

### Cómo se instala en el cliente

`ejemplos/agente-compose.yml` tiene el compose de referencia. Lo importante es
`--raiz-permitida`: el agente no opera fuera de ese directorio, y eso se verifica
leyendo el compose con el que se lo levanta.

## Tests

```bash
make test
```

El motor de despliegue se prueba con un doble de Docker, así que los caminos
que importan —la vuelta atrás, la cancelación, el lock, el release que no se
puede revertir— se ejercitan sin necesidad de un daemon.

Además de probar el código, el pipeline valida **el contenido del repo**:
`tests/test_repo.py` recorre todos los manifiestos versionados y falla si alguno
no está pinneado, le falta un healthcheck o su plantilla no renderiza. Un release
que no pasa por ahí no llega a main.

## Qué falta

### Para cerrar la Fase 0

- [ ] **Relevar los compose reales de los 16 clientes de MEP.** Es el pendiente
      número uno del documento de arquitectura y el que puede mover el plazo:
      `productos/mep/producto.yaml` tiene hoy un inventario de servicios que es
      un punto de partida, no un dato confirmado.
- [ ] Levantar el registry privado (ver `registry/README.md`)
- [ ] Definir los dominios definitivos antes de pedirle nada a 16 áreas de seguridad
- [ ] Decidir dónde viven las claves de cosign, y recién ahí sumar el paso de
      firma al pipeline
- [ ] Publicar el primer release real de MEP y validarlo de punta a punta

### Para cerrar la Fase 1

- [ ] Correr `ejemplos/e2e-agente.sh` con el engine de Docker levantado: el
      despliegue real todavía no se ejercitó de punta a punta
- [ ] UI local del agente en el puerto 9000, como respaldo cuando no haya hub
- [ ] Auto-update del agente, que nunca debe ocurrir durante un despliegue
- [ ] Enrolamiento: el token propio del agente, que hoy no existe porque no hay
      hub contra el cual enrolarse

## Documentación

`docs/arquitectura-deploy-center.html` — el documento de arquitectura completo:
el modelo hub/agente, la multitenencia, la parametría y el plan por fases.
