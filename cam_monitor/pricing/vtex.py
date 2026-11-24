"""Proveedores de tiendas VTEX vía su API pública de catálogo.

Las tiendas que corren sobre VTEX exponen su catálogo en JSON en el dominio
backend {cuenta}.myvtex.com, sin Cloudflare ni scraping:

    GET https://{cuenta}.myvtex.com/api/catalog_system/pub/products/search/?ft={consulta}

La respuesta es una lista de productos VTEX estándar: cada producto tiene
`items[].sellers[].commertialOffer` con `Price`, `ListPrice` e `IsAvailable`.
Se toma el MENOR precio disponible entre los sellers (precio de internet,
no el precio de lista).

Se instancia un proveedor por tienda configurada en config.yaml (mismo
patrón que `jsonld`). Ejemplo:

    vtex:
      tiendas:
        - nombre: easy
          cuenta: easycl                     # cuenta VTEX de la tienda
          dominio_publico: "https://www.easy.cl"   # para las URLs del dashboard
          activado: true

Notas:
- VTEX a veces responde HTTP 206 (contenido parcial): se acepta como éxito.
- Pausa entre consultas configurable para no abusar del backend (vtex.pausa).
- Si la tienda cambia de plataforma o bloquea, el motor registra el error
  y continúa con los demás proveedores.
"""

from __future__ import annotations

import time
from typing import List
from urllib.parse import quote

import requests

from .base import PrecioEncontrado, PriceProvider, ProviderError


class VtexStoreProvider(PriceProvider):
    """Un proveedor por tienda VTEX (Easy, Jumbo, La Polar, etc.)."""

    nombre = "vtex"          # se sobreescribe por instancia con el nombre de la tienda
    activado = True

    def __init__(self, config_proveedor=None):
        super().__init__(config_proveedor)
        self.cuenta = (self.config.get("cuenta") or "").strip()
        if not self.cuenta:
            raise ValueError("tienda vtex sin 'cuenta' (ej: easycl, jumbochile)")
        self.base = "https://%s.myvtex.com" % self.cuenta
        # fuente mostrada en el dashboard: nombre de la tienda (ej: "easy")
        self.nombre = (self.config.get("nombre") or self.cuenta).strip()
        self.dominio_publico = (self.config.get("dominio_publico") or "").strip()
        self.timeout = int(self.config.get("timeout_segundos", 25))
        self.reintentos = int(self.config.get("reintentos", 2))
        self.max_resultados = int(self.config.get("max_resultados", 5))
        self.pausa = float(self.config.get("pausa_segundos", 1.0))

    def buscar(self, query: str) -> List[PrecioEncontrado]:
        ultimo_error = None
        url = "%s/api/catalog_system/pub/products/search/" % self.base
        for intento in range(self.reintentos + 1):
            if intento:
                time.sleep(3 * intento)
            if self.pausa:
                time.sleep(self.pausa)
            try:
                resp = requests.get(
                    url,
                    params={"ft": quote(query)},
                    headers=self._headers_simples(),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                ultimo_error = exc
                continue
            # VTEX usa 200 y a veces 206 para respuestas válidas
            if resp.status_code in (200, 206):
                try:
                    data = resp.json()
                except ValueError as exc:
                    ultimo_error = exc
                    continue
                return self._parsear(data)
            ultimo_error = ProviderError(
                "HTTP %d: %s" % (resp.status_code, resp.text[:120])
            )
        raise ProviderError(
            "%s falló tras %d intentos: %s"
            % (self.nombre, self.reintentos + 1, ultimo_error)
        )

    def _parsear(self, data) -> List[PrecioEncontrado]:
        precios: List[PrecioEncontrado] = []
        if not isinstance(data, list):
            return precios
        for prod in data[: self.max_resultados]:
            titulo = (prod.get("productName") or "").strip()
            if not titulo:
                continue
            # menor precio disponible entre todos los items/sellers de la ficha
            mejor = None
            for item in prod.get("items") or []:
                for seller in item.get("sellers") or []:
                    oferta = seller.get("commertialOffer") or {}
                    if not oferta.get("IsAvailable"):
                        continue
                    precio = oferta.get("Price")
                    if not precio or float(precio) <= 0:
                        continue
                    valor = int(round(float(precio)))
                    if mejor is None or valor < mejor:
                        mejor = valor
            if mejor is None:
                continue  # sin ofertas disponibles: descartar (igual que solotodo)
            precios.append(
                PrecioEncontrado(
                    fuente=self.nombre,
                    titulo=titulo,
                    precio_clp=mejor,
                    url=self._url_producto(prod),
                    disponible=True,
                )
            )
        return precios

    def _url_producto(self, prod) -> str:
        enlace = (prod.get("link") or "").strip()
        if enlace and self.dominio_publico:
            # la API devuelve URLs del dominio backend (myvtex): reescribir
            # al dominio público de la tienda para que sirvan en el dashboard
            enlace = enlace.replace(self.base, self.dominio_publico)
        if enlace:
            return enlace
        texto = (prod.get("linkText") or "").strip("/")
        return "%s/%s/p" % (self.dominio_publico or self.base, texto)
