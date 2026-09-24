"""Preflight: todo lo que hoy falla en la ventana nocturna, comprobado antes.

La idea es simple y es la que más tiempo ahorra de todo el proyecto: correr esto
a las tres de la tarde. Si pasa, la ventana dura minutos porque lo lento y lo
falible ya ocurrió. Si falla, te enteraste de día y con margen.

Ninguna comprobación toca lo que está corriendo. El compose nuevo se prepara
aparte, en `.deploycenter/preparado/`, y las imágenes se descargan apuntando ahí:
el stack en producción sigue igual hasta el despliegue.
"""

from .. import compose as compose_mod
from .. import manifiesto as mf
from .. import variables as vars_
from .docker import ErrorDocker

ESPACIO_MINIMO_GB = 5.0


class Comprobacion:
    def __init__(self, nombre, ok, detalle, bloqueante=True):
        self.nombre, self.ok, self.detalle, self.bloqueante = nombre, ok, detalle, bloqueante

    def __repr__(self):  # pragma: no cover
        return f"<{self.nombre}: {'ok' if self.ok else 'falla'}>"


class Informe:
    def __init__(self, comprobaciones):
        self.comprobaciones = list(comprobaciones)

    @property
    def bloqueos(self):
        return [c for c in self.comprobaciones if not c.ok and c.bloqueante]

    @property
    def avisos(self):
        return [c for c in self.comprobaciones if not c.ok and not c.bloqueante]

    @property
    def ok(self):
        return not self.bloqueos

    def resumen(self):
        return {
            "ok": self.ok,
            "comprobaciones": [
                {"nombre": c.nombre, "ok": c.ok, "bloqueante": c.bloqueante,
                 "detalle": c.detalle}
                for c in self.comprobaciones
            ],
        }


