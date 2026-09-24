"""UI local del agente: la misma operación que el CLI, con pantalla.

Vive en el servidor del cliente y escucha en loopback. En Fase 1 es la única
interfaz gráfica; cuando exista el hub, sigue sirviendo de respaldo para el
cliente que no quiera depender de él o para el día que el hub no esté.
"""

from .servidor import HOST, PUERTO, crear_app, servir  # noqa: F401

__all__ = ["crear_app", "servir", "HOST", "PUERTO"]
