import yaml
import pytest
from fastapi.testclient import TestClient

from app import paths
from app.main import app
from app.personas import FOR_INSTRUCT_NAME, PersonaStore


def make_persona(name="Jean", **overrides):
    persona = {
        "name": name,
        "description": f"{name} desc",
        "system_prompt": f"You are {name}.",
        "router_hints": [],
        "avatar_color": "#87CEEB",
        "avatar_image": None,
        "reference_audio": None,
        "reference_audio_transcript": None,
        "reference_audio_language": None,
    }
    persona.update(overrides)
    return persona


@pytest.fixture
def store(tmp_path):
    s = PersonaStore(personas_yaml=tmp_path / "personas.yaml")
    s.create(make_persona("Jean"))
    s.create(make_persona("Zhongli"))
    s.create(make_persona("Barbara"))
    return s


def names(layout):
    out = []
    for e in layout:
        if e["type"] == "persona":
            out.append(e["name"])
        else:
            out.extend(e["personas"])
    return out


def write_layout(store, layout):
    # escribe a mano la tecla layout (para probar la normalizacion al leer)
    data = {"personas": store.list(), "layout": layout}
    store.yaml_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return PersonaStore(personas_yaml=store.yaml_path)


# ── store ────────────────────────────────────────────────────────────────

def test_get_layout_tecla_ausente(store):
    # sin tecla layout: todas sueltas al tope, en orden de store
    assert store.get_layout() == [
        {"type": "persona", "name": "Jean"},
        {"type": "persona", "name": "Zhongli"},
        {"type": "persona", "name": "Barbara"},
    ]


def test_save_layout_roundtrip(store):
    layout = [
        {"type": "persona", "name": "Barbara"},
        {"type": "folder", "name": "Genshin", "personas": ["Jean", "Zhongli"]},
    ]
    assert store.save_layout(layout) == layout
    reloaded = PersonaStore(personas_yaml=store.yaml_path)
    assert reloaded.get_layout() == layout


def test_layout_columns_roundtrip(store):
    assert store.get_layout_columns() == 2
    store.save_layout(store.get_layout(), columns=3)
    assert store.get_layout_columns() == 3
    reloaded = PersonaStore(personas_yaml=store.yaml_path)
    assert reloaded.get_layout_columns() == 3
    # sin columns: no cambia el valor guardado
    store2 = PersonaStore(personas_yaml=store.yaml_path)
    store2.save_layout(store2.get_layout())
    assert PersonaStore(personas_yaml=store.yaml_path).get_layout_columns() == 3


@pytest.mark.parametrize("bad", [0, 5, -1, "2", True])
def test_layout_columns_invalido(store, bad):
    with pytest.raises(ValueError):
        store.save_layout(store.get_layout(), columns=bad)


def test_for_instruct_fuera_del_layout(store):
    store.create(make_persona(FOR_INSTRUCT_NAME))
    saved = store.save_layout([
        {"type": "persona", "name": FOR_INSTRUCT_NAME},
        {"type": "persona", "name": "Jean"},
    ])
    assert saved == [
        {"type": "persona", "name": "Jean"},
        {"type": "persona", "name": "Zhongli"},
        {"type": "persona", "name": "Barbara"},
    ]


def test_normalizacion_descarta_desconocidos(store):
    saved = store.save_layout([
        {"type": "persona", "name": "Fantasma"},
        {"type": "folder", "name": "Vacia", "personas": ["Nadie"]},
        {"type": "folder", "name": "Genshin", "personas": ["Jean", "Borrada"]},
    ])
    assert saved == [
        {"type": "folder", "name": "Vacia", "personas": []},
        {"type": "folder", "name": "Genshin", "personas": ["Jean"]},
        {"type": "persona", "name": "Zhongli"},
        {"type": "persona", "name": "Barbara"},
    ]


# los duplicados se RECHAZAN al escribir (400/ValueError) y se normalizan
# (gana la primera) al LEER: se prueban con la tecla layout escrita a mano
def test_normalizacion_read_persona_duplicada_gana_la_primera(store):
    reloaded = write_layout(store, [
        {"type": "persona", "name": "Jean"},
        {"type": "folder", "name": "A", "personas": ["Jean"]},
    ])
    assert reloaded.get_layout() == [
        {"type": "persona", "name": "Jean"},
        {"type": "folder", "name": "A", "personas": []},
        {"type": "persona", "name": "Zhongli"},
        {"type": "persona", "name": "Barbara"},
    ]


def test_normalizacion_read_carpeta_duplicada_gana_la_primera(store):
    reloaded = write_layout(store, [
        {"type": "folder", "name": "A", "personas": ["Jean"]},
        {"type": "folder", "name": "A", "personas": ["Zhongli"]},
    ])
    assert reloaded.get_layout() == [
        {"type": "folder", "name": "A", "personas": ["Jean"]},
        {"type": "persona", "name": "Zhongli"},
        {"type": "persona", "name": "Barbara"},
    ]


