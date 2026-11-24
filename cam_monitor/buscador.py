"""Descarga automática de adjuntos PDF de la ficha de Compra Ágil.

Para las CAs que la API entrega sin `productos_solicitados` pero con
`documentos[]` (el detalle vive en un PDF escaneado publicado en la ficha),
este módulo abre la ficha pública con un navegador headless (Playwright),
detecta los PDF que la página carga o enlaza, los descarga y extrae sus
líneas de producto con el OCR de pdf_items.py.

Por qué un navegador: la ficha (buscador.mercadopublico.cl) es una SPA
servida por CloudFront que rechaza peticiones simples de servidores (403) y
la API oficial NO expone URL de descarga de documentos (verificado: la doc
solo lista documentos[].id/nombre). Un navegador real ejecuta el JavaScript
de la página y aporta cookies/cabeceras plausibles. Si CloudFront bloquea
también al runner, este módulo falla silencioso y la CA conserva el aviso de
adjunto (plan B: subir el PDF a adjuntos/{codigo}.pdf; sigue funcionando).

Requisitos (instalados best-effort por el workflow):
    pip install playwright
    python -m playwright install chromium --with-deps

Uso:
    - Integrado: cli.py invoca completar_con_adjuntos() tras la ingesta.
    - Manual:    python -m cam_monitor.buscador   (procesa la última corrida)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import urljoin

from .config import Config

logger = logging.getLogger(__name__)

FICHA_URL = "https://buscador.mercadopublico.cl/ficha?code=%s"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class BuscadorNoDisponible(Exception):
    """El navegador no está instalado o la ficha no fue accesible."""


def _carpeta_salida(config: Config) -> str:
    return config.get("buscador.dir_salida", "data/adjuntos")


def _max_items(config: Config) -> int:
    return int(config.get("pricing.max_items_por_ca", 8))


def aplicar_cache(ca: Dict[str, Any], config: Config) -> bool:
    """Aplica el resultado cacheado del buscador ({codigo}.json) si existe."""
    ruta = os.path.join(_carpeta_salida(config), "%s.json" % ca["codigo"])
    if not os.path.isfile(ruta):
        return False
    try:
        with open(ruta, "r", encoding="utf-8") as fh:
            datos = json.load(fh)
    except Exception:
        return False
    items = datos.get("items") or []
    if not items:
        return False
    ca["productos"] = items
    ca["detalle_fuente"] = datos.get("fuente", "buscador")
    logger.info("Detalle %s: %d ítems desde caché del buscador",
                ca["codigo"], len(items))
    return True


def completar_con_adjuntos(cas: List[Dict[str, Any]], config: Config) -> int:
    """Descarga y procesa los adjuntos de las CAs pendientes. Devuelve n° OK.

    Modifica cada CA in-place (productos, detalle_fuente) y deja caché en
    data/adjuntos/{codigo}.json. Nunca lanza hacia arriba salvo que Playwright
    no esté instalado (BuscadorNoDisponible); errores por CA solo se registran.
    """
    pendientes = [c for c in cas if not (c.get("productos") or [])]
    resueltas = 0

    # 1) Caché de corridas anteriores: sin navegador.
    restantes: List[Dict[str, Any]] = []
    for ca in pendientes:
        if aplicar_cache(ca, config):
            resueltas += 1
        else:
            restantes.append(ca)
    if not restantes:
        return resueltas

    # 2) Navegador para el resto.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BuscadorNoDisponible(
            "Playwright no está instalado (pip install playwright)") from exc

    max_cas = int(config.get("buscador.max_cas_por_corrida", 5))
    timeout_ms = int(config.get("buscador.timeout_ms", 45000))
    os.makedirs(_carpeta_salida(config), exist_ok=True)

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        contexto = navegador.new_context(
            user_agent=USER_AGENT, locale="es-CL",
            viewport={"width": 1366, "height": 900})
        try:
            for ca in restantes[:max_cas]:
                codigo = ca["codigo"]
                try:
                    pdfs = _pdfs_de_ficha(contexto, codigo, timeout_ms)
                    items = _items_desde_pdfs(contexto, pdfs, config)
                except Exception as exc:
                    logger.warning("Ficha %s no disponible: %s", codigo, exc)
                    continue
                if items:
                    ca["productos"] = items
                    ca["detalle_fuente"] = "buscador"
                    resueltas += 1
                    logger.info("Detalle %s: %d ítems extraídos del adjunto "
                                "de la ficha", codigo, len(items))
                else:
                    logger.warning("Adjunto de %s descargado pero sin filas "
                                   "de producto reconocibles", codigo)
        finally:
            navegador.close()
    return resueltas


def _pdfs_de_ficha(contexto, codigo: str, timeout_ms: int) -> List[str]:
    """Abre la ficha y devuelve las URLs de PDF que carga o enlaza."""
    urls: List[str] = []

    def _oler(resp):  # PDFs que la página descarga por su cuenta
        try:
            if "application/pdf" in (resp.headers or {}).get("content-type", "").lower():
                urls.append(resp.url)
        except Exception:
            pass

    pagina = contexto.new_page()
    pagina.on("response", _oler)
    try:
        resp = pagina.goto(FICHA_URL % codigo, timeout=timeout_ms,
                           wait_until="domcontentloaded")
        if resp is None or resp.status != 200:
            raise BuscadorNoDisponible("HTTP %s" % (resp.status if resp else "?"))
        pagina.wait_for_timeout(4000)  # render del SPA y carga de adjuntos
        for enlace in pagina.query_selector_all("a[href]"):
            href = enlace.get_attribute("href") or ""
            if ".pdf" in href.lower():
                urls.append(urljoin(pagina.url, href))
    finally:
        pagina.close()
    return list(dict.fromkeys(urls))[:2]  # sin duplicados, máx. 2 archivos


def _items_desde_pdfs(contexto, pdfs: List[str], config: Config) -> List[dict]:
    """Descarga los PDFs y extrae sus ítems con el OCR (pdf_items)."""
    from .pdf_items import extraer_items_pdf

    for url in pdfs:
        try:
            resp = contexto.request.get(url, timeout=30000)
        except Exception as exc:
            logger.warning("Descarga fallida de %s: %s", url, exc)
            continue
        contenido = resp.body() or b""
        if resp.status != 200 or not contenido.startswith(b"%PDF"):
            logger.warning("El archivo de %s no es un PDF (HTTP %s)",
                           url, resp.status)
            continue
        with tempfile.TemporaryDirectory(prefix="cam-adj-") as tmp:
            ruta = os.path.join(tmp, "adjunto.pdf")
            with open(ruta, "wb") as fh:
                fh.write(contenido)
            items = extraer_items_pdf(ruta, max_items=_max_items(config))
        if items:
            return items
    return []


def main() -> int:
    """Modo manual: procesa las CAs pendientes de la última corrida."""
    import logging as _log
    from .config import cargar_config
    from .storage import Almacen

    _log.basicConfig(level=_log.INFO,
                     format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    config = cargar_config()
    almacen = Almacen(config.dir_datos)
    ruta = almacen.ultima_corrida()
    if not ruta or not os.path.isfile(os.path.join(ruta, "analizado.json")):
        logger.info("No hay corridas previas; nada que procesar.")
        return 0
    compras = (almacen.leer(os.path.join(ruta, "analizado.json"))
               or {}).get("compras") or []
    pendientes = [c for c in compras
                  if not (c.get("productos") or []) and (c.get("documentos") or [])]
    if not pendientes:
        logger.info("Ninguna CA pendiente de adjunto.")
        return 0
    try:
        ok = completar_con_adjuntos(pendientes, config)
    except BuscadorNoDisponible as exc:
        logger.warning("Descarga de adjuntos no disponible: %s", exc)
        return 0
    logger.info("Adjuntos procesados: %d de %d pendientes", ok, len(pendientes))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
