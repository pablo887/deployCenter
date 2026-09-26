"""Alta de cuentas en el proveedor de identidad (Supabase Auth, o local en desarrollo)."""

import uuid

import pytest

from deploycenter.hub import proveedor as pv


class TransporteFalso:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.pedidos = []

    def __call__(self, metodo, url, cuerpo, cabeceras, timeout):
        self.pedidos.append({"metodo": metodo, "url": url, "cuerpo": cuerpo,
                             "cabeceras": cabeceras})
        return self.respuestas.pop(0)


URL = "https://proyecto.supabase.co"


class TestSupabase:
    def test_invita_y_devuelve_el_id(self):
        nuevo = str(uuid.uuid4())
        t = TransporteFalso((200, {"id": nuevo, "email": "ana@andino.example"}))
        p = pv.ProveedorSupabase(URL + "/", "sb_secret_x", redirigir_a="https://dc.example",
                                 transporte=t)
        assert p.invitar("ana@andino.example", "Ana Paz") == nuevo
        pedido = t.pedidos[0]
        assert pedido["metodo"] == "POST"
        assert pedido["url"] == f"{URL}/auth/v1/invite"
        assert pedido["cuerpo"] == {"email": "ana@andino.example", "data": {"nombre": "Ana Paz"},
                                    "redirect_to": "https://dc.example"}
        assert pedido["cabeceras"]["apikey"] == "sb_secret_x"
        assert pedido["cabeceras"]["Authorization"] == "Bearer sb_secret_x"

    @pytest.mark.parametrize("respuesta", [
        (422, {"code": 422, "error_code": "email_exists", "msg": "Email address already exists"}),
        (422, {"msg": "A user with this email address has already been registered"}),
    ])
    def test_email_ya_registrado(self, respuesta):
        p = pv.ProveedorSupabase(URL, "k", transporte=TransporteFalso(respuesta))
        with pytest.raises(pv.EmailYaRegistrado):
            p.invitar("ana@andino.example")

    def test_otro_error_del_proveedor(self):
        p = pv.ProveedorSupabase(URL, "k", transporte=TransporteFalso(
            (429, {"msg": "email rate limit exceeded"})))
        with pytest.raises(pv.ErrorProveedor, match="429.*rate limit") as e:
            p.invitar("ana@andino.example")
        assert not e.value.es_del_pedido

    def test_el_dato_rechazado_es_del_pedido(self):
        p = pv.ProveedorSupabase(URL, "k", transporte=TransporteFalso(
            (400, {"code": 400, "error_code": "email_address_invalid",
                   "msg": 'Email address "a@b.example" is invalid'})))
        with pytest.raises(pv.ErrorProveedor) as e:
            p.invitar("a@b.example")
        assert e.value.es_del_pedido

    def test_sin_id_en_la_respuesta(self):
        p = pv.ProveedorSupabase(URL, "k", transporte=TransporteFalso((200, {})))
        with pytest.raises(pv.ErrorProveedor, match="no devolvió el id"):
            p.invitar("ana@andino.example")

    def test_borrar(self):
        t = TransporteFalso((200, {}), (404, {"msg": "User not found"}))
        p = pv.ProveedorSupabase(URL, "k", transporte=t)
        p.borrar("abc")
        p.borrar("abc")   # ya no estaba: no es un error
        assert t.pedidos[0]["metodo"] == "DELETE"
        assert t.pedidos[0]["url"] == f"{URL}/auth/v1/admin/users/abc"


class TestDesdeEntorno:
    def test_supabase(self):
        p = pv.desde_entorno({"SUPABASE_URL": URL, "SUPABASE_SECRET_KEY": "k"})
        assert isinstance(p, pv.ProveedorSupabase)

    def test_local_en_desarrollo(self):
        assert isinstance(pv.desde_entorno({"DC_JWT_SECRET": "x" * 32}), pv.ProveedorLocal)

    def test_sin_clave_secreta_no_hay_alta(self):
        assert pv.desde_entorno({"SUPABASE_URL": URL}) is None
        assert pv.desde_entorno({}) is None


def test_local_no_repite_emails():
    p = pv.ProveedorLocal()
    uid = p.invitar("ana@andino.example")
    assert uuid.UUID(uid)
    with pytest.raises(pv.EmailYaRegistrado):
        p.invitar("ana@andino.example")
    p.borrar(uid)
    p.invitar("ana@andino.example")
