"""Proveedor genérico JSON-LD: busca en cualquier tienda que emita
schema.org/Product en sus páginas de búsqueda (`<script type="application/ld+json">`).

Muchas tiendas chilenas (Paris, Sodimac, Easy, ABCDin...) incluyen JSON-LD.
Este proveedor descarga la URL de búsqueda configurada y extrae Product+Offer,
por lo que agregar una tienda nueva es solo configuración:

    pricing:
      proveedores:
        jsonld:
          activado: true
          tiendas:
            - nombre: paris
              url_busqueda: "https://www.paris.cl/search?text={query}"
              dominio: "https://www.paris.cl"

NOTA: algunas tiendas usan anti-bot (Cloudflare/Akamai) y responderán 403;
en ese caso el proveedor reporta el error y el motor sigue con los demás.
Use con moderación y respete los términos de uso de cada tienda.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

import requests

try:
    from bs4 import BeautifulSoup  # noqa: F401
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

from .base import PrecioEncontrado, PriceProvider, ProviderError

logger = logging.getLogger(__name__)


class JsonLdProvider(PriceProvider):
    """Busca productos en una tienda cuyo HTML de búsqueda incluye JSON-LD."""

    def __init__(self, config_proveedor=None):
        super().__init__(config_proveedor)
        self.nombre = self.config.get("nombre", "jsonld")
        self.url_busqueda = self.config.get("url_busqueda", "")
        self.dominio = self.config.get("dominio", "")
        self.timeout = int(self.config.get("timeout_segundos", 20))
        self.max_resultados = int(self.config.get("max_resultados", 5))
        if not self.url_busqueda:
            raise ValueError("Proveedor jsonld sin 'url_busqueda' en config.yaml")
        if BeautifulSoup is None:
            raise ValueError("Falta beautifulsoup4 (pip install -r requirements.txt)")

    def buscar(self, query: str) -> List[PrecioEncontrado]:
        url = self.url_busqueda.replace("{query}", requests.utils.quote(query))
        try:
            resp = requests.get(
                url, headers=self._headers_simples(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise ProviderError("%s error de red: %s" % (self.nombre, exc))
        if resp.status_code in (403, 429, 503):
            raise ProviderError(
                "%s bloqueó la solicitud (HTTP %d); anti-bot o rate limit."
                % (self.nombre, resp.status_code)
            )
        if resp.status_code != 200:
            raise ProviderError("%s HTTP %d" % (self.nombre, resp.status_code))

        productos = self._extraer_jsonld(resp.text)
        precios: List[PrecioEncontrado] = []
        for prod in productos[: self.max_resultados]:
            ofertas = prod.get("offers") or []
            if isinstance(ofertas, dict):
                ofertas = [ofertas]
            precio = None
            for oferta in ofertas:
                try:
                    precio = int(round(float(oferta.get("price"))))
                    break
                except (TypeError, ValueError):
                    continue
            if not precio or precio <= 0:
                continue
            link = prod.get("url") or prod.get("@id") or ""
            if link and link.startswith("/") and self.dominio:
                link = self.dominio.rstrip("/") + link
            precios.append(
                PrecioEncontrado(
                    fuente=self.nombre,
                    titulo=str(prod.get("name") or "").strip(),
                    precio_clp=precio,
                    url=link,
                )
            )
        return precios

    def _extraer_jsonld(self, html: str) -> List[dict]:
        """Extrae objetos Product (incluidos dentro de @graph / ItemList)."""
        soup = BeautifulSoup(html, "lxml")
        encontrados: List[dict] = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except (TypeError, ValueError):
                continue
            for obj in _aplanar(data):
                if isinstance(obj, dict) and obj.get("@type") in ("Product", "ListItem") and obj.get("name"):
                    if obj.get("@type") == "ListItem" and not obj.get("item"):
                        continue
                    item = obj.get("item") if isinstance(obj.get("item"), dict) else obj
                    if item.get("@type") == "Product":
                        encontrados.append(item)
        return encontrados


def _aplanar(obj: object, _prof: int = 0) -> list:
    """Recorre estructuras JSON anidadas (@graph, listas) hasta cierta prof."""
    if _prof > 4:
        return []
    salida: List[dict] = []
    if isinstance(obj, dict):
        salida.append(obj)
        salida.extend(_aplanar(obj.get("@graph"), _prof + 1))
    elif isinstance(obj, list):
        for elemento in obj:
            salida.extend(_aplanar(elemento, _prof + 1))
    return salida
