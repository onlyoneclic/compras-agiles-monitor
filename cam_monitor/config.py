"""Carga de configuración: config.yaml + variables de entorno.

El archivo config.yaml es la única fuente de parámetros. La variable de
entorno MP_TICKET (definida en .env o en el entorno) nunca se escribe en
config.yaml para evitar que se suba al repositorio.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


class Config:
    """Acceso tipo diccionario anidado a la configuración del proyecto."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    # -- acceso -------------------------------------------------------------
    def get(self, ruta: str, default: Any = None) -> Any:
        """Obtiene un valor por ruta con puntos: 'rentabilidad.iva'."""
        nodo: Any = self._data
        for parte in ruta.split("."):
            if not isinstance(nodo, dict) or parte not in nodo:
                return default
            nodo = nodo[parte]
        return nodo

    def seccion(self, ruta: str) -> Dict[str, Any]:
        """Devuelve una sección completa como dict ({} si no existe)."""
        valor = self.get(ruta, {})
        return valor if isinstance(valor, dict) else {}

    def __getitem__(self, ruta: str) -> Any:
        valor = self.get(ruta)
        if valor is None:
            raise KeyError(ruta)
        return valor

    # -- valores derivados ---------------------------------------------------
    @property
    def ticket(self) -> Optional[str]:
        """Ticket de la API leído desde el entorno (nunca desde el YAML)."""
        nombre_env = self.get("mercado_publico.ticket_env", "MP_TICKET")
        return os.environ.get(nombre_env) or None

    @property
    def dir_datos(self) -> str:
        return self.get("salida.dir_datos", "data")

    @property
    def cache_dir(self) -> str:
        return self.get("pricing.cache_dir", ".cache/prices")


def cargar_config(ruta_config: Optional[str] = None) -> Config:
    """Carga config.yaml. Busca en la ruta indicada, CWD y raíz del repo."""
    if yaml is None:
        raise RuntimeError(
            "Falta la dependencia PyYAML. Instale con: pip install -r requirements.txt"
        )
    candidatas: list = []
    if ruta_config:
        candidatas.append(ruta_config)
    candidatas += ["config.yaml", os.path.join(os.path.dirname(__file__), "..", "config.yaml")]
    for ruta in candidatas:
        ruta_abs = os.path.abspath(ruta)
        if os.path.isfile(ruta_abs):
            with open(ruta_abs, "r", encoding="utf-8") as fh:
                return Config(yaml.safe_load(fh) or {})
    raise FileNotFoundError(
        "No se encontró config.yaml (buscado en: %s)" % ", ".join(candidatas)
    )
