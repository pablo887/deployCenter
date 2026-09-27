"""deployCenter — toolchain de releases y despliegue para los productos del área.

Fase 0: el release deja de ser prosa y pasa a ser un manifiesto ejecutable, las
imágenes se pinnean por digest y el docker-compose.yml se genera desde una
plantilla versionada en vez de editarse a mano.
"""

__version__ = "0.2.0"

from . import compose, digests, firma, manifiesto, variables, versiones  # noqa: F401

__all__ = ["compose", "digests", "firma", "manifiesto", "variables", "versiones", "__version__"]
