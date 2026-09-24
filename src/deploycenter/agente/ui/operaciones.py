"""Operaciones en curso.

Un despliegue puede tardar minutos —el pull de una imagen de varios GB en un
enlace lento— así que no puede vivir dentro de una petición HTTP. Cada operación
corre en un hilo y la UI la sigue por polling.

Esto es lo que hace posible la parte que importa de la interfaz: cuando la
verificación falla, la operación pasa a la fase `cuenta_regresiva` con un
vencimiento concreto, y la pantalla puede mostrar el reloj y el botón de
cancelar. El operador ve lo mismo que está pasando, no un spinner.

El registro vive en memoria: si el agente se reinicia, las operaciones en curso
se pierden de la vista. El estado real no, porque ese está en disco, en la
instalación. La UI es una ventana, no la fuente de verdad.
"""

import threading
import time
import uuid

CORRIENDO = "corriendo"
TERMINADA = "terminada"
FALLIDA = "fallida"


class Operacion:
    def __init__(self, tipo, instalacion, paquete=None):
        self.id = uuid.uuid4().hex[:12]
        self.tipo = tipo
        self.instalacion = instalacion
        self.paquete = paquete
        self.iniciada = time.time()
        self.terminada_en = None
        self.estado = CORRIENDO
        self.fase = "arrancando"
        self.eventos = []
        self.resultado = None
        self.error = None
        self.cancelable_hasta = None
        self._cancelar = threading.Event()
        self._lock = threading.Lock()

    # -- cancelación -------------------------------------------------------- #

    def cancelar(self):
        self._cancelar.set()

    def cancelado(self):
        return self._cancelar.is_set()

    @property
    def cancelable(self):
        return (self.estado == CORRIENDO
                and self.cancelable_hasta is not None
                and time.time() < self.cancelable_hasta)

    @property
    def segundos_restantes(self):
        if self.cancelable_hasta is None:
            return None
        return max(0, int(round(self.cancelable_hasta - time.time())))

    # -- avance -------------------------------------------------------------- #

    def registrar(self, evento, datos=None):
        with self._lock:
            self.eventos.append({"ts": time.time(), "evento": evento,
                                 "datos": datos or {}})

    def cerrar(self, resultado=None, error=None):
        with self._lock:
            self.resultado = resultado
            self.error = error
            self.estado = FALLIDA if error else TERMINADA
            self.fase = "terminada"
            self.terminada_en = time.time()
            self.cancelable_hasta = None

    # -- serialización -------------------------------------------------------- #

    def a_dict(self):
        return {
            "id": self.id,
            "tipo": self.tipo,
            "instalacion": self.instalacion,
            "paquete": self.paquete,
            "estado": self.estado,
            "fase": self.fase,
            "cancelable": self.cancelable,
            "segundos_restantes": self.segundos_restantes,
            "segundos": round((self.terminada_en or time.time()) - self.iniciada, 1),
            "eventos": [{"evento": e["evento"], "datos": e["datos"]} for e in self.eventos],
            "resultado": self.resultado,
            "error": self.error,
        }


class Registro:
    """Las últimas N operaciones del proceso. En memoria, a propósito."""

    def __init__(self, maximo=50, lanzar_hilo=True):
        self._ops = {}
        self._orden = []
        self._maximo = maximo
        self._lock = threading.Lock()
        self._lanzar_hilo = lanzar_hilo

    def crear(self, tipo, instalacion, paquete=None):
        op = Operacion(tipo, instalacion, paquete)
        with self._lock:
            self._ops[op.id] = op
            self._orden.append(op.id)
            while len(self._orden) > self._maximo:
                viejo = self._orden.pop(0)
                self._ops.pop(viejo, None)
        return op

    def obtener(self, id_operacion):
        return self._ops.get(id_operacion)

    def activa_de(self, instalacion):
        for id_op in reversed(self._orden):
            op = self._ops.get(id_op)
            if op and op.instalacion == instalacion and op.estado == CORRIENDO:
                return op
        return None

    def ultimas_de(self, instalacion, limite=5):
        salida = []
        for id_op in reversed(self._orden):
            op = self._ops.get(id_op)
            if op and op.instalacion == instalacion:
                salida.append(op)
            if len(salida) >= limite:
                break
        return salida

    def ejecutar(self, operacion, funcion):
        """Corre `funcion(operacion)` en un hilo. En los tests corre en línea,
        que es lo que permite probar el flujo sin carreras."""
        def envoltura():
            try:
                operacion.cerrar(resultado=funcion(operacion))
            except Exception as e:  # noqa: BLE001 - se muestra en la UI
                operacion.cerrar(error=f"{type(e).__name__}: {e}")

        if not self._lanzar_hilo:
            envoltura()
            return operacion

        hilo = threading.Thread(target=envoltura, name=f"op-{operacion.id}", daemon=True)
        hilo.start()
        operacion.hilo = hilo
        return operacion
