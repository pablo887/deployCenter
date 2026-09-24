"""El despliegue de punta a punta, con vuelta atrás.

    lock → preflight → punto de retorno → aplicar → verificar
                                                      ├── ok
                                                      └── falla → aviso
                                                                → cuenta regresiva
                                                                → rollback

Dos decisiones que están acá y no en otro lado, porque son las que hacen que
esto sea usable de noche en un banco:

**El aviso sale antes del rollback, no después.** Cuando la verificación falla,
lo primero que ocurre es la notificación; recién después arranca la cuenta
regresiva. Un operador que sabe lo que está pasando puede cancelarla y quedarse
con el sistema roto para diagnosticarlo, que a veces es lo correcto.

**Si el rollback no es seguro, no se revierte.** Con `rollback_seguro: false` —el
caso de las migraciones de esquema— el agente corta, marca la instalación como
degradada y escala. Revertir la imagen contra una base ya migrada deja un estado
que no existía antes del despliegue y que es peor que el fallo original.
"""

import time
import uuid

from .. import variables as vars_
from . import instalacion as inst_mod
from . import verificacion as verif_mod
from .docker import ErrorDocker
from .preflight import Preflight

ESPERA_CANCELACION_S = 60

OK = "ok"
REVERTIDO = "revertido"
DEGRADADO = "degradado"
ABORTADO = "abortado"


class Resultado:
    def __init__(self, estado, despliegue_id, desde, hacia, detalle="",
                 preflight=None, verificacion=None):
        self.estado = estado
        self.id = despliegue_id
        self.desde = desde
        self.hacia = hacia
        self.detalle = detalle
        self.preflight = preflight
        self.verificacion = verificacion

    @property
    def ok(self):
        return self.estado == OK

    def resumen(self):
        return {
            "estado": self.estado,
            "id": self.id,
            "desde": self.desde,
            "hacia": self.hacia,
            "detalle": self.detalle,
            "preflight": self.preflight.resumen() if self.preflight else None,
            "verificacion": self.verificacion.resumen() if self.verificacion else None,
        }

    def __repr__(self):  # pragma: no cover
        return f"<Despliegue {self.desde}->{self.hacia}: {self.estado}>"


