"""Generación del dashboard web (HTML autocontenido).

Lee la plantilla `web/dashboard_template.html`, inyecta los datos de la
corrida como JSON (`window.__DATA__ = {...}`) y escribe `web/index.html`.
El resultado funciona sin servidor: se abre directo en el navegador y es
fácil de publicar en GitHub Pages.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

VEREDICTOS_ORDEN = {"RENTABLE": 0, "MARGINAL": 1, "NO RENTABLE": 2, "SIN DATOS": 3}


def construir_datos_dashboard(compras: List[dict], resumen: dict, meta: dict) -> dict:
    """Ordena y recorta los datos que el dashboard necesita."""
    ordenadas = sorted(
        compras,
        key=lambda c: (
            VEREDICTOS_ORDEN.get(c.get("analisis", {}).get("veredicto", "SIN DATOS"), 9),
            -(c.get("analisis", {}).get("margen") or -1),
            -(c.get("monto_disponible_clp") or 0),
        ),
    )
    return {
        "meta": {
            **meta,
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": _version_proyecto(),
        },
        "resumen": resumen,
        "compras": ordenadas,
    }


def generar_html(ruta_plantilla: str, datos: dict, ruta_salida: str) -> str:
    """Inyecta datos JSON en la plantilla y guarda el HTML final."""
    if not os.path.isfile(ruta_plantilla):
        raise FileNotFoundError("Plantilla del dashboard no encontrada: %s" % ruta_plantilla)
    with open(ruta_plantilla, "r", encoding="utf-8") as fh:
        plantilla = fh.read()

    json_datos = json.dumps(datos, ensure_ascii=False)
    # Evita cerrar el <script> accidentalmente con '</' en textos de productos.
    json_datos = json_datos.replace("</", "<\\/")

    if "__DATA_PLACEHOLDER__" not in plantilla:
        raise ValueError("La plantilla debe contener el marcador __DATA_PLACEHOLDER__")
    html = plantilla.replace("__DATA_PLACEHOLDER__", json_datos)

    os.makedirs(os.path.dirname(os.path.abspath(ruta_salida)), exist_ok=True)
    with open(ruta_salida, "w", encoding="utf-8") as fh:
        fh.write(html)
    logger.info("Dashboard generado: %s (%.1f KB)", ruta_salida, len(html) / 1024)
    return ruta_salida


def _version_proyecto() -> str:
    try:
        from . import __version__

        return __version__
    except Exception:  # pragma: no cover
        return "?"
