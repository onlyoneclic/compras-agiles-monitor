"""Proveedor MercadoLibre Chile (MLC) vía API oficial de MercadoLibre.

Desde 2024/2025 la búsqueda pública exige token de aplicación. Si usted
dispone de un token (https://developers.mercadolibre.cl), actívelo en
config.yaml y defina la variable de entorno ML_TOKEN. Sin token, este
proveedor queda deshabilitado y el pipeline continúa con los demás.
"""

from __future__ import annotations

import os
import time
from typing import List

import requests

from .base import PrecioEncontrado, PriceProvider, ProviderError

API_URL = "https://api.mercadolibre.com/sites/MLC/search"


class MercadoLibreProvider(PriceProvider):
    nombre = "mercadolibre"
    activado = False  # requiere token; activar en config.yaml

    def __init__(self, config_proveedor=None):
        super().__init__(config_proveedor)
        self.timeout = int(self.config.get("timeout_segundos", 20))
        self.max_resultados = int(self.config.get("max_resultados", 5))

    def buscar(self, query: str) -> List[PrecioEncontrado]:
        token = os.environ.get("ML_TOKEN", "")
        headers = {"Authorization": "Bearer %s" % token} if token else {}
        try:
            resp = requests.get(
                API_URL,
                params={"q": query, "limit": self.max_resultados},
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError("mercadolibre error de red: %s" % exc)
        if resp.status_code in (401, 403):
            raise ProviderError(
                "mercadolibre requiere un token válido (variable ML_TOKEN)."
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
