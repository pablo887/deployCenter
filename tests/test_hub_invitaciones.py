# ruff: noqa: F811  (los fixtures de test_hub_api se importan y se usan como parámetros)
"""Invitaciones: el hub crea a la persona en Supabase Auth, le da el rol y
devuelve el link para que elija su contraseña. Supabase es un doble en memoria."""

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient  # noqa: E402
from test_hub_api import EMISOR, SECRETO, U, como, personas  # noqa: E402,F401

from deploycenter.errores import ErrorDeployCenter  # noqa: E402
from deploycenter.hub.api import crear_app  # noqa: E402
from deploycenter.hub.identidad import ValidadorJWT  # noqa: E402
from deploycenter.hub.invitaciones import AdminSupabase, ErrorIdentidad  # noqa: E402

URL = "https://proyecto.supabase.co"


class SupabaseFalso:
    """La API de administración: generate_link crea el usuario en una invitación y
    rechaza invitar a un email que ya existe, como la real."""

    def __init__(self):
        self.usuarios = {}   # email → id
        self.pedidos = []

    def __call__(self, metodo, url, cuerpo, cabeceras, timeout):
        self.pedidos.append((metodo, url, cuerpo, cabeceras))
        assert cabeceras["Authorization"] == "Bearer sb_secret_prueba"
        if not url.endswith("/auth/v1/admin/generate_link"):
            return 404, {"msg": "no existe"}
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
                                    f"&redirect_to={cuerpo['redirect_to']}"}


@pytest.fixture
def supabase():
    return SupabaseFalso()


@pytest.fixture
def admin(supabase):
    return AdminSupabase(URL, "sb_secret_prueba", transporte=supabase)


@pytest.fixture
def cliente(hub, personas, admin, tmp_path):  # noqa: F811
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<!doctype html>")
    validador = ValidadorJWT(secreto=SECRETO, emisor=EMISOR)
    return TestClient(crear_app(hub, validador=validador, admin=admin, web=tmp_path / "web",
                                config_web={"identidad": "token"},
                                url_publica="https://deploy.accusys.com.ar"))


def invitar(cliente, quien, **cuerpo):
    cuerpo.setdefault("email", "nueva@banco.example")
    cuerpo.setdefault("rol", "operador")
    cuerpo.setdefault("tenant", "andino")
    return cliente.post("/api/v1/invitaciones", headers=como(quien), json=cuerpo)


class TestInvitar:
    def test_el_aprobador_invita_a_su_organizacion(self, cliente, supabase):
        r = invitar(cliente, "aprobador", nombre="Nueva Persona")
        assert r.status_code == 201, r.text
        datos = r.json()
        assert datos["nueva"] is True and datos["rol"] == "operador"
        assert datos["usuario_id"] == supabase.usuarios["nueva@banco.example"]
        assert "type=invite" in datos["link"]
        # vuelve al hub, a la URL pública configurada
        assert supabase.pedidos[0][2]["redirect_to"] == "https://deploy.accusys.com.ar/"
        usuarios = cliente.get("/api/v1/usuarios", headers=como("aprobador")).json()
        assert {"nueva@banco.example"} <= {u["email"] for u in usuarios}

    def test_quien_ya_existe_recibe_un_link_de_recuperacion(self, cliente, supabase):
        supabase.usuarios["ya@banco.example"] = str(uuid.uuid4())
        datos = invitar(cliente, "comercial", email="Ya@Banco.example").json()
        assert datos["nueva"] is False and "type=recovery" in datos["link"]
        assert datos["usuario_id"] == supabase.usuarios["ya@banco.example"]

    def test_queda_en_la_auditoria(self, cliente):
        invitar(cliente, "aprobador")
        acciones = [a["accion"] for a in cliente.get("/api/v1/auditoria",
                                                      headers=como("aprobador")).json()]
        assert "invitacion" in acciones and "usuario_rol" in acciones

    @pytest.mark.parametrize("quien,cuerpo,codigo", [
        ("operador", {}, 403),                               # no administra usuarios
        ("aprobador", {"tenant": "otro"}, 403),              # otra organización
        ("comercial", {"rol": "soporte", "tenant": None}, 403),  # Accusys: solo consola
        ("aprobador", {"rol": "jefe"}, 422),                 # rol desconocido
        ("aprobador", {"email": "no-es-un-email"}, 422),
    ])
    def test_lo_que_no_se_puede_no_crea_a_nadie(self, cliente, supabase, quien, cuerpo, codigo):
        assert invitar(cliente, quien, **cuerpo).status_code == codigo
        assert supabase.usuarios == {}

    def test_cliente_inexistente_no_crea_a_nadie(self, cliente, supabase):
        assert invitar(cliente, "comercial", tenant="no-existe").status_code == 404
        assert supabase.usuarios == {}

    def test_sin_secret_key_la_invitacion_explica(self, hub, personas):  # noqa: F811
        c = TestClient(crear_app(hub, validador=ValidadorJWT(secreto=SECRETO, emisor=EMISOR)))
        r = invitar(c, "aprobador")
        assert r.status_code == 422 and "SUPABASE_SECRET_KEY" in r.json()["detalle"]


class TestLinkDeAcceso:
    def test_el_aprobador_pide_un_link_para_alguien_de_su_organizacion(self, cliente, supabase):
        datos = invitar(cliente, "aprobador").json()
        r = cliente.post(f"/api/v1/usuarios/{datos['usuario_id']}/acceso",
                         headers=como("aprobador"))
        assert r.status_code == 200 and "type=recovery" in r.json()["link"]

    def test_no_para_otra_organizacion(self, cliente):
        datos = invitar(cliente, "aprobador").json()
        r = cliente.post(f"/api/v1/usuarios/{datos['usuario_id']}/acceso",
                         headers=como("operador_otro"))
        assert r.status_code == 404

    def test_sin_email_no_hay_link(self, cliente):
        r = cliente.post(f"/api/v1/usuarios/{U['lector']}/acceso", headers=como("aprobador"))
        assert r.status_code == 422 and "email" in r.json()["detalle"]


class TestConfig:
    def test_la_web_sabe_si_hay_invitaciones(self, cliente):
        assert cliente.get("/config.json").json()["invitaciones"] is True


class TestAdminSupabase:
    def test_desde_entorno_sin_secret_key_no_hay_invitaciones(self):
        assert AdminSupabase.desde_entorno({"SUPABASE_URL": URL}) is None

    def test_el_usuario_puede_venir_anidado(self):
        def transporte(metodo, url, cuerpo, cabeceras, timeout):
            return 200, {"user": {"id": "u-1"}, "properties": {"action_link": "https://l"}}
        a = AdminSupabase(URL, "k", transporte=transporte)
        assert a.invitar("a@b.co", "https://hub/") == ("u-1", "https://l", True)

    def test_un_error_del_proveedor_llega_con_su_mensaje(self):
        a = AdminSupabase(URL, "k", transporte=lambda *a: (429, {"msg": "rate limit"}))
        with pytest.raises(ErrorIdentidad, match="429 rate limit"):
            a.invitar("a@b.co", "https://hub/")

    def test_email_invalido(self, admin):
        with pytest.raises(ErrorDeployCenter, match="email válido"):
            admin.invitar("sin-arroba", "https://hub/")