class Preflight:
    def __init__(self, docker, instalacion, paquete, espacio_minimo_gb=ESPACIO_MINIMO_GB,
                 clave_publica=None):
        self.docker = docker
        self.instalacion = instalacion
        self.paquete = paquete
        self.espacio_minimo_gb = espacio_minimo_gb
        self.clave_publica = clave_publica

    # -- comprobaciones ----------------------------------------------------- #

    def _manifiesto_valido(self, manifiesto):
        problemas = mf.validar(manifiesto, exigir_pin=True)
        if problemas:
            return Comprobacion("manifiesto", False,
                                f"{len(problemas)} problema(s): " + "; ".join(problemas[:3]))
        return Comprobacion("manifiesto", True,
                            f"{manifiesto['producto']} {manifiesto['release']}, "
                            f"imágenes pinneadas por digest")

    def _firma(self, manifiesto):
        from .. import firma as firma_mod
        if self.clave_publica is None:
            return Comprobacion(
                "firma", False,
                "no hay clave pública configurada: el manifiesto no se verificó",
                bloqueante=False)
        if not self.paquete.tiene_firma():
            return Comprobacion("firma", False, "el paquete no trae manifiesto.json.sig")
        valida = firma_mod.verificar(manifiesto, self.clave_publica, self.paquete.ruta_firma)
        return Comprobacion("firma", valida,
                            "firma válida" if valida else "la firma no corresponde al manifiesto")

    def _circuito(self, manifiesto):
        if mf.es_autoservicio(manifiesto):
            return Comprobacion("circuito", True, "autoservicio")
        return Comprobacion(
            "circuito", False,
            "el release trae migraciones de esquema y no es autoservicio; "
            "hay que coordinar la ventana con Accusys")

    def _ruta_upgrade(self, manifiesto):
        instalada = self.instalacion.version_instalada()
        if not instalada:
            return Comprobacion(
                "ruta_upgrade", True,
                "no hay versión registrada: se trata como instalación inicial",
                bloqueante=False)
        if instalada == manifiesto["release"]:
            return Comprobacion("ruta_upgrade", False,
                                f"{instalada} ya está instalada")
        if mf.permite_upgrade_desde(manifiesto, instalada):
            return Comprobacion("ruta_upgrade", True,
                                f"{instalada} → {manifiesto['release']}")
        return Comprobacion(
            "ruta_upgrade", False,
            f"no se puede saltar de {instalada} a {manifiesto['release']}: "
            f"el release admite {manifiesto['desde_version']}")

    def _variables(self, manifiesto):
        entorno = self.instalacion.entorno()
        informe = vars_.informe(manifiesto, entorno)
        if informe["bloquea"]:
            nombres = ", ".join(v["nombre"] for v in informe["faltantes"])
            return Comprobacion("variables", False,
                                f"faltan sin default: {nombres}")
        if informe["completables"]:
            nombres = ", ".join(v["nombre"] for v in informe["completables"])
            return Comprobacion("variables", True,
                                f"se completan solas con su default: {nombres}")
        return Comprobacion("variables", True, "el entorno ya tiene todo")

    def _espacio(self):
        try:
            libre = self.docker.espacio_libre_gb(self.instalacion.directorio)
        except OSError as e:
            return Comprobacion("espacio", False, f"no se pudo medir el disco: {e}")
        if libre < self.espacio_minimo_gb:
            return Comprobacion(
                "espacio", False,
                f"quedan {libre:.1f} GB libres y el mínimo es {self.espacio_minimo_gb:.1f} GB")
        return Comprobacion("espacio", True, f"{libre:.1f} GB libres")

    def _preparar_compose(self, manifiesto):
        """Renderiza el compose nuevo en .deploycenter/preparado/ sin aplicarlo."""
        entorno = vars_.aplicar_defaults(manifiesto, self.instalacion.entorno())
        destino_dir = self.instalacion.interno / "preparado"
        destino_dir.mkdir(parents=True, exist_ok=True)
        destino = destino_dir / "docker-compose.yml"
        try:
            compose_mod.generar(self.paquete.ruta_plantilla, manifiesto,
                                entorno=entorno, salida=destino)
        except Exception as e:  # noqa: BLE001 - se reporta como comprobación
            return Comprobacion("compose", False, str(e)), None
        return Comprobacion("compose", True, f"generado en {destino.name}"), destino

    def _descarga(self, manifiesto, compose_preparado):
        if compose_preparado is None:
            return Comprobacion("descarga", False, "no hay compose que descargar")
        try:
            self.docker.pull(self.instalacion.directorio, archivo=compose_preparado)
        except ErrorDocker as e:
            return Comprobacion("descarga", False, f"{e}: {e.error or e.salida}")

        faltan = [s for s, ref in sorted((manifiesto.get("imagenes") or {}).items())
                  if not self.docker.imagen_presente(ref)]
        if faltan:
            return Comprobacion(
                "descarga", False,
                "se descargó pero no quedaron presentes por su digest: " + ", ".join(faltan))
        return Comprobacion("descarga", True,
                            f"{len(manifiesto.get('imagenes') or {})} imagen/es "
                            f"descargadas y verificadas por digest")

    def _estado_actual(self, manifiesto):
        """Si el stack ya está caído, conviene saberlo antes de culpar al release."""
        if not self.instalacion.ruta_compose.is_file():
            return Comprobacion("estado_actual", True,
                                "no hay stack previo: instalación inicial", bloqueante=False)
        try:
            estados = self.docker.ps(self.instalacion.directorio)
        except ErrorDocker as e:
            return Comprobacion("estado_actual", False, str(e), bloqueante=False)
        caidos = [e["servicio"] for e in estados if e["estado"] not in ("running",)]
        if caidos:
            return Comprobacion(
                "estado_actual", False,
                "hay servicios caídos antes de desplegar: " + ", ".join(sorted(caidos)),
                bloqueante=False)
        return Comprobacion("estado_actual", True, f"{len(estados)} servicio(s) arriba")

    # -- orquestación ------------------------------------------------------- #

    def correr(self, descargar=True):
        """Corre todo en orden de costo: lo barato primero, el pull al final."""
        self.paquete.exigir()
        manifiesto = self.paquete.manifiesto()

        comprobaciones = [self._manifiesto_valido(manifiesto)]
        if not comprobaciones[0].ok:
            # sin un manifiesto válido el resto solo agrega ruido
            return Informe(comprobaciones), None

        comprobaciones.append(self._firma(manifiesto))
        comprobaciones.append(self._circuito(manifiesto))
        comprobaciones.append(self._ruta_upgrade(manifiesto))
        comprobaciones.append(self._variables(manifiesto))
        comprobaciones.append(self._espacio())
        comprobaciones.append(self._estado_actual(manifiesto))

        comp_compose, preparado = self._preparar_compose(manifiesto)
        comprobaciones.append(comp_compose)

        if descargar and comp_compose.ok and not any(
                c for c in comprobaciones if not c.ok and c.bloqueante):
            comprobaciones.append(self._descarga(manifiesto, preparado))

        return Informe(comprobaciones), preparado
