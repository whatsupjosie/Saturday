from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

import main


def _route_match(routes: set[str], ref: str) -> bool:
    if ref in routes:
        return True
    for route in routes:
        pattern = '^' + re.sub(r'\{[^/]+\}', '[^/]+', route) + '$'
        if re.match(pattern, ref):
            return True
    return False


def test_no_broken_static_api_refs_after_startup():
    static = Path('static')
    patterns = [
        re.compile(r'(["\'\(])(/api/[A-Za-z0-9_./?=&:-]+)'),
        re.compile(r'(["\'\(])(/ws/[A-Za-z0-9_./?=&:-]+)'),
    ]
    refs: set[str] = set()
    for path in static.rglob('*'):
        if path.suffix.lower() not in {'.html', '.js', '.css', '.json'}:
            continue
        text = path.read_text('utf-8', errors='ignore')
        for pattern in patterns:
            for match in pattern.finditer(text):
                refs.add(match.group(2).split('?')[0])

    with TestClient(main.app):
        routes = {getattr(r, 'path', None) for r in main.app.routes if getattr(r, 'path', None)}

    missing = sorted(ref for ref in refs if not _route_match(routes, ref))
    assert missing == []


def test_byok_root_info_endpoint_present():
    with TestClient(main.app) as client:
        response = client.get('/api/byok')
        assert response.status_code == 200
        payload = response.json()
        assert payload['ok'] is True
        assert payload['catalog'] == '/api/byok/catalog'
        assert payload['models'] == '/api/byok/models'


def test_pubworld_props_endpoint_returns_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    with TestClient(main.app) as client:
        response = client.get('/api/pubworld/props')
        assert response.status_code == 200
        assert response.json() == {'props': []}


def test_dressing_room_security_compat_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    with TestClient(main.app) as client:
        status = client.get(
            '/api/dressing-room/security/status',
            params={'project_id': 'proj', 'session_id': 'sess'},
            headers={'X-Client-Id': 'josie'},
        )
        assert status.status_code == 200
        assert status.json()['ok'] is True
        assert status.json()['lock_enabled'] is False

        set_code = client.post(
            '/api/dressing-room/security/code',
            headers={'X-Client-Id': 'josie'},
            json={'action': 'set', 'code': '#123'},
        )
        assert set_code.status_code == 200
        assert set_code.json()['lock_enabled'] is True

        enter = client.post(
            '/api/dressing-room/security/enter',
            headers={'X-Client-Id': 'guest'},
            json={
                'room_owner_id': 'josie',
                'acting_identity': 'guest',
                'project_id': 'proj',
                'session_id': 'sess',
                'credential_type': 'personal',
                'valid_routing': True,
                'code': '#123',
                'accessed_files': ['avatar_profile'],
            },
        )
        assert enter.status_code == 200
        assert enter.json()['ok'] is True
        assert enter.json()['status'] == 'unlocked'
