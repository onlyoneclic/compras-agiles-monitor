"""Proveedor Solotodo (api.solotodo.com) — comparador de precios chileno.

API pública sin autenticación, con precios en CLP agregados de decenas de
tiendas nacionales (Madrid, PC Factory, Ripley, Paris, Ferix, Corfinsura...).
Cobertura fuerte en electrónica/cómputo; para otros rubros devuelve poco.

Endpoint: GET https://api.solotodo.com/products/?search={query}
Rate limit: conviene usar caché y timeouts generosos.
"""

from __future__ import annotations

import logging
import time
from typing import List

import requests

from .base import PrecioEncontrado, PriceProvider, ProviderError

logger = logging.getLogger(__name__)

API_URL = "https://api.solotodo.com/products/"


class SolotodoProvider(PriceProvider):
    nombre = "solotodo"
    activado = True

    def __init__(self, config_proveedor=None):
        super().__init__(config_proveedor)
        self.timeout = int(self.config.get("timeout_segundos", 25))
        self.reintentos = int(self.config.get("reintentos", 2))
        self.max_resultados = int(self.config.get("max_resultados", 5))

    def buscar(self, query: str) -> List[PrecioEncontrado]:
        ultimo_error = None
        for intento in range(self.reintentos + 1):
            if intento:
                time.sleep(3 * intento)
            try:
                resp = requests.get(
                    API_URL,
                    params={"search": query},
                    timeout=self.timeout,
                    headers=self._headers_simples(),
                )
            except requests.RequestException as exc:
                ultimo_error = exc
                continue
            if resp.status_code == 200:
                return self._parsear(resp.json())
            ultimo_error = ProviderError("solotodo HTTP %d: %s" % (resp.status_code, resp.text[:120]))
        raise ProviderError("solotodo falló tras %d intentos: %s" % (self.reintentos + 1, ultimo_error))

    def _parsear(self, data: dict) -> List[PrecioEncontrado]:
        precios: List[PrecioEncontrado] = []
        resultados = data.get("results") or []
        for prod in resultados[: self.max_resultados]:
            registro = prod.get("active_registry") or {}
            # offer_price = mejor precio oferta; normal_price = precio normal
            precio = registro.get("offer_price") or registro.get("normal_price")
            if not precio or int(precio) <= 0:
                continue
            precios.append(
                PrecioEncontrado(
                    fuente=self.nombre,
                    titulo=(prod.get("name") or "").strip(),
                    precio_clp=int(precio),
                    url="https://solotodo.com/products/%s" % prod.get("id"),
                    disponible=bool(prod.get("is_available", True)),
                )
            )
        return precios
