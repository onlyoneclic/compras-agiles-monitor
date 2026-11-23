"""Base del motor de precios: interfaz de proveedores y tipos comunes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PrecioEncontrado:
    """Un precio de un producto en una tienda nacional."""

    fuente: str                 # nombre del proveedor (ej: "solotodo")
    titulo: str                 # título del producto en la tienda
    precio_clp: int             # precio en CLP (asumiendo precio al público)
    url: str                    # enlace al producto
    disponible: bool = True     # False si está fuera de stock


@dataclass
class ResultadoBusqueda:
    """Resultado de consultar todos los proveedores para una consulta."""

    query: str
    precios: List[PrecioEncontrado] = field(default_factory=list)
    errores: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.precios)


class ProviderError(Exception):
    """Un proveedor falló (bloqueo anti-bot, rate limit, cambio de HTML...)."""


class PriceProvider:
    """Interfaz de un proveedor de precios en una tienda nacional.

    Para agregar una tienda nueva: herede de esta clase, implemente `buscar`
    y regístrela en `pricing/__init__.py` + actívela en config.yaml.
    """

    nombre: str = "base"
    activado: bool = False

    def __init__(self, config_proveedor: Optional[dict] = None):
        self.config = config_proveedor or {}

    def buscar(self, query: str) -> List[PrecioEncontrado]:
        """Busca el producto y devuelve precios en CLP (precio al público).

        Debe lanzar ProviderError si la tienda bloquea o cambia su formato;
        el motor captura el error y continúa con los demás proveedores.
        """
        raise NotImplementedError

    # ---------------------------------------------------------------- utils
    @staticmethod
    def _headers_simples() -> dict:
        """Headers básicos tipo navegador (algunas tiendas los exigen)."""
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "es-CL,es;q=0.9",
        }


_LIMPIEZA_EXTRA = re.compile(r"\s+")


def normalizar_query(nombre: str, descripcion: Optional[str] = None) -> str:
    """Construye la consulta de búsqueda a partir de nombre (+ descripción).

    La descripción de la línea puede aportar contexto útil (marca, detalle),
    pero suele ser larga: se usa solo como refuerzo si el nombre es muy corto.
    """
    texto = (nombre or "").strip()
    if len(texto) < 12 and descripcion:
        texto = "%s %s" % (texto, descripcion.strip())
    texto = texto.lower()
    texto = re.sub(r"[^\wáéíóúñü\s]", " ", texto, flags=re.UNICODE)
    return _LIMPIEZA_EXTRA.sub(" ", texto).strip()
