"""Ingesta de Compras Ágiles vigentes desde la API oficial.

"Vigentes" = estado `publicada` (abiertas y recibiendo cotizaciones).

Flujo:
    1. `obtener_vigentes()`     -> listado paginado con filtros (estado, fechas).
    2. `enriquecer_con_detalle()` -> por cada CA seleccionada, el detalle completo
       incluye `productos_solicitados[]` (nombre, cantidad, unidad), que es la
       base para la búsqueda de precios.

Cada request de detalle consume 1 solicitud de la cuota diaria del ticket, por
eso `max_detalles` es configurable (ver config.yaml).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import Config
from .mp_client import CompraAgilClient, MPApiError

logger = logging.getLogger(__name__)


def obtener_vigentes(client: CompraAgilClient, config: Config) -> List[Dict[str, Any]]:
    """Descarga el listado de CAs vigentes según los filtros de config.yaml."""
    mp_cfg = config.seccion("mercado_publico")
    filtros: Dict[str, Any] = {
        "estado": ",".join(mp_cfg.get("estados", ["publicada"])),
        "ordenar_por": mp_cfg.get("ordenar_por", "FechaPublicacion"),
    }

    fecha_desde = config.get("ingesta.fecha_desde")
    if fecha_desde:
        filtros["publicado_desde"] = fecha_desde

    # Sincronización incremental opcional (útil si se corre más de 1 vez/día):
    # solo registros cambiados en las últimas N horas.
    horas_cambio = int(config.get("ingesta.solo_cambios_ultimas_horas", 0) or 0)
    if horas_cambio > 0:
        ahora = datetime.now(timezone.utc)
        desde = datetime.fromtimestamp(ahora.timestamp() - horas_cambio * 3600, tz=timezone.utc)
        filtros["cambio_desde"] = desde.strftime("%Y-%m-%dT%H:%M:%SZ")

    items = client.listar(
        filtros=filtros,
        max_paginas=int(mp_cfg.get("max_paginas", 6)),
        tamanio_pagina=int(mp_cfg.get("tamanio_pagina", 50)),
    )
    logger.info("Listado descargado: %d Compras Ágiles vigentes", len(items))
    return [normalizar_listado(item) for item in items]


def normalizar_listado(item: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte un ítem del listado al formato interno del proyecto."""
    fechas = item.get("fechas") or {}
    montos = item.get("montos") or {}
    institucion = item.get("institucion") or {}
    resumen = item.get("resumen") or {}
    estado = item.get("estado") or {}
    convocatoria = item.get("convocatoria") or {}
    return {
        "codigo": item.get("codigo") or "",
        "nombre": (item.get("nombre") or "").strip(),
        "estado_codigo": estado.get("codigo") or "",
        "estado_glosa": estado.get("glosa") or "",
        "convocatoria_numero": convocatoria.get("estado_convocatoria"),
        "convocatoria_glosa": convocatoria.get("descripcion") or "",
        "fecha_publicacion": fechas.get("fecha_publicacion"),
        "fecha_cierre": fechas.get("fecha_cierre"),
        "monto_disponible": montos.get("monto_disponible"),
        "monto_disponible_clp": montos.get("monto_disponible_clp"),
        "organismo": institucion.get("organismo_comprador") or "",
        "rut_organismo": institucion.get("rut") or "",
        "unidad_compra": institucion.get("unidad_compra") or "",
        "region": institucion.get("region"),
        "region_nombre": institucion.get("nombre_region") or "",
        "ofertas_recibidas": resumen.get("total_ofertas_recibidas"),
        "detalle_api": (item.get("links") or {}).get("detalle") or "",
    }


def enriquecer_con_detalle(
    client: CompraAgilClient,
    compras: List[Dict[str, Any]],
    config: Config,
) -> List[Dict[str, Any]]:
    """Consulta el detalle de las N CAs más interesantes y agrega productos.

    Selección (`mercado_publico.orden_detalles`):
        monto     -> mayor monto disponible primero (default: más potencial $)
        cierre    -> cierra antes primero
        publicado -> publicado más reciente primero

    Las CAs cuyo detalle falle se conservan sin productos (el resto del
    pipeline las marca como SIN DATOS y no detiene la corrida).
    """
    max_detalles = int(config.get("mercado_publico.max_detalles", 30))
    orden = config.get("mercado_publico.orden_detalles", "monto")

    def clave_orden(ca: Dict[str, Any]):
        monto = ca.get("monto_disponible_clp") or 0
        cierre = ca.get("fecha_cierre") or ""
        publicado = ca.get("fecha_publicacion") or ""
        if orden == "cierre":
            return (cierre == "", cierre, -(monto or 0))
        if orden == "publicado":
            return (publicado == "", _invertir_iso(publicado), -(monto or 0))
        return (monto or 0, )  # descendente por monto

    seleccionadas = sorted(
        compras, key=clave_orden, reverse=(orden == "monto")
    )[:max_detalles]

    enriquecidas: List[Dict[str, Any]] = []
    for ca in seleccionadas:
        try:
            det = client.detalle(ca["codigo"])
        except MPApiError as exc:
            logger.warning("Detalle falló para %s: %s", ca["codigo"], exc)
            ca["productos"] = []
            ca["error_detalle"] = str(exc)
            enriquecidas.append(ca)
            continue
        enriquecidas.append(_combinar(ca, det))
        if not ca.get("productos"):
            _intentar_adjunto_pdf(ca, config)
        logger.info(
            "Detalle %s: %d productos, %s CLP",
            ca["codigo"],
            len(ca.get("productos") or []),
            ca.get("monto_disponible_clp"),
        )
    # Las que quedaron fuera de la selección también deben aparecer (sin detalle)
    codigos_procesados = {ca["codigo"] for ca in enriquecidas}
    for ca in compras:
        if ca["codigo"] not in codigos_procesados:
            ca["productos"] = []
            ca["sin_detalle"] = True
            enriquecidas.append(ca)
    return enriquecidas


