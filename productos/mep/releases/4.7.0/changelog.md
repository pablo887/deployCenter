# MEP 4.7.0

_Publicado 2026-09-18 · canal estable_

> Release de ejemplo, incluido para que el toolchain tenga contra qué correr.
> Reemplazar por el primer release real cuando se releve el inventario de MEP.

## Qué cambia

- Timeout de firma configurable por variable de entorno, para los clientes cuyo
  HSM responde por encima de los 30 segundos por defecto.

## Impacto en el despliegue

- **Variables nuevas:** `MEP_FIRMA_TIMEOUT_S`, obligatoria, con default `30`.
  El preflight la completa sola; no hace falta pedirle nada al cliente.
- **Migraciones de base:** no. El release va por circuito autoservicio y el
  rollback automático está habilitado.
- **Ruta de upgrade:** desde 4.5.0 en adelante. Un cliente en 4.4.x tiene que
  pasar antes por 4.5.0.

## Vuelta atrás

Automática si falla la verificación. El punto de retorno se toma antes de tocar
nada y apunta a los digests que estaban corriendo.
