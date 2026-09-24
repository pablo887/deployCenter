"""Agente de deployCenter — Fase 1.

Corre en el host Docker del cliente, uno por host, y atiende todos los productos
instalados ahí. Cada producto tiene su directorio de stack con su estado, su
historial y su punto de retorno independientes.

En Fase 1 lo dispara el CLI con un paquete de release que alguien bajó. En Fase 2
lo va a disparar una orden que llega del hub por una conexión saliente. El motor
de despliegue es el mismo en los dos casos.
"""

from . import despliegue, docker, instalacion, preflight, verificacion  # noqa: F401

__all__ = ["despliegue", "docker", "instalacion", "preflight", "verificacion"]
