"""Motor de precios: coordina proveedores de tiendas con caché en disco.

Uso:
    engine = MotorPrecios(config)
    resultado = engine.buscar("toner hp 12a")

- Consulta en paralelo los proveedores activados (ThreadPool).
- Cachea cada query en .cache/prices/{sha1}.json por N horas para no
  golpear a las tiendas en cada corrida (y respetar rate limits).
- Los errores de un proveedor no interrumpen la corrida: se acumulan en
  ResultadoBusqueda.errores.
- En modo demo se inyecta un DemoProvider con precios de ejemplo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from .base import PrecioEncontrado, PriceProvider, ProviderError, ResultadoBusqueda
from .mercadolibre import MercadoLibreProvider
from .solotodo import SolotodoProvider

logger = logging.getLogger(__name__)

# Registro de proveedores "codificados"; jsonld se instancia por tienda configurada.
PROVEEDORES_CODIFICADOS = {
    "solotodo": SolotodoProvider,
    "mercadolibre": MercadoLibreProvider,
}


class MotorPrecios:
    def __init__(self, config, demo: bool = False):
        self.config = config
        self.demo = demo
        self.cache_horas = float(config.get("pricing.cache_horas", 20))
        self.cache_dir = config.cache_dir
        self.timeout_global = int(config.get("pricing.timeout_segundos", 15))
        self.max_workers = int(config.get("pricing.max_workers", 4))
        self.proveedores: List[PriceProvider] = self._crear_proveedores()

    # ------------------------------------------------------------------ setup
    def _crear_proveedores(self) -> List[PriceProvider]:
        proveedores: List[PriceProvider] = []
        if self.demo:
            from ..demo import DemoProvider  # import tardío para evitar ciclo

            proveedores.append(DemoProvider())
            return proveedores

        cfg_provs = self.config.seccion("pricing.proveedores")
        for nombre, clase in PROVEEDORES_CODIFICADOS.items():
            conf = cfg_provs.get(nombre) or {}
            activado = bool(conf.get("activado", clase.activado))
            if activado:
                try:
                    proveedores.append(clase(conf))
                except Exception as exc:  # config incompleta
                    logger.warning("Proveedor %s no se pudo inicializar: %s", nombre, exc)

        for tienda in cfg_provs.get("jsonld", {}).get("tiendas", []) or []:
            if not tienda.get("activado", True):
                continue
            try:
                from .jsonld import JsonLdProvider

                proveedores.append(JsonLdProvider(tienda))
            except Exception as exc:
                logger.warning("Tienda jsonld '%s' no se pudo inicializar: %s",
                               tienda.get("nombre"), exc)

        for tienda in cfg_provs.get("vtex", {}).get("tiendas", []) or []:
            if not tienda.get("activado", True):
                continue
            try:
                from .vtex import VtexStoreProvider

                proveedores.append(VtexStoreProvider(tienda))
            except Exception as exc:
                logger.warning("Tienda vtex '%s' no se pudo inicializar: %s",
                               tienda.get("nombre"), exc)

        logger.info("Proveedores de precios activos: %s",
                    [p.nombre for p in proveedores] or "ninguno")
        return proveedores

    # ----------------------------------------------------------------- buscar
    def buscar(self, query: str) -> ResultadoBusqueda:
        """Busca una consulta en todos los proveedores (con caché)."""
        clave_cache = self._clave(query)
        cacheado = self._leer_cache(clave_cache)
        if cacheado is not None:
            return ResultadoBusqueda(query=query, precios=cacheado)

        resultado = ResultadoBusqueda(query=query)
        if not self.proveedores:
            resultado.errores.append("No hay proveedores de precios activados.")
            return resultado

        def correr(proveedor: PriceProvider):
            try:
                return proveedor.buscar(query)
            except ProviderError as exc:
                resultado.errores.append(str(exc))
                return []
            except Exception as exc:  # nunca romper la corrida por una tienda
                resultado.errores.append("%s: error inesperado %s" % (proveedor.nombre, exc))
                return []

        with ThreadPoolExecutor(max_workers=max(1, len(self.proveedores))) as pool:
            for precios in pool.map(correr, self.proveedores):
                resultado.precios.extend(precios)

        resultado.precios = self._deduplicar(resultado.precios)
        self._escribir_cache(clave_cache, resultado)
        return resultado

    # ------------------------------------------------------------------ caché
    def _clave(self, query: str) -> str:
        prefijo = "demo|" if self.demo else ""  # separa caché demo de la real
        return hashlib.sha1((prefijo + query.lower()).encode("utf-8")).hexdigest()

    def _leer_cache(self, clave: str):
        ruta = os.path.join(self.cache_dir, "%s.json" % clave)
        if not os.path.isfile(ruta):
            return None
        if time.time() - os.path.getmtime(ruta) > self.cache_horas * 3600:
            return None
        try:
            with open(ruta, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return [PrecioEncontrado(**p) for p in data]
        except (OSError, ValueError, TypeError):
            return None

    def _escribir_cache(self, clave: str, resultado: ResultadoBusqueda) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            ruta = os.path.join(self.cache_dir, "%s.json" % clave)
            with open(ruta, "w", encoding="utf-8") as fh:
                json.dump([p.__dict__ for p in resultado.precios], fh,
                          ensure_ascii=False)
        except OSError as exc:
            logger.warning("No se pudo escribir caché: %s", exc)

    @staticmethod
    def _deduplicar(precios: List[PrecioEncontrado]) -> List[PrecioEncontrado]:
        vistos, unicos = set(), []
        for p in precios:
            clave = (p.fuente, p.url or p.titulo.lower())
            if clave in vistos:
                continue
            vistos.add(clave)
            unicos.append(p)
        return unicos
