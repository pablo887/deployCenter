# deployCenter — Fases 0, 1 y 2

Toolchain de releases, agente de despliegue y hub para los productos del área:
MEP, Factnova, Pases & CRyL, Repi, SML y CEDIN.

**Fase 0** convierte el release en algo ejecutable. **Fase 1** agrega el agente
que lo aplica en el servidor del cliente, lo verifica y lo revierte si falla.
**Fase 2** suma el hub: el canal con el agente (el hub encola la orden y el
agente sale a buscarla), la identidad de las personas con segundo factor y el
aislamiento por cliente en la base. Todavía no hay frontend: el hub se opera por
su API y por `dc-hub`.

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

# sellar la plantilla dentro del manifiesto, antes de firmar
dc sellar    productos/mep/releases/4.7.0/manifiesto.json --escribir

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

src/deploycenter/            el toolchain (dc)
src/deploycenter/agente/     el agente (dc-agent)
src/deploycenter/hub/        el hub (dc-hub)
supabase/migrations/         esquema del hub, RLS y hook de identidad
web/                         maqueta navegable del hub
ejemplos/                    cliente ficticio y composes de referencia del agente
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

### La UI local

```bash
dc-agent ui --raiz /opt/accusys --paquetes /var/lib/deploycenter/paquetes
```

Escucha en `http://127.0.0.1:9000`. Da lo mismo que el CLI pero con pantalla:
qué corre en cada instalación, qué paquetes hay disponibles, preflight,
despliegue con **la cuenta regresiva visible y el botón de cancelar**, vuelta
atrás, historial y logs.

El despliegue corre en un hilo y la pantalla lo sigue por polling: se puede
cerrar la pestaña, la operación la ejecuta el agente. El estado real vive en
disco, en la instalación; la UI es una ventana, no la fuente de verdad.

Tres decisiones de seguridad, porque esto corre en el servidor de un banco:

- **Escucha en loopback.** No tiene login: quien llega a la página puede
  desplegar. El acceso normal es por consola del servidor o por túnel SSH. Con
  `--host 0.0.0.0` la página lo avisa en un banner.
- **Las acciones piden un token** en un header, que se genera al arrancar y se
  imprime. Va en header y no en cookie, así que un formulario cruzado desde otra
  pestaña no lo puede mandar.
- **Se valida el header Host**, que es lo que corta el DNS rebinding.

La página no carga nada de internet: tipografías del sistema, CSS y JS inline.
El servidor del cliente no tiene salida, y hay un test que lo verifica.

Si `waitress` está instalado se usa ese servidor; si no, cae al de desarrollo de
Flask, que avisa en cada arranque que no es para producción. El extra `ui` lo
incluye.

### Cómo se instala en el cliente

`ejemplos/agente-compose.yml` tiene el compose de referencia. Lo importante es
`--raiz-permitida`: el agente no opera fuera de ese directorio, y eso se verifica
leyendo el compose con el que se lo levanta.

## El hub (Fase 2)

La web centralizada: parametría, órdenes, parque y auditoría. No ejecuta nada
remoto: deja la orden encolada y es el agente el que sale a buscarla.

### El canal con el agente

```
agente (servidor del cliente)                      hub (nube de Accusys)
  │  POST /api/agente/v1/enrolar  código de un uso ──▶ token propio; el hub guarda el hash
  │  POST /api/agente/v1/latido   inventario       ──▶ parque: qué corre dónde
  │  GET  /api/agente/v1/ordenes/siguiente  (long-poll 25 s) ◀── orden + paquete
  │  POST /api/agente/v1/ordenes/{id}/eventos  logs ──▶ la respuesta trae "cancelar rollback"
  │  POST /api/agente/v1/ordenes/{id}/resultado     ──▶ cierre
```

Todo lo inicia el agente, por 443, hacia un solo dominio. No hay puertos
entrantes ni VPN.

```bash
# agente (en el servidor del cliente)
dc-agent enrolar  --hub https://deploy.accusys.com.ar --codigo DC-XXXX-XXXX
dc-agent conectar --raiz /opt/accusys --clave-publica /etc/deploycenter/cosign.pub
```

