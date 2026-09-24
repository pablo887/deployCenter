"""Errores del toolchain. Uno por causa, para que el CLI pueda dar un código de salida distinto."""


class ErrorDeployCenter(Exception):
    """Base de todos los errores propios."""


class ErrorValidacion(ErrorDeployCenter):
    """El manifiesto no cumple el contrato. Trae la lista completa de problemas."""

    def __init__(self, problemas):
        self.problemas = list(problemas)
        super().__init__("; ".join(self.problemas))


class ErrorHerramienta(ErrorDeployCenter):
    """Falta una herramienta externa (docker, cosign) o devolvió error."""


class ErrorArchivo(ErrorDeployCenter):
    """No se pudo leer o parsear un archivo de entrada."""