class Despliegue:
    def __init__(self, docker, instalacion, paquete,
                 espera_cancelacion_s=ESPERA_CANCELACION_S,
                 clave_publica=None, avisar=None,
                 dormir=time.sleep, reloj=time.monotonic, cliente_http=None):
        self.docker = docker
        self.instalacion = instalacion
        self.paquete = paquete
        self.espera_cancelacion_s = espera_cancelacion_s
        self.clave_publica = clave_publica
        self._avisar = avisar
        self.dormir = dormir
        self.reloj = reloj
        self.cliente_http = cliente_http

    # -- avisos ------------------------------------------------------------- #

    def avisar(self, evento, **datos):
        """En Fase 1 el aviso queda en el historial. En Fase 2 el hub lo convierte
        en mail al cliente y a la guardia de Accusys. La interfaz es la misma."""
        self.instalacion.registrar("aviso", evento, **datos)
        if self._avisar is not None:
            self._avisar(evento, datos)

    # -- pasos --------------------------------------------------------------- #

    def _aplicar(self, manifiesto, compose_preparado):
        """Deja el compose nuevo en su lugar y completa el .env. Todavía no levanta."""
        completables = vars_.completables(manifiesto, self.instalacion.entorno())
        if completables:
            vars_.agregar_al_env(
                self.instalacion.ruta_entorno,
                [(v["nombre"], v["default"]) for v in completables],
                comentario=f"agregadas por deployCenter al desplegar {manifiesto['release']}",
            )
        self.instalacion.ruta_compose.write_bytes(compose_preparado.read_bytes())

    def _revertir(self, manifiesto_previo):
        """Restaura el compose anterior y lo levanta. Devuelve (ok, detalle)."""
        try:
            self.instalacion.restaurar_punto_retorno()
            self.docker.up(self.instalacion.directorio)
        except (ErrorDocker, OSError) as e:
            return False, f"el rollback falló: {e}"

        if manifiesto_previo is None:
            return True, "compose anterior restaurado (sin manifiesto previo que verificar)"

        informe = verif_mod.verificar(
            self.docker, self.instalacion, manifiesto_previo,
            cliente_http=self.cliente_http, dormir=self.dormir, reloj=self.reloj)
        if informe.ok:
            return True, "se volvió a la versión anterior y verificó bien"
        return False, "se restauró el compose anterior pero no verifica: " + "; ".join(
            r.detalle for r in informe.fallas)

    def _cuenta_regresiva(self, cancelado):
        """Ventana para que un operador frene el rollback. Devuelve True si lo frenó."""
        restante = self.espera_cancelacion_s
        while restante > 0:
            if cancelado():
                return True
            paso = min(1, restante)
            self.dormir(paso)
            restante -= paso
        return cancelado()

    # -- orquestación -------------------------------------------------------- #

    def ejecutar(self, cancelado=None, descargar=True):
        cancelado = cancelado or (lambda: False)
        despliegue_id = uuid.uuid4().hex[:12]
        desde = self.instalacion.version_instalada()

        self.instalacion.preparar()
        self.instalacion.bloquear(dueno=f"despliegue:{despliegue_id}")
        try:
            return self._ejecutar_bloqueado(despliegue_id, desde, cancelado, descargar)
        finally:
            self.instalacion.desbloquear()

    def _ejecutar_bloqueado(self, despliegue_id, desde, cancelado, descargar):
        # 1. preflight
        preflight = Preflight(self.docker, self.instalacion, self.paquete,
                              clave_publica=self.clave_publica)
        informe, compose_preparado = preflight.correr(descargar=descargar)
        manifiesto = self.paquete.manifiesto()
        hacia = manifiesto.get("release")

        if not informe.ok:
            motivos = "; ".join(c.detalle for c in informe.bloqueos)
            self.instalacion.registrar("despliegue", ABORTADO, id=despliegue_id,
                                       desde=desde, hacia=hacia, detalle=motivos)
            return Resultado(ABORTADO, despliegue_id, desde, hacia,
                             detalle=f"el preflight no pasó: {motivos}", preflight=informe)

        # 2. punto de retorno, antes de tocar nada
        hay_stack_previo = self.instalacion.ruta_compose.is_file()
        manifiesto_previo = self.instalacion.manifiesto_aplicado()
        if hay_stack_previo:
            self.instalacion.tomar_punto_retorno(manifiesto_actual=manifiesto_previo)

        # 3. aplicar y levantar
        self.instalacion.guardar_estado(estado=inst_mod.ESTADO_DESPLEGANDO,
                                        despliegue=despliegue_id, objetivo=hacia)
        self.avisar("despliegue_iniciado", id=despliegue_id, desde=desde, hacia=hacia,
                    producto=manifiesto["producto"])
        try:
            self._aplicar(manifiesto, compose_preparado)
            self.docker.up(self.instalacion.directorio)
        except (ErrorDocker, OSError) as e:
            return self._fallo(despliegue_id, desde, hacia, manifiesto, manifiesto_previo,
                               informe, None, f"no se pudo levantar el stack: {e}",
                               hay_stack_previo, cancelado)

        # 4. verificar
        verificacion = verif_mod.verificar(
            self.docker, self.instalacion, manifiesto,
            cliente_http=self.cliente_http, dormir=self.dormir, reloj=self.reloj)

        if verificacion.ok:
            self.instalacion.guardar_manifiesto(manifiesto)
            self.instalacion.guardar_estado(
                estado=inst_mod.ESTADO_OK, version=hacia, producto=manifiesto["producto"],
                digests={s: r.split("@", 1)[-1] for s, r in manifiesto["imagenes"].items()},
                despliegue=despliegue_id, objetivo=None)
            self.instalacion.registrar("despliegue", OK, id=despliegue_id, desde=desde,
                                       hacia=hacia, verificacion=verificacion.resumen())
            self.avisar("despliegue_ok", id=despliegue_id, desde=desde, hacia=hacia,
                        producto=manifiesto["producto"])
            return Resultado(OK, despliegue_id, desde, hacia,
                             detalle=f"{hacia} desplegada y verificada",
                             preflight=informe, verificacion=verificacion)

        motivo = "; ".join(r.detalle for r in verificacion.fallas)
        return self._fallo(despliegue_id, desde, hacia, manifiesto, manifiesto_previo,
                           informe, verificacion, motivo, hay_stack_previo, cancelado)

    def _fallo(self, despliegue_id, desde, hacia, manifiesto, manifiesto_previo,
               preflight, verificacion, motivo, hay_stack_previo, cancelado):
        """El camino que importa: qué pasa cuando la versión nueva no levanta."""

        # El aviso sale primero, siempre, antes de cualquier acción correctiva.
        self.avisar("despliegue_falla", id=despliegue_id, desde=desde, hacia=hacia,
                    producto=manifiesto["producto"], motivo=motivo)

        puede_revertir = bool(manifiesto.get("rollback_seguro")) and hay_stack_previo \
            and self.instalacion.hay_punto_retorno()

        if not puede_revertir:
            razon = ("el release declara rollback_seguro en false"
                     if not manifiesto.get("rollback_seguro")
                     else "no hay punto de retorno")
            self.instalacion.guardar_estado(estado=inst_mod.ESTADO_DEGRADADO,
                                            despliegue=despliegue_id)
            self.instalacion.registrar("despliegue", DEGRADADO, id=despliegue_id,
                                       desde=desde, hacia=hacia, detalle=motivo,
                                       rollback="no", razon=razon)
            self.avisar("requiere_intervencion", id=despliegue_id, producto=manifiesto["producto"],
                        motivo=motivo, razon=razon)
            return Resultado(DEGRADADO, despliegue_id, desde, hacia,
                             detalle=f"{motivo}. No se revirtió: {razon}",
                             preflight=preflight, verificacion=verificacion)

        if self._cuenta_regresiva(cancelado):
            self.instalacion.guardar_estado(estado=inst_mod.ESTADO_DEGRADADO,
                                            despliegue=despliegue_id)
            self.instalacion.registrar("despliegue", DEGRADADO, id=despliegue_id,
                                       desde=desde, hacia=hacia, detalle=motivo,
                                       rollback="cancelado por el operador")
            self.avisar("rollback_cancelado", id=despliegue_id, producto=manifiesto["producto"])
            return Resultado(DEGRADADO, despliegue_id, desde, hacia,
                             detalle=f"{motivo}. El operador canceló la vuelta atrás.",
                             preflight=preflight, verificacion=verificacion)

        revirtio, detalle_rollback = self._revertir(manifiesto_previo)
        estado = REVERTIDO if revirtio else DEGRADADO
        self.instalacion.guardar_estado(
            estado=inst_mod.ESTADO_OK if revirtio else inst_mod.ESTADO_DEGRADADO,
            version=desde if revirtio else None,
            despliegue=despliegue_id, objetivo=None)
        self.instalacion.registrar("despliegue", estado, id=despliegue_id, desde=desde,
                                   hacia=hacia, detalle=motivo, rollback=detalle_rollback)
        self.avisar("rollback_ok" if revirtio else "rollback_falla",
                    id=despliegue_id, producto=manifiesto["producto"],
                    detalle=detalle_rollback)
        return Resultado(estado, despliegue_id, desde, hacia,
                         detalle=f"{motivo}. {detalle_rollback}",
                         preflight=preflight, verificacion=verificacion)