def test_normalizacion_read_duplicado_dentro_de_carpeta(store):
    reloaded = write_layout(store, [
        {"type": "folder", "name": "A", "personas": ["Jean", "Jean", "Zhongli"]},
    ])
    assert reloaded.get_layout() == [
        {"type": "folder", "name": "A", "personas": ["Jean", "Zhongli"]},
        {"type": "persona", "name": "Barbara"},
    ]


def test_borrar_persona_limpia_el_layout(store):
    store.save_layout([{"type": "folder", "name": "A", "personas": ["Jean", "Zhongli"]}])
    store.delete("Jean")
    assert store.get_layout() == [
        {"type": "folder", "name": "A", "personas": ["Zhongli"]},
        {"type": "persona", "name": "Barbara"},
    ]


def test_create_no_toca_layout(store):
    store.save_layout([
        {"type": "persona", "name": "Jean"},
        {"type": "folder", "name": "A", "personas": ["Zhongli"]},
    ])
    store.create(make_persona("Lisa"))
    # la nueva persona aparece al final por normalizacion, sin tocar lo guardado
    assert names(store.get_layout()) == ["Jean", "Zhongli", "Barbara", "Lisa"]


@pytest.mark.parametrize(
    "bad",
    [
        "no-lista",
        [{"type": "alien", "name": "Jean"}],
        [{"type": "persona"}],
        [{"type": "persona", "name": ""}],
        [{"type": "persona", "name": "Jean"}, {"type": "persona", "name": "Jean"}],
        [{"type": "folder", "name": "A"}, {"type": "folder", "name": "A"}],
        [{"type": "persona", "name": "Jean"}, {"type": "folder", "name": "A", "personas": ["Jean"]}],
        [{"type": "folder", "name": "A", "personas": ["Jean", "Jean"]}],
        [{"type": "folder", "name": "A", "personas": "Jean"}],
    ],
)
def test_save_layout_invalido(store, bad):
    with pytest.raises(ValueError):
        store.save_layout(bad)


# ── router ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    monkeypatch.setattr(paths, "PERSONAS_AVATARS_DIR", tmp_path / "personas_avatars")
    with TestClient(app) as c:
        yield c


def test_get_personas_incluye_layout(client):
    body = client.get("/api/personas").json()
    # solo existe For Instruct (lifespan) y queda fuera del layout
    assert body["layout"] == []
    assert body["layout_columns"] == 2


def test_put_layout_roundtrip(client):
    client.post("/api/personas", json=make_persona("Jean"))
    client.post("/api/personas", json=make_persona("Zhongli"))
    layout = [
        {"type": "persona", "name": "Zhongli"},
        {"type": "folder", "name": "Genshin", "personas": ["Jean"]},
    ]
    r = client.put("/api/personas/layout", json={"layout": layout})
    assert r.status_code == 200
    assert r.json() == {"layout": layout, "layout_columns": 2}
    assert client.get("/api/personas").json()["layout"] == layout


def test_put_layout_columns(client):
    client.post("/api/personas", json=make_persona("Jean"))
    r = client.put("/api/personas/layout", json={"layout": [], "columns": 4})
    assert r.status_code == 200
    assert r.json()["layout_columns"] == 4
    assert client.get("/api/personas").json()["layout_columns"] == 4


def test_put_layout_columns_invalido_422(client):
    r = client.put("/api/personas/layout", json={"layout": [], "columns": 9})
    assert r.status_code == 422


def test_put_layout_no_sombra_de_put_personas_name(client):
    # si PUT /personas/{name} capturara "layout", esto daria 422
    r = client.put("/api/personas/layout", json={"layout": []})
    assert r.status_code == 200


def test_put_layout_normaliza(client):
    client.post("/api/personas", json=make_persona("Jean"))
    r = client.put("/api/personas/layout", json={"layout": [
        {"type": "persona", "name": "Fantasma"},
        {"type": "folder", "name": "A", "personas": ["Jean"]},
    ]})
    assert r.status_code == 200
    assert r.json()["layout"] == [{"type": "folder", "name": "A", "personas": ["Jean"]}]


@pytest.mark.parametrize(
    "bad",
    [
        "no-lista",
        [{"type": "alien", "name": "Jean"}],
        [{"type": "persona", "name": ""}],
        [{"type": "folder", "name": "A"}, {"type": "folder", "name": "A"}],
        [{"type": "persona", "name": "Jean"}, {"type": "folder", "name": "A", "personas": ["Jean"]}],
    ],
)
def test_put_layout_invalido_400(client, bad):
    r = client.put("/api/personas/layout", json={"layout": bad})
    assert r.status_code == 400


def test_layout_incluye_persona_recien_creada(client):
    client.post("/api/personas", json=make_persona("Jean"))
    body = client.get("/api/personas").json()
    assert body["layout"] == [{"type": "persona", "name": "Jean"}]
