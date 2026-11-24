"""Motor de rentabilidad: estima utilidad de adjudicarse una Compra Ágil.

Modelo (supuestos configurables en config.yaml -> rentabilidad):

    ingreso_neto = monto_disponible                    (se asume neto; si no, se le quita IVA)
    costo_unitario_neto = precio_tienda / (1 + IVA)    (los precios de tiendas incluyen IVA)
    costo_adquisicion = Σ costo_unitario_neto × cantidad
    costo_total = costo_adquisicion + flete + costo_adquisicion × comisión_pct
    utilidad = ingreso_neto − costo_total
    margen = utilidad / ingreso_neto

Veredictos:
    RENTABLE    margen ≥ margen_rentable (default 10%)
    MARGINAL    0 ≤ margen < margen_rentable
    NO RENTABLE margen < 0
    SIN DATOS   sin estimación posible (no alcanza cobertura de tiendas y
                tampoco hay cotizaciones de competencia en la CA)

Dos fuentes de estimación (fuente_estimacion):
    tiendas                 -> precios de tiendas (SoloTodo / ML / jsonld)
    cotizacion_competencia  -> si la cobertura de tiendas es baja y hay
                               proveedores que ya cotizaron en la CA, su mejor
                               cotización neta se usa como proxy del mercado:
                               costo = cot / (1 + margen_proveedor_estimado);
                               ingreso al ganar = min(monto, cot * (1-descuento)).
                               Rubros no-electrónicos quedan así cubiertos sin
                               depender de scraping (ver config.yaml).

Advertencia de competencia: si en la CA ya existe una cotización de otro
proveedor con valor menor que nuestro costo estimado, es muy difícil ganar
bajando aún más: se marca `riesgo_competencia`.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ESTRATEGIAS = {"minimo", "percentil_25", "mediana", "promedio"}


def analizar_compra(compra: Dict, resultados_precio: Dict[str, Dict], config) -> Dict:
    """Calcula rentabilidad de una CA.

    compra: dict normalizado por ingest (con 'productos').
    resultados_precio: {nombre_producto_normalizado: {query, precios, errores}}
    config: objeto Config del proyecto.
    """
    rent_cfg = config.seccion("rentabilidad")
    iva = float(rent_cfg.get("iva", 0.19))
    monto_es_neto = bool(rent_cfg.get("monto_disponible_es_neto", True))
    precios_con_iva = bool(rent_cfg.get("precios_tienda_incluyen_iva", True))
    estrategia = rent_cfg.get("estrategia", "mediana")
    if estrategia not in ESTRATEGIAS:
        estrategia = "mediana"
    flete = float(rent_cfg.get("flete_estimado_clp", 0))
    comision_pct = float(rent_cfg.get("costo_transaccion_pct", 0))
    margen_rentable = float(rent_cfg.get("margen_rentable", 0.10))
    cobertura_minima = float(rent_cfg.get("cobertura_minima", 0.6))
    umbral_competencia = float(rent_cfg.get("alerta_competencia_pct", 0.90))
    usar_cotizaciones_api = bool(rent_cfg.get("usar_cotizaciones_api", True))
    margen_proveedor = float(rent_cfg.get("margen_proveedor_estimado", 0.15))
    descuento_ganar = float(rent_cfg.get("descuento_para_ganar", 0.02))

    monto_clp = _a_float(compra.get("monto_disponible_clp")
                         or compra.get("monto_disponible"), None)
    ingreso_neto = monto_clp if monto_es_neto else _sin_iva(monto_clp, iva) if monto_clp else None

    items: List[Dict] = []
    costo_adquisicion = 0.0
    items_con_precio = 0
    productos = compra.get("productos") or []

    for producto in productos:
        nombre_norm = producto.get("nombre") or ""
        datos = resultados_precio.get(nombre_norm) or {}
        precios: List[Dict] = datos.get("precios") or []
        valores = [p["precio_clp"] for p in precios if p.get("precio_clp", 0) > 0]

        # valores vienen con IVA incluido -> se lleva a neto
        netos = [_sin_iva(v, iva) if precios_con_iva else v for v in valores]
        estimado_unit = _agregar(netos, estrategia) if netos else None
        cantidad = _a_float(producto.get("cantidad"), 1.0) or 1.0

        total_linea = None
        if estimado_unit is not None:
            total_linea = estimado_unit * cantidad
            costo_adquisicion += total_linea
            items_con_precio += 1

        items.append(
            {
                "nombre": nombre_norm,
                "descripcion": producto.get("descripcion"),
                "cantidad": cantidad,
                "unidad": producto.get("unidad"),
                "precio_estimado_unit_neto": _redondear(estimado_unit),
                "total_linea_neto": _redondear(total_linea),
                "precios_encontrados": len(valores),
                "fuentes": [
                    {
                        "fuente": p.get("fuente"),
                        "titulo": p.get("titulo"),
                        "precio_clp": p.get("precio_clp"),
                        "url": p.get("url"),
                    }
                    for p in precios[:6]
                ],
                "query_busqueda": datos.get("query"),
                "errores_busqueda": datos.get("errores") or [],
            }
        )

    total_productos = len(productos)
    cobertura = (items_con_precio / total_productos) if total_productos else 0.0

    # Competencia: mejor cotización neta ya registrada en la CA (dato de la API).
    mejor_cot = _a_float(compra.get("mejor_cotizacion_neta"), None)

    costo_total = costo_adquisicion * (1 + comision_pct) + flete
    utilidad = None
    margen = None
    veredicto = "SIN DATOS"
    fuente_estimacion = "tiendas"

    tiene_monto = ingreso_neto is not None and ingreso_neto > 0
    if total_productos and tiene_monto and cobertura >= cobertura_minima:
        utilidad = ingreso_neto - costo_total
        margen = utilidad / ingreso_neto
        if margen >= margen_rentable:
            veredicto = "RENTABLE"
        elif margen >= 0:
            veredicto = "MARGINAL"
        else:
            veredicto = "NO RENTABLE"

    # Modo alternativo SIN scraping: estimar con la cotización de la
    # competencia. SoloTodo (y el scraping en general) solo cubre productos de
    # tienda; los rubros típicos de Compra Ágil (impresión, insumos médicos,
    # aseo, servicios) quedan sin precio. Cuando hay proveedores que ya
    # cotizaron en la CA, su mejor cotización neta es el mejor proxy
    # disponible del mercado:
    #     costo estimado    = mejor_cot / (1 + margen_proveedor_estimado)
    #                         (la proveedora también suma su margen sobre su costo)
    #     ingreso al ganar  = min(monto_disponible, mejor_cot * (1 - descuento))
    #                         (para adjudicarse hay que quedar bajo la mejor)
    #     utilidad          = ingreso_ganador - costo_estimado - flete
    # Es una heurística conservadora y configurable en config.yaml.
    if (
        usar_cotizaciones_api
        and total_productos
        and tiene_monto
        and cobertura < cobertura_minima
        and mejor_cot and mejor_cot > 0
    ):
        fuente_estimacion = "cotizacion_competencia"
        costo_estimado = mejor_cot / (1 + margen_proveedor)
        ingreso_ganador = min(ingreso_neto, mejor_cot * (1 - descuento_ganar))
        costo_total = costo_estimado * (1 + comision_pct) + flete
        utilidad = ingreso_ganador - costo_total
        margen = utilidad / ingreso_ganador if ingreso_ganador else None
        if margen is not None:
            if margen >= margen_rentable:
                veredicto = "RENTABLE"
            elif margen >= 0:
                veredicto = "MARGINAL"
            else:
                veredicto = "NO RENTABLE"

    # Alerta de competencia: solo tiene sentido en modo tiendas (en modo
    # cotización, la cotización ES la fuente, no un riesgo adicional).
    riesgo_competencia = False
    if fuente_estimacion == "tiendas" and mejor_cot and costo_adquisicion and costo_total:
        riesgo_competencia = mejor_cot <= costo_total * umbral_competencia

    return {
        "veredicto": veredicto,
        "fuente_estimacion": fuente_estimacion,
        "margen": _redondear(margen, 4),
        "utilidad_estimada": _redondear(utilidad),
        "ingreso_neto": _redondear(ingreso_neto),
        "costo_adquisicion_neto": _redondear(costo_adquisicion),
        "flete_estimado": _redondear(flete),
        "costo_total_neto": _redondear(costo_total),
        "cobertura_precios": _redondear(cobertura, 2),
        "items_con_precio": items_con_precio,
        "total_items": total_productos,
        "estrategia_precio": estrategia,
        "iva_usado": iva,
        "riesgo_competencia": riesgo_competencia,
        "mejor_cotizacion_competencia": _redondear(mejor_cot),
        "items": items,
    }


# --------------------------------------------------------------------- utils
def _sin_iva(valor: Optional[float], iva: float) -> Optional[float]:
    if valor is None:
        return None
    return valor / (1 + iva)


def _agregar(valores: List[float], estrategia: str) -> float:
    """Agrega precios netos según la estrategia elegida."""
    ordenados = sorted(valores)
    n = len(ordenados)
    if estrategia == "minimo":
        return ordenados[0]
    if estrategia == "promedio":
        return sum(ordenados) / n
    if estrategia == "percentil_25":
        indice = max(0, math.ceil(0.25 * n) - 1)
        return ordenados[indice]
    # mediana
    medio = n // 2
    if n % 2:
        return ordenados[medio]
    return (ordenados[medio - 1] + ordenados[medio]) / 2


def _a_float(valor, default=None):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


def _redondear(valor, decimales=0):
    if valor is None:
        return None
    return round(float(valor), decimales)
