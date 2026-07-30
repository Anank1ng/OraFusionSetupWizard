from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests
from requests.auth import HTTPBasicAuth


@dataclass
class OracleResponse:
    ok: bool
    status_code: int
    body: Any
    text: str
    url: str


class OracleFusionClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 60):
        if not base_url:
            raise ValueError("base_url wajib diisi")
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.timeout = timeout

    def _url(self, endpoint: str) -> str:
        return urljoin(self.base_url, endpoint.lstrip("/"))

    def _headers(self, upsert_mode: bool = False, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if upsert_mode:
            headers["Upsert-Mode"] = "true"
        if extra_headers:
            headers.update({k: v for k, v in extra_headers.items() if v})
        return headers

    def test_connection(self, endpoint: str) -> OracleResponse:
        return self.get(endpoint, params={"limit": 1, "onlyData": "true"})

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> OracleResponse:
        url = self._url(endpoint)
        response = requests.get(
            url,
            params=params or {},
            headers=self._headers(),
            auth=HTTPBasicAuth(self.username, self.password),
            timeout=self.timeout,
        )
        return self._to_oracle_response(response)

    def post(self, endpoint: str, payload: Dict[str, Any], upsert_mode: bool = False) -> OracleResponse:
        url = self._url(endpoint)
        response = requests.post(
            url,
            headers=self._headers(upsert_mode=upsert_mode),
            auth=HTTPBasicAuth(self.username, self.password),
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        return self._to_oracle_response(response)

    def patch(self, endpoint: str, payload: Dict[str, Any]) -> OracleResponse:
        url = self._url(endpoint)
        response = requests.patch(
            url,
            headers=self._headers(),
            auth=HTTPBasicAuth(self.username, self.password),
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        return self._to_oracle_response(response)

    @staticmethod
    def _to_oracle_response(response: requests.Response) -> OracleResponse:
        try:
            body = response.json()
        except Exception:
            body = response.text
        return OracleResponse(
            ok=response.ok,
            status_code=response.status_code,
            body=body,
            text=response.text,
            url=response.url,
        )