`ejemplos/agente-conectado-compose.yml` es el compose de referencia para dejar
el agente levantado en el cliente.

**Qué decide el hub.** La habilitación se evalúa al encolar, no al ejecutar.
Una orden de despliegue o preflight se rechaza, con el motivo a la vista, si:

- el agente está fuera de línea (más de 90 s sin contacto) o revocado;
- ya hay una orden abierta sobre esa instalación (el lock, adelantado);
- el cliente está suspendido o no tiene el producto adquirido;
- el release se publicó después del fin del mantenimiento y no es crítico de
  seguridad;
- trae migraciones (circuito asistido), el cliente tiene el autoservicio
  deshabilitado o el release es del canal anticipado y el cliente no;
- la ruta de upgrade desde la versión que reporta el agente no está soportada.

El rollback no se bloquea nunca: ni por mantenimiento vencido ni por
suspensión.

**Qué verifica el agente.** El hub propone y el agente verifica. Con el hub
comprometido se pueden encolar órdenes, pero no se puede hacer desplegar algo que
Accusys no firmó:

- La firma del manifiesto se verifica con una clave pública que configura el
  cliente. El hub no la manda. Sin clave, `dc-agent conectar` no arranca, salvo
  con `--sin-firma`, que existe solo para pruebas.
- La plantilla viaja desde el hub, así que el manifiesto firmado declara su
  huella (`plantilla_sha256`, la escribe `dc sellar`) y el agente la compara.
  Además, Jinja corre en su sandbox.
- El paquete tiene que corresponder a la orden, y la instalación tiene que ser
  un nombre simple dentro de `--raiz`.

**Cuando se corta la red.** Eventos y resultados pasan por un buzón en disco. Si
el hub no responde, el despliegue sigue, termina, verifica o revierte, y el
buzón se vacía cuando vuelve la conexión. Un hub caído no demora la cuenta
regresiva del rollback. Con el token revocado, el agente se detiene.

### Identidad y aislamiento por cliente

Las personas entran con el JWT de su proveedor de identidad (Supabase Auth, u
otro que publique sus claves). El hub valida firma, emisor, audiencia,
vencimiento y **segundo factor** (`aal2`); los agentes siguen con su token
propio y nunca usan el proveedor.

**Qué puede hacer cada uno no sale del token**: sale de `usuarios_tenant` y
`usuarios_accusys`, en cada pedido. Quitarle el rol a alguien corta el acceso en
el acto, sin esperar a que venza su sesión.

| Rol | Lado | Puede |
|---|---|---|
| Lector | Cliente | ver parque, órdenes y auditoría de su organización |
| Operador | Cliente | ordenar preflight, despliegue y rollback; cancelar |
| Aprobador | Cliente | administrar usuarios de su organización; habilitar a Accusys por un plazo |
| Soporte | Accusys | ver todo el parque; emitir códigos y revocar agentes; ordenar **solo con habilitación vigente del cliente** |
| Publicador | Accusys | (releases: todavía por el repo y `dc sellar`/`dc firmar`) |
| Comercial | Accusys | alta de clientes y parametría comercial |

**Dos barreras.** El hub chequea el rol en la aplicación y, en Postgres, además
corre cada operación de una persona con su identidad (`set local role
authenticated` y sus claims), así que las políticas RLS de
`supabase/migrations/` son una segunda barrera independiente: una consulta sin
filtro no devuelve filas de otro cliente aunque el código se equivoque.
`tests/test_hub_postgres*.py` lo prueban contra un Postgres real, incluso
apagando los chequeos de la aplicación. Los hashes de tokens y códigos no se
pueden leer desde la web ni con acceso directo a la base.

### Levantarlo

