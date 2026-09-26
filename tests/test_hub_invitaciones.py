# ruff: noqa: F811  (los fixtures de test_hub_api se importan y se usan como parámetros)
"""Alta con link: el hub crea a la persona en Supabase Auth, le da el rol y
devuelve el link para que elija su contraseña, sin depender del mail. Supabase
es un doble en memoria de la API de administración."""

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient  # noqa: E402
from test_hub_api import EMISOR, SECRETO, U, como, personas  # noqa: E402,F401

from deploycenter.hub.api import crear_app  # noqa: E402
from deploycenter.hub.identidad import ValidadorJWT  # noqa: E402
from deploycenter.hub.proveedor import ProveedorSupabase  # noqa: E402

URL = "https://proyecto.supabase.co"


class SupabaseFalso:
    """generate_link crea el usuario en una invitación y rechaza invitar a un email
    que ya existe, como la real; la recuperación exige que exista."""

    def __init__(self):
        self.usuarios = {}   # email → id
        self.pedidos = []

    def __call__(self, metodo, url, cuerpo, cabeceras, timeout):
        self.pedidos.append((metodo, url, cuerpo))
        assert cabeceras["Authorization"] == "Bearer sb_secret_prueba"
        if metodo == "DELETE":
            self.usuarios = {e: u for e, u in self.usuarios.items() if not url.endswith(u)}
            return 200, {}
        assert url == f"{URL}/auth/v1/admin/generate_link"
        email, tipo = cuerpo["email"], cuerpo["type"]
        if tipo == "invite":
            if email in self.usuarios:
                return 422, {"code": 422, "error_code": "email_exists",
                             "msg": "A user with this email address has already been registered"}
            self.usuarios[email] = str(uuid.uuid4())
        elif email not in self.usuarios:
            return 404, {"msg": "User not found"}
        return 200, {"id": self.usuarios[email], "email": email,
                     "action_link": f"{URL}/auth/v1/verify?token=t-{tipo}&type={tipo}"
                                    f"&redirect_to={cuerpo.get('redirect_to')}"}


@pytest.fixture
def supabase():
    return SupabaseFalso()


def _cliente(hub, supabase, tmp_path, redirigir_a=None):
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "web" / "index.html").write_text("<!doctype html>")
    hub.proveedor = ProveedorSupabase(URL, "sb_secret_prueba", redirigir_a=redirigir_a,
                                      transporte=supabase)
    validador = ValidadorJWT(secreto=SECRETO, emisor=EMISOR)
    return TestClient(crear_app(hub, validador=validador, web=tmp_path / "web",
                                config_web={"identidad": "token"}))


@pytest.fixture
def cliente(hub, personas, supabase, tmp_path):
    return _cliente(hub, supabase, tmp_path, redirigir_a="https://deploy.accusys.com.ar")


def alta(cliente, quien, **cuerpo):
    cuerpo = {"email": "nueva@banco.example", "rol": "operador", "tenant": "andino", **cuerpo}
    return cliente.post("/api/v1/usuarios", headers=como(quien), json=cuerpo)


