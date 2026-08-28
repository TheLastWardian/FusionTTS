from app.persistence import new_message

from test_sessions import client


def _seed_room(client, room):
    store = client.app.state.app_state.get_room_store(room)
    store.append(new_message("assistant", "Jean", "hola"))
    return store


def test_room_file_wav_200(client):
    store = _seed_room(client, "r1")
    fn = store.save_wav(store.history[0]["uuid"], 0, b"RIFF-fake")
    r = client.get(f"/api/rooms/r1/file/{fn}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF-fake"


def test_room_file_image_200(client):
    store = _seed_room(client, "r1")
    rel = store.save_image(b"png", ".png")
    r = client.get(f"/api/rooms/r1/file/{rel}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == b"png"


def test_room_file_missing_404(client):
    _seed_room(client, "r1")
    r = client.get("/api/rooms/r1/file/nope.wav")
    assert r.status_code == 404


def test_room_file_encoded_traversal_400(client):
    _seed_room(client, "r1")
    r = client.get("/api/rooms/r1/file/..%2Fevil.wav")
    assert r.status_code == 400


def test_room_file_dotdot_flat_404(client):
    _seed_room(client, "r1")
    r = client.get("/api/rooms/r1/file/..")
    assert r.status_code == 404


def test_room_file_invalid_room_400(client):
    r = client.get("/api/rooms/bad!name/file/a.wav")
    assert r.status_code == 400


def test_room_file_invalid_room_slash_404(client):
    r = client.get("/api/rooms/..%2Fevil/file/a.wav")
    assert r.status_code == 404


def test_room_file_room_without_dir_404(client):
    r = client.get("/api/rooms/room-vacio/file/a.wav")
    assert r.status_code == 404