```bash
# con Supabase: el esquema, la RLS y el hook
supabase db push                       # o: DC_HUB_DB=… dc-hub migrar
# sin salida al 5432 (solo HTTPS): la Management API, con el mismo registro
SUPABASE_URL=https://<proyecto>.supabase.co SUPABASE_ACCESS_TOKEN=sbp_… \
  dc-hub migrar --via-api
# Authentication → Hooks → Custom Access Token → public.custom_access_token_hook
# Authentication → Sign In / Up: sin registro abierto; MFA (TOTP) habilitado

# prueba de integración contra el proyecto real (usuarios, TOTP, JWKS, hook y
# RLS con los claims reales; borra todo al final). Se saltea sin las variables.
# Necesita además SUPABASE_PUBLISHABLE_KEY y SUPABASE_SECRET_KEY.
python ejemplos/integracion-supabase.py

export DC_HUB_DB=postgresql+psycopg://…     # la conexión a Postgres del proyecto
export SUPABASE_URL=https://<proyecto>.supabase.co   # de acá salen JWKS y emisor
dc-hub usuario <uuid> --rol soporte --nombre "…"      # los de Accusys, solo por consola
dc-hub tenant andino "Banco Andino"
dc-hub adquirir andino mep --hasta 2026-12-31
dc-hub servir --puerto 8000

# desarrollo local, sin proveedor: SQLite y tokens firmados con un secreto
export DC_JWT_SECRET=<32+ caracteres> DC_HUB_DB=sqlite:///hub.db
dc-hub usuario <uuid> --rol operador --tenant andino
dc-hub token-dev <uuid>                # JWT con aal2, para probar la API
```

Con SQLite no hay RLS: los permisos los aplica solo el hub. Sirve para
desarrollar, no para producción.

## Maqueta del hub (`web/`)

Maqueta navegable del hub web de las Fases 2 y 3, sin backend: HTML, CSS y JS
estáticos con datos ficticios y estado en `localStorage`. Sirve como referencia
de diseño y de circuito para construir el hub real.

```bash
cd web && python3 -m http.server 8080   # http://localhost:8080
```

Del lado del cliente muestra las instalaciones, el catálogo con la regla de
habilitación, el despliegue (preflight, variables, doble aprobación,
verificación y rollback con cuenta regresiva), el historial y los usuarios. Del
lado de Accusys muestra el tablero de parque, la parametría comercial, la
publicación de releases, los agentes y la auditoría. Desde el menú de usuario se
cambia de rol.

| Archivo | Contenido |
| --- | --- |
| `web/index.html` | Punto de entrada |
| `web/styles.css` | Estilos y tokens (claro y oscuro), los mismos del documento |
| `web/data.js` | Datos de ejemplo: productos, releases, clientes, instalaciones |
| `web/app.js` | Vistas, reglas de habilitación y simulación del agente |


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
- [ ] Auto-update del agente, que nunca debe ocurrir durante un despliegue

### Fase 2

- [x] Canal hub–agente: enrolamiento, latido, órdenes con long-poll, eventos,
      resultado, cancelación del rollback desde el hub y buzón ante cortes
- [x] Identidad por JWT (JWKS o secreto), segundo factor obligatorio, roles
      desde las tablas, RLS por cliente, habilitaciones y auditoría
- [x] Migraciones SQL versionadas (`supabase/migrations/`)
- [x] `dc-hub migrar --via-api`: migraciones por la Management API, para
      entornos sin salida al puerto de Postgres
- [ ] Conectar el proyecto de Supabase real (`deploycenter-dev`): TOTP ya está
      habilitado; falta aplicar las migraciones (`dc-hub migrar --via-api`),
      activar el hook, cerrar el registro abierto y correr
      `ejemplos/integracion-supabase.py`. Después, el dominio propio
      (`auth.accusys.com.ar`)
- [ ] Invitaciones: hoy el usuario se crea en el proveedor y se le asigna el rol
      con `dc-hub usuario` o la API; falta que el hub mande la invitación
- [ ] Variables nuevas desde la web: el formulario tiene que escribir en el
      `.env` del servidor sin que el valor pase por el hub
- [ ] Órdenes entregadas sin respuesta: si el agente muere después de tomar
      una orden, queda `entregada`. Falta marcarla vencida pasado un plazo.
- [ ] Límite de intentos en el enrolamiento (en el proxy de entrada)
- [ ] Imagen del hub y su despliegue en la nube de Accusys
- [ ] Frontend real a partir de la maqueta de `web/`

## Documentación

`docs/arquitectura-deploy-center.html` — el documento de arquitectura completo:
el modelo hub/agente, la multitenencia, la parametría y el plan por fases.
