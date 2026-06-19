from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests


@dataclass
class MatrixClient:
    homeserver: str
    as_token: str
    hs_token: str
    synapse_admin_token: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        self.homeserver = self.homeserver.rstrip("/")
        self.session = requests.Session()

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict | None = None,
        json: dict | None = None,
        data: bytes | None = None,
        headers: dict | None = None,
    ) -> requests.Response:
        url = f"{self.homeserver}{path}"
        req_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            req_headers.update(headers)
        response = self.session.request(
            method,
            url,
            params=params,
            json=json,
            data=data,
            headers=req_headers,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            err = requests.HTTPError(f"{response.status_code}: {response.text}", response=response)
            raise err
        return response

    def ensure_user(self, mxid: str, displayname: str) -> None:
        encoded = quote(mxid, safe="")
        try:
            self._request(
                "PUT",
                f"/_synapse/admin/v2/users/{encoded}",
                token=self.synapse_admin_token,
                json={
                    "displayname": displayname,
                    "deactivated": False,
                },
            )
        except requests.HTTPError as exc:
            response_text = exc.response.text if exc.response is not None else str(exc)
            if "only be used with local users" in response_text.lower():
                raise ValueError(
                    f"MXID no local para Synapse: {mxid}. "
                    "Usa --domain igual al server_name local de Synapse (no la URL del homeserver)."
                ) from exc
            raise

    def list_users(self, *, from_offset: int = 0, limit: int = 100, guests: bool = False) -> dict:
        response = self._request(
            "GET",
            "/_synapse/admin/v2/users",
            token=self.synapse_admin_token,
            params={
                "from": from_offset,
                "limit": limit,
                "guests": str(guests).lower(),
            },
        )
        return response.json() if response.text else {}

    def deactivate_user(self, mxid: str, *, erase: bool = True) -> None:
        encoded = quote(mxid, safe="")
        self._request(
            "POST",
            f"/_synapse/admin/v1/deactivate/{encoded}",
            token=self.synapse_admin_token,
            json={"erase": erase},
        )

    def create_room(
        self,
        room_name: str,
        creator_mxid: str,
        *,
        actor_mxid: str | None = None,
    ) -> str:
        acting_mxid = actor_mxid or creator_mxid
        resp = self._request(
            "POST",
            "/_matrix/client/v3/createRoom",
            token=self.as_token,
            params={"user_id": acting_mxid},
            json={
                "name": room_name,
                "preset": "private_chat",
                "power_level_content_override": {
                    "users": {
                        acting_mxid: 100,
                        creator_mxid: 100,
                    },
                    "users_default": 0,
                    "events_default": 0,
                    "state_default": 50,
                    "invite": 0,
                    "kick": 50,
                    "ban": 50,
                    "redact": 50,
                },
            },
        )
        return resp.json()["room_id"]

    def join_room(self, room_id: str, mxid: str) -> None:
        encoded = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{encoded}/join",
            token=self.as_token,
            params={"user_id": mxid},
            json={},
        )

    def invite_to_room(self, room_id: str, inviter_mxid: str, invitee_mxid: str) -> None:
        encoded = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{encoded}/invite",
            token=self.as_token,
            params={"user_id": inviter_mxid},
            json={"user_id": invitee_mxid},
        )

    def admin_join_room(self, room_id: str, mxid: str) -> None:
        encoded = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_synapse/admin/v1/join/{encoded}",
            token=self.synapse_admin_token,
            json={"user_id": mxid},
        )

    def make_room_admin(self, room_id: str, mxid: str) -> None:
        encoded = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_synapse/admin/v1/rooms/{encoded}/make_room_admin",
            token=self.synapse_admin_token,
            json={"user_id": mxid},
        )

    def admin_delete_room(self, room_id: str, *, block: bool = True, purge: bool = True) -> dict:
        encoded = quote(room_id, safe="")
        payload = {
            "block": block,
            "purge": purge,
            "force_purge": True,
        }
        attempts: list[tuple[str, str, dict | None]] = [
            ("POST", f"/_synapse/admin/v2/rooms/{encoded}/delete", payload),
            ("POST", f"/_synapse/admin/v1/rooms/{encoded}/delete", payload),
            ("DELETE", f"/_synapse/admin/v2/rooms/{encoded}", payload),
            ("DELETE", f"/_synapse/admin/v1/rooms/{encoded}", payload),
        ]
        last_exc: Exception | None = None
        for method, path, json_payload in attempts:
            try:
                response = self._request(
                    method,
                    path,
                    token=self.synapse_admin_token,
                    json=json_payload,
                )
                return response.json() if response.text else {}
            except requests.HTTPError as exc:
                last_exc = exc
                text = exc.response.text.lower() if exc.response is not None else ""
                # Keep trying on "unknown/unrecognized/404" endpoint incompatibilities.
                if (
                    exc.response is not None
                    and exc.response.status_code in (400, 404, 405)
                    and ("unrecognized" in text or "unknown" in text or "not found" in text)
                ):
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        return {}

    def admin_get_delete_status(self, delete_id: str) -> dict:
        encoded = quote(delete_id, safe="")
        attempts: list[str] = [
            f"/_synapse/admin/v2/rooms/delete_status/{encoded}",
            f"/_synapse/admin/v1/rooms/delete_status/{encoded}",
        ]
        last_exc: Exception | None = None
        for path in attempts:
            try:
                response = self._request(
                    "GET",
                    path,
                    token=self.synapse_admin_token,
                )
                return response.json() if response.text else {}
            except requests.HTTPError as exc:
                last_exc = exc
                text = exc.response.text.lower() if exc.response is not None else ""
                if (
                    exc.response is not None
                    and exc.response.status_code in (400, 404, 405)
                    and ("unrecognized" in text or "unknown" in text or "not found" in text)
                ):
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        return {}

    def admin_get_room(self, room_id: str) -> dict:
        encoded = quote(room_id, safe="")
        attempts = [
            f"/_synapse/admin/v2/rooms/{encoded}",
            f"/_synapse/admin/v1/rooms/{encoded}",
        ]
        last_exc: Exception | None = None
        for path in attempts:
            try:
                response = self._request(
                    "GET",
                    path,
                    token=self.synapse_admin_token,
                )
                return response.json() if response.text else {}
            except requests.HTTPError as exc:
                last_exc = exc
                if exc.response is not None and exc.response.status_code == 404:
                    return {}
                text = exc.response.text.lower() if exc.response is not None else ""
                if (
                    exc.response is not None
                    and exc.response.status_code in (400, 404, 405)
                    and ("unrecognized" in text or "unknown" in text or "not found" in text)
                ):
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        return {}

    def admin_shutdown_room(self, room_id: str) -> dict:
        encoded = quote(room_id, safe="")
        payload = {
            "message": "Room shutdown by migration cleanup tool",
            "block": True,
        }
        response = self._request(
            "POST",
            f"/_synapse/admin/v1/shutdown_room/{encoded}",
            token=self.synapse_admin_token,
            json=payload,
        )
        return response.json() if response.text else {}

    def upload_media(self, file_path: Path, mime_type: str, mxid: str) -> str:
        data = file_path.read_bytes()
        attempts: list[tuple[str, str, dict]] = [
            (
                "/_matrix/media/v3/upload",
                self.synapse_admin_token,
                {"filename": file_path.name},
            ),
            (
                "/_matrix/client/v1/media/upload",
                self.synapse_admin_token,
                {"filename": file_path.name},
            ),
            (
                "/_matrix/media/v3/upload",
                self.as_token,
                {"filename": file_path.name, "user_id": mxid},
            ),
            (
                "/_matrix/client/v1/media/upload",
                self.as_token,
                {"filename": file_path.name, "user_id": mxid},
            ),
        ]

        last_exc: Exception | None = None
        for path, token, params in attempts:
            try:
                resp = self._request(
                    "POST",
                    path,
                    token=token,
                    params=params,
                    data=data,
                    headers={"Content-Type": mime_type},
                )
                content_uri = resp.json()["content_uri"]
                self._verify_media_exists(content_uri)
                return content_uri
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Media upload failed without explicit exception")

    def _verify_media_exists(self, mxc_uri: str) -> None:
        if not mxc_uri.startswith("mxc://"):
            raise ValueError(f"Invalid MXC URI: {mxc_uri}")
        parts = mxc_uri[len("mxc://") :].split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid MXC URI format: {mxc_uri}")
        server_name, media_id = parts
        server_q = quote(server_name, safe="")
        media_q = quote(media_id, safe="")

        checks = [
            f"/_matrix/client/v1/media/download/{server_q}/{media_q}",
            f"/_matrix/media/v3/download/{server_q}/{media_q}",
        ]
        last_exc: Exception | None = None
        for path in checks:
            try:
                self._request(
                    "GET",
                    path,
                    token=self.synapse_admin_token,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc

    def send_message(
        self,
        room_id: str,
        txn_id: str,
        sender_mxid: str,
        timestamp_ms: int,
        content: dict,
    ) -> str:
        encoded = quote(room_id, safe="")
        resp = self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{encoded}/send/m.room.message/{txn_id}",
            token=self.as_token,
            params={"user_id": sender_mxid, "ts": str(timestamp_ms)},
            json=content,
        )
        return resp.json()["event_id"]

    def set_room_history_visibility(self, room_id: str, sender_mxid: str, visibility: str = "shared") -> None:
        encoded = quote(room_id, safe="")
        self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{encoded}/state/m.room.history_visibility",
            token=self.as_token,
            params={"user_id": sender_mxid},
            json={"history_visibility": visibility},
        )
