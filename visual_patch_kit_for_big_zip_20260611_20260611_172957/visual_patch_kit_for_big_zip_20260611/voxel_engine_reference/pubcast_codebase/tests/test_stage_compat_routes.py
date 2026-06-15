from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.hub import Hub
from modules.stage_compat_routes import create_stage_compat_router


class FakeCameraSource:
    def __init__(self, source_id: str):
        self.source_id = source_id


class FakeCameras:
    def __init__(self):
        self.program = FakeCameraSource("cam_a")
        self.preview = FakeCameraSource("cam_b")

    def get_program_source(self):
        return self.program

    def get_preview_source(self):
        return self.preview

    def set_program_source(self, source_id: str):
        self.program = FakeCameraSource(source_id)
        return True

    def set_preview_source(self, source_id: str):
        self.preview = FakeCameraSource(source_id)
        return True


def make_client(*, hub=None, cameras=None):
    app = FastAPI()
    app.include_router(create_stage_compat_router(hub=hub, cameras=cameras))
    return TestClient(app)


def test_stage_compat_unavailable_routes_return_payload_not_404():
    client = make_client()

    assert client.get("/api/state/production").status_code == 200
    assert client.get("/api/state/production").json()["status"] == "unavailable"
    assert client.get("/api/switcher").status_code == 200
    assert client.get("/api/switcher").json()["status"] == "unavailable"


def test_user_state_is_privacy_thin_and_stable():
    client = make_client()

    response = client.get("/api/state/user", headers={"X-Client-Id": "director", "X-Display-Name": "Director"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["user"]["user_id"] == "director"
    assert payload["user"]["display_name"] == "Director"
    assert payload["user"]["roles"] == []


def test_production_state_round_trip(tmp_path: Path):
    hub = Hub(tmp_path)
    client = make_client(hub=hub)

    initial = client.get("/api/state/production")
    assert initial.status_code == 200
    assert initial.json()["status"] == "ok"

    updated = client.post("/api/state/production", json={"on_air": True, "camera": "cam_a"})
    assert updated.status_code == 200
    assert updated.json()["state"]["on_air"] is True
    assert updated.json()["state"]["camera"] == "cam_a"


def test_switcher_compat_delegates_to_camera_manager_shape():
    cameras = FakeCameras()
    client = make_client(cameras=cameras)

    state = client.get("/api/switcher")
    assert state.status_code == 200
    assert state.json()["switcher"]["program"] == "cam_a"
    assert state.json()["switcher"]["preview"] == "cam_b"

    changed = client.post("/api/switcher", json={"program": "cam_c", "preview": "cam_d"})
    assert changed.status_code == 200
    assert changed.json()["changed"] == {"program": "cam_c", "preview": "cam_d"}
    assert cameras.get_program_source().source_id == "cam_c"
    assert cameras.get_preview_source().source_id == "cam_d"