def _combinar(ca: Dict[str, Any], detalle: Dict[str, Any]) -> Dict[str, Any]:
    """Fusiona el listado normalizado con los campos útiles del detalle."""
    presupuesto = detalle.get("presupuesto") or {}
    entrega = detalle.get("entrega") or {}
    resumen = detalle.get("resumen") or {}
    orden_compra = detalle.get("orden_compra") or {}

    productos = []
    for p in detalle.get("productos_solicitados") or []:
        productos.append(
            {
                "codigo_producto": p.get("codigo_producto"),
                "nombre": (p.get("nombre") or "").strip(),
                "descripcion": (p.get("descripcion") or "").strip() or None,
                "cantidad": _a_float(p.get("cantidad"), 1.0),
                "unidad": p.get("unidad_medida") or "EA",
            }
        )

    # Competencia: mejor (menor) cotización neta activa registrada en la CA.
    # Según la doc oficial, `valor_neto` es el valor neto sin impuestos.
    mejor_cotizacion = None
    for prov in detalle.get("proveedores_cotizando") or []:
        if not prov.get("activo"):
            continue
        valor = _a_float(prov.get("valor_neto"), None)
        if valor is not None and (mejor_cotizacion is None or valor < mejor_cotizacion):
            mejor_cotizacion = valor

    documentos = [
        {"id": d.get("id"), "nombre": (d.get("nombre") or "")}
        for d in (detalle.get("documentos") or []) if isinstance(d, dict)
    ]
    ca.update(
        {
            "documentos": documentos,
            "descripcion": (detalle.get("descripcion") or "").strip() or None,
            "direccion_entrega": entrega.get("direccion_entrega") or None,
            "plazo_entrega_dias": _a_float(entrega.get("plazo_entrega_dias"), None),
            "tipo_presupuesto": presupuesto.get("tipo_presupuesto") or None,
            "presupuesto_estimado": _a_float(presupuesto.get("presupuesto_estimado"), None),
            "productos": productos,
            "mejor_cotizacion_neta": mejor_cotizacion,
            "ofertas_recibidas": resumen.get("total_ofertas_recibidas", ca.get("ofertas_recibidas")),
            "id_orden_compra": orden_compra.get("id_orden_compra"),
        }
    )
    return ca


def _intentar_adjunto_pdf(ca: Dict[str, Any], config: Config) -> None:
    """Sin productos en la API: intenta extraerlos de adjuntos/{codigo}.pdf (OCR).

    El usuario descarga el PDF adjunto desde la ficha en buscador.mercadopublico.cl
    y lo sube al repositorio con el nombre {codigo}.pdf (ej: adjuntos/
    2436-1903-COT26.pdf). Si el OCR no rinde líneas, la CA queda sin productos
    y el dashboard muestra el aviso de adjunto disponible.
    """
    carpeta = config.get("ingesta.carpeta_adjuntos", "adjuntos")

    # 1) Caché del descargador automático (cam_monitor/buscador.py).
    ruta_json = os.path.join(config.get("buscador.dir_salida", "data/adjuntos"),
                             "%s.json" % ca["codigo"])
    if os.path.isfile(ruta_json):
        try:
            with open(ruta_json, "r", encoding="utf-8") as fh:
                items = json.load(fh).get("items") or []
            if items:
                ca["productos"] = items
                ca["detalle_fuente"] = "buscador"
                logger.info("Detalle %s: %d productos desde la caché del "
                            "buscador", ca["codigo"], len(items))
                return
        except Exception as exc:
            logger.warning("Caché de buscador ilegible para %s: %s",
                           ca["codigo"], exc)

    # 2) PDF subido manualmente al repositorio (plan B).
    ruta = os.path.join(carpeta, "%s.pdf" % ca["codigo"])
    if not os.path.isfile(ruta):
        return
    try:
        from .pdf_items import extraer_items_pdf

        max_items = int(config.get("pricing.max_items_por_ca", 8))
        items = extraer_items_pdf(ruta, max_items=max_items)
    except Exception as exc:  # tesseract/pdftoppm ausentes, PDF corrupto, etc.
        logger.warning("OCR falló para el adjunto de %s: %s", ca["codigo"], exc)
        return
    if items:
        ca["productos"] = items
        ca["detalle_fuente"] = "pdf-ocr"
        logger.info("Detalle %s: %d productos extraídos del PDF adjunto",
                    ca["codigo"], len(items))
    else:
        logger.warning("El PDF adjunto de %s no rindió filas de producto", ca["codigo"])


# ------------------------------------------------------------------ helpers
def _a_float(valor: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _invertir_iso(fecha: str) -> str:
    """Invierte 'YYYY-MM-DDTHH:MM:SS' para ordenar desc como string."""
    return "".join(reversed(fecha.split("-"))) if fecha else ""
