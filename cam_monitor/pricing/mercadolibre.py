
"""Proveedor MercadoLibre Chile (MLC) vía API oficial de MercadoLibre.

Desde 2024/2025 la búsqueda pública exige token de aplicación (sin él la API
responde 403). Hay dos formas de autenticarse, por variables de entorno o
secrets de GitHub Actions:

  1) ML_TOKEN               -> un token ya generado (dura ~6 horas; poco práctico).
  2) ML_CLIENT_ID +         -> credenciales de la aplicación creada en
     ML_CLIENT_SECRET          https://developers.mercadolibre.cl; este proveedor
                               genera y renueva el token SOLO (fluxo
                               client_credentials de ML) y lo cachea en memoria.

Sin credenciales el proveedor no se inicializa y el motor continúa con los
demás proveedores (SoloTodo, jsonld) y con las cotizaciones de la CA.
"""

from __future__ import annotations

import os
import threading
import time
from typing import List

import requests

from .base import PrecioEncontrado, PriceProvider, ProviderError

API_URL = "https://api.mercadolibre.com/sites/MLC/search"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

# Token de aplicación compartido entre instancias y corridas del mismo proceso.
_TOKEN: dict = {"token": None, "expira": 0.0}
_LOCK = threading.Lock()


class MercadoLibreProvider(PriceProvider):
    nombre = "mercadolibre"
    activado = True  # se activa en config.yaml; sin credenciales se omite con aviso

    def __init__(self, config_proveedor=None):
        super().__init__(config_proveedor)
        self.timeout = int(self.config.get("timeout_segundos", 20))
        self.max_resultados = int(self.config.get("max_resultados", 5))
        self.token_directo = os.environ.get("ML_TOKEN", "")
        self.client_id = os.environ.get("ML_CLIENT_ID", "")
        self.client_secret = os.environ.get("ML_CLIENT_SECRET", "")
        if not self.token_directo and not (self.client_id and self.client_secret):
            raise ValueError(
                "mercadolibre sin credenciales: defina ML_TOKEN, o "
                "ML_CLIENT_ID + ML_CLIENT_SECRET (secrets de Actions)."
            )

    # ------------------------------------------------------------------ token
    def _obtener_token(self) -> str:
        """Devuelve un token válido (client_credentials), renovándolo si expira."""
        if _TOKEN["token"] and time.time() < _TOKEN["expira"]:
            return _TOKEN["token"]
        with _LOCK:
            if _TOKEN["token"] and time.time() < _TOKEN["expira"]:
                return _TOKEN["token"]
            try:
                resp = requests.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise ProviderError(
                    "mercadolibre error de red al pedir token: %s" % exc
                )
            if resp.status_code != 200:
                raise ProviderError(
                    "mercadolibre rechazó las credenciales (HTTP %d): revise "
                    "ML_CLIENT_ID y ML_CLIENT_SECRET." % resp.status_code
                )
            datos = resp.json()
            token = datos.get("access_token") or ""
            segundos = int(datos.get("expires_in") or 21600)
            if not token:
                raise ProviderError("mercadolibre respondió sin access_token.")
            _TOKEN["token"] = token
            # renovar 10 minutos antes de que expire
            _TOKEN["expira"] = time.time() + max(60, segundos - 600)
            return token

    # ------------------------------------------------------------------ buscar
    def buscar(self, query: str) -> List[PrecioEncontrado]:
        token = self.token_directo or self._obtener_token()
        return self._consultar(query, token, reintento=True)

    def _consultar(self, query: str, token: str, reintento: bool) -> List[PrecioEncontrado]:
        try:
            resp = requests.get(
                API_URL,
                params={"q": query, "limit": self.max_resultados},
                headers={"Authorization": "Bearer %s" % token},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError("mercadolibre error de red: %s" % exc)
        if resp.status_code == 401 and reintento and not self.token_directo:
            # el token de app venció antes de tiempo: invalidar y pedir otro
            _TOKEN["token"] = None
            _TOKEN["expira"] = 0.0
            return self._consultar(query, self._obtener_token(), reintento=False)
        if resp.status_code in (401, 403):
            raise ProviderError(
                "mercadolibre rechazó el token (HTTP %d)." % resp.status_code
            )
        if resp.status_code != 200:
            raise ProviderError("mercadolibre HTTP %d" % resp.status_code)
        return self._parsear(resp.json())

    def _parsear(self, data: dict) -> List[PrecioEncontrado]:
        precios: List[PrecioEncontrado] = []
        for item in (data.get("results") or [])[: self.max_resultados]:
            precio = item.get("price")
            if not precio or precio <= 0:
                continue
            precios.append(
                PrecioEncontrado(
                    fuente=self.nombre,
                    titulo=item.get("title") or "",
                    precio_clp=int(round(precio)),
                    url=item.get("permalink") or "",
                    disponible=item.get("status") == "active",
                )
            )
        return precios
