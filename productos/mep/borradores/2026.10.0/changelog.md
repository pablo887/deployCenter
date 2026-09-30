# MEP 2026.10.0

_Borrador · canal estable_

> Primer release del estándar. Trae las mismas versiones que corren hoy en
> Naranja X; lo que cambia es cómo se instala.

## Qué cambia

- El docker-compose.yml deja de escribirse a mano: lo genera deployCenter desde
  la plantilla de MEP, con cada imagen fijada por digest.
- Las versiones de cada servicio salen del release, no del .env. Las variables
  `MEP_*_VERSION` del .env dejan de usarse (salvo `MEP_CONNECTOR_VERSION`, que
  el front sigue mostrando y la informa el stack del banco).
- El connector y el contable salen de este stack: son de cada banco y van en
  su propio producto (`mep-<banco>`), en otro directorio, por la misma red.
- Cada servicio recibe solo las variables que usa: la clave del BCRA ya no
  llega al front ni la de la base al worker.
- `mep-api` y `mep-worker` esperan a que Redis y RabbitMQ estén sanos.
- Redis queda en un tag fijo (7.2) en vez de `redis` sin versión.

## Impacto en el despliegue

- **Variables nuevas:** `BCRA_ENDPOINT` es obligatoria y sin default (URL de
  homologación o de producción, según el ambiente). `BCRA_IP_HOMOLOGACION` y
  `BCRA_IP_PRODUCCION` son opcionales: van si el banco no resuelve esos nombres.
- **Migraciones de base:** no. Son las mismas versiones que ya corren.
- **Adopción:** el primer despliegue reemplaza el compose hecho a mano, que
  queda como punto de retorno. Los contenedores conservan su nombre, sus redes
  y sus datos (`../../data/apps/mep`, o `MEP_DATOS`). Como el connector y el
  contable salen de este stack, `up --remove-orphans` los baja: el stack del
  banco se despliega en la misma ventana, inmediatamente después.
