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


def respuesta_link(uid, tipo="invite", anidado=False):
    link = f"{URL}/auth/v1/verify?token=t&type={tipo}"
    if anidado:
        return 200, {"user": {"id": uid}, "properties": {"action_link": link}}
    return 200, {"id": uid, "email": "ana@andino.example", "action_link": link}


class TestSupabase:
    def test_crea_la_cuenta_y_devuelve_el_id_y_el_link(self):
        nuevo = str(uuid.uuid4())
        t = TransporteFalso(respuesta_link(nuevo))
        p = pv.ProveedorSupabase(URL + "/", "sb_secret_x", redirigir_a="https://dc.example",
                                 transporte=t)
        alta = p.invitar("ana@andino.example", "Ana Paz")
        assert alta == pv.Alta(nuevo, f"{URL}/auth/v1/verify?token=t&type=invite", True)
        pedido = t.pedidos[0]
        assert pedido["metodo"] == "POST"
        # generate_link no manda mail: el link vuelve en la respuesta
        assert pedido["url"] == f"{URL}/auth/v1/admin/generate_link"
        assert pedido["cuerpo"] == {"type": "invite", "email": "ana@andino.example",
                                    "data": {"nombre": "Ana Paz"},
                                    "redirect_to": "https://dc.example"}
        assert pedido["cabeceras"]["apikey"] == "sb_secret_x"
        assert pedido["cabeceras"]["Authorization"] == "Bearer sb_secret_x"

    def test_la_vuelta_del_pedido_gana_a_la_configurada(self):
        t = TransporteFalso(respuesta_link(str(uuid.uuid4())))
        p = pv.ProveedorSupabase(URL, "k", redirigir_a="https://dc.example", transporte=t)
        p.invitar("ana@andino.example", redirigir_a="https://otro.example/")
        assert t.pedidos[0]["cuerpo"]["redirect_to"] == "https://otro.example/"

    def test_el_usuario_puede_venir_anidado(self):
        uid = str(uuid.uuid4())
        p = pv.ProveedorSupabase(URL, "k", transporte=TransporteFalso(
            respuesta_link(uid, anidado=True)))
        assert p.invitar("ana@andino.example").usuario_id == uid

    @pytest.mark.parametrize("rechazo", [
        (422, {"code": 422, "error_code": "email_exists", "msg": "Email address already exists"}),
        (422, {"msg": "A user with this email address has already been registered"}),
    ])
    def test_si_ya_tenia_cuenta_el_link_es_de_recuperacion(self, rechazo):
        uid = str(uuid.uuid4())
        t = TransporteFalso(rechazo, respuesta_link(uid, "recovery"))
        alta = pv.ProveedorSupabase(URL, "k", transporte=t).invitar("ana@andino.example")
        assert alta.usuario_id == uid and alta.nueva is False
        assert "type=recovery" in alta.link
        assert t.pedidos[1]["cuerpo"] == {"type": "recovery", "email": "ana@andino.example"}

    def test_link_de_acceso(self):
        uid = str(uuid.uuid4())
        t = TransporteFalso(respuesta_link(uid, "recovery"))
        p = pv.ProveedorSupabase(URL, "k", transporte=t)
        assert p.link_de_acceso("ana@andino.example", "https://dc.example/") == (
            uid, f"{URL}/auth/v1/verify?token=t&type=recovery")
        assert t.pedidos[0]["cuerpo"]["redirect_to"] == "https://dc.example/"

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

    @pytest.mark.parametrize("datos,falta", [
        ({}, "id"),
        ({"id": str(uuid.uuid4())}, "link"),
    ])
    def test_respuesta_incompleta(self, datos, falta):
        p = pv.ProveedorSupabase(URL, "k", transporte=TransporteFalso((200, datos)))
        with pytest.raises(pv.ErrorProveedor, match=f"no devolvió el {falta}"):
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


def test_local_no_repite_emails_ni_da_links():
    p = pv.ProveedorLocal()
    alta = p.invitar("ana@andino.example")
    assert uuid.UUID(alta.usuario_id) and alta.link is None and alta.nueva
    assert p.invitar("ana@andino.example") == pv.Alta(alta.usuario_id, None, False)
    assert p.link_de_acceso("ana@andino.example") == (alta.usuario_id, None)
    p.borrar(alta.usuario_id)
    assert p.invitar("ana@andino.example").nueva