def rollback_manual(docker, instalacion, cliente_http=None, dormir=time.sleep,
                    reloj=time.monotonic):
    """Vuelta atrás a pedido, con el sistema funcionando.

    La regla de mantenimiento no se evalúa acá a propósito: el rollback nunca se
    bloquea porque a un cliente se le haya vencido el mantenimiento.
    """
    if not instalacion.hay_punto_retorno():
        return Resultado(ABORTADO, "manual", instalacion.version_instalada(), None,
                         detalle="no hay punto de retorno registrado")

    previo = instalacion.manifiesto_de_retorno()
    desde = instalacion.version_instalada()
    hacia = (previo or {}).get("release")

    instalacion.bloquear(dueno="rollback-manual")
    try:
        instalacion.restaurar_punto_retorno()
        docker.up(instalacion.directorio)
        if previo is not None:
            informe = verif_mod.verificar(docker, instalacion, previo,
                                          cliente_http=cliente_http,
                                          dormir=dormir, reloj=reloj)
            if not informe.ok:
                instalacion.guardar_estado(estado=inst_mod.ESTADO_DEGRADADO)
                detalle = "; ".join(r.detalle for r in informe.fallas)
                instalacion.registrar("rollback_manual", DEGRADADO, desde=desde,
                                      hacia=hacia, detalle=detalle)
                return Resultado(DEGRADADO, "manual", desde, hacia, detalle=detalle,
                                 verificacion=informe)
            instalacion.guardar_manifiesto(previo)

        instalacion.guardar_estado(estado=inst_mod.ESTADO_OK, version=hacia)
        instalacion.registrar("rollback_manual", OK, desde=desde, hacia=hacia)
        return Resultado(REVERTIDO, "manual", desde, hacia,
                         detalle=f"se volvió a {hacia}")
    except (ErrorDocker, OSError) as e:
        instalacion.guardar_estado(estado=inst_mod.ESTADO_DEGRADADO)
        instalacion.registrar("rollback_manual", DEGRADADO, desde=desde, hacia=hacia,
                              detalle=str(e))
        return Resultado(DEGRADADO, "manual", desde, hacia, detalle=str(e))
    finally:
        instalacion.desbloquear()