class TestAltaConLink:
    def test_el_aprobador_da_de_alta_y_recibe_el_link(self, cliente, supabase):
        r = alta(cliente, "aprobador", nombre="Nueva Persona")
        assert r.status_code == 201, r.text
        datos = r.json()
        assert datos["nueva"] is True and datos["rol"] == "operador"
        assert datos["usuario_id"] == supabase.usuarios["nueva@banco.example"]
        assert "type=invite" in datos["link"]
        # vuelve a la URL pública configurada (DC_URL_WEB)
        assert supabase.pedidos[0][2]["redirect_to"] == "https://deploy.accusys.com.ar/"

    def test_sin_url_configurada_vuelve_a_la_del_pedido(self, hub, personas, supabase,
                                                         tmp_path):
        c = _cliente(hub, supabase, tmp_path)
        assert alta(c, "aprobador").status_code == 201
        assert supabase.pedidos[0][2]["redirect_to"] == "http://testserver/"

    def test_quien_ya_tenia_cuenta_recibe_el_rol_y_un_link_de_recuperacion(self, cliente,
                                                                          supabase):
        supabase.usuarios["ya@banco.example"] = uid = str(uuid.uuid4())
        datos = alta(cliente, "comercial", email="Ya@Banco.example").json()
        assert datos["nueva"] is False and "type=recovery" in datos["link"]
        assert datos["usuario_id"] == uid

    def test_si_falla_la_base_una_cuenta_que_ya_existia_no_se_borra(self, cliente, supabase,
                                                                     monkeypatch):
        from deploycenter.hub import servicio as srv

        def rompe(*_a, **_k):
            raise srv.Conflicto("la base dijo que no")

        supabase.usuarios["ya@banco.example"] = str(uuid.uuid4())
        monkeypatch.setattr(srv.Hub, "_guardar_rol", rompe)
        assert alta(cliente, "comercial", email="ya@banco.example").status_code == 409
        assert alta(cliente, "comercial", email="otra@banco.example").status_code == 409
        # la que ya existía sigue; la nueva se borró
        assert set(supabase.usuarios) == {"ya@banco.example"}

    def test_alguien_de_otro_cliente_no_se_reasigna(self, cliente, supabase):
        uid = alta(cliente, "comercial", email="x@otro.example", tenant="otro").json()["usuario_id"]
        # el hub no tiene su email (por ejemplo, alta por id): el proveedor sí
        cliente.put(f"/api/v1/usuarios/{uid}", headers=como("comercial"),
                    json={"rol": "operador", "tenant": "otro", "email": "y@otro.example"})
        supabase.usuarios["y@otro.example"] = uid
        r = alta(cliente, "aprobador", email="y@otro.example")
        assert r.status_code == 409 and "link" not in r.json()

    @pytest.mark.parametrize("quien,cuerpo,codigo", [
        ("operador", {}, 403),                                   # no administra usuarios
        ("aprobador", {"tenant": "otro"}, 403),                  # otra organización
        ("comercial", {"rol": "soporte", "tenant": None}, 403),  # Accusys: solo consola
        ("aprobador", {"rol": "jefe"}, 422),                     # rol desconocido
        ("aprobador", {"email": "no-es-un-email"}, 422),
        ("comercial", {"tenant": "no-existe"}, 404),
    ])
    def test_lo_que_no_se_puede_no_crea_a_nadie(self, cliente, supabase, quien, cuerpo,
                                                codigo):
        assert alta(cliente, quien, **cuerpo).status_code == codigo
        assert supabase.usuarios == {}


class TestLinkDeAcceso:
    def test_el_aprobador_pide_un_link_para_alguien_de_su_organizacion(self, cliente):
        uid = alta(cliente, "aprobador").json()["usuario_id"]
        r = cliente.post(f"/api/v1/usuarios/{uid}/acceso", headers=como("aprobador"))
        assert r.status_code == 200, r.text
        assert "type=recovery" in r.json()["link"]
        acciones = [a["accion"] for a in cliente.get("/api/v1/auditoria",
                                                      headers=como("aprobador")).json()]
        assert "link_de_acceso" in acciones

    @pytest.mark.parametrize("quien,codigo", [
        ("operador_otro", 404),   # otra organización: ni sabe que existe
        ("operador", 403),        # de la organización, pero no administra usuarios
        ("soporte", 403),         # Accusys, pero no administra usuarios
    ])
    def test_quien_no_puede(self, cliente, quien, codigo):
        uid = alta(cliente, "aprobador").json()["usuario_id"]
        assert cliente.post(f"/api/v1/usuarios/{uid}/acceso",
                            headers=como(quien)).status_code == codigo

    def test_el_propio_no(self, cliente):
        r = cliente.post(f"/api/v1/usuarios/{U['aprobador']}/acceso", headers=como("aprobador"))
        assert r.status_code == 403

    def test_sin_email_no_hay_link(self, cliente):
        r = cliente.post(f"/api/v1/usuarios/{U['lector']}/acceso", headers=como("aprobador"))
        assert r.status_code == 422 and "email" in r.json()["detalle"]

    def test_si_el_email_es_de_otra_cuenta_no_se_entrega(self, cliente, supabase):
        uid = alta(cliente, "aprobador").json()["usuario_id"]
        supabase.usuarios["nueva@banco.example"] = str(uuid.uuid4())   # otra cuenta
        r = cliente.post(f"/api/v1/usuarios/{uid}/acceso", headers=como("aprobador"))
        assert r.status_code == 409 and "link" not in r.json()

    def test_sin_proveedor(self, cliente, hub):
        uid = alta(cliente, "aprobador").json()["usuario_id"]
        hub.proveedor = None
        r = cliente.post(f"/api/v1/usuarios/{uid}/acceso", headers=como("aprobador"))
        assert r.status_code == 503


class TestConfig:
    def test_la_web_sabe_que_alta_ofrecer(self, cliente, hub):
        assert cliente.get("/config.json").json()["proveedor"] == "supabase"
        hub.proveedor = None
        assert cliente.get("/config.json").json()["proveedor"] is None
