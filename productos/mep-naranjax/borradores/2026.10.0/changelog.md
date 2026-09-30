# MEP · Naranja X 2026.10.0

_Borrador · canal estable_

> Primer release del estándar para lo propio de Naranja X. Mismas versiones que
> corren hoy: connector 1.0.31, contable 1.0.13 y API contable 1.0.14.

## Qué cambia

- Connector y contable pasan a un stack propio, separado del núcleo de MEP, en
  la misma red uw2-backend. mep-api los sigue encontrando como `mep-connector`.
- Lo que estaba escrito en el compose pasa al .env: los endpoints y
  credenciales del connector (siguen cifrados como `ENC(...)`) y la
  configuración del contable.
- El `client_secret` de Dynamics estaba en texto plano en el compose. Ahora va
  en el .env, marcado como secreto: conviene rotarlo al hacer el cambio.

## Impacto en el despliegue

- **Variables nuevas:** todas las del connector, las `CONTABLE_*` y las de la
  base (`CS_*`, las mismas del núcleo). El preflight frena si falta alguna.
- **Migraciones de base:** no.
- **Adopción:** va en la misma ventana que el núcleo, justo después: el
  despliegue estándar del núcleo baja el connector y el contable viejos.
