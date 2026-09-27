"""Hub de deployCenter — Fase 2.

La web centralizada: catálogo, parametría, órdenes, parque y logs. No ejecuta nada
remoto y no tiene forma de llegar a un servidor de cliente. Deja la orden
encolada y es el agente el que sale a buscarla por 443.

    agente                                   hub
      │  POST /enrolar  (código de un uso) ──▶  token propio, se guarda con hash
      │  POST /latido   (inventario)       ──▶  estado del parque
      │  GET  /ordenes/siguiente  (long-poll)◀──  la orden, con su paquete
      │  POST /ordenes/{id}/eventos         ──▶  logs; la respuesta trae la cancelación
      │  POST /ordenes/{id}/resultado       ──▶  cierre

La lógica vive en `servicio.py` y no depende de HTTP; `api.py` es una capa fina
encima. Así las reglas —qué se puede ordenar, a quién, cuándo— se prueban sin
levantar un servidor.
"""
