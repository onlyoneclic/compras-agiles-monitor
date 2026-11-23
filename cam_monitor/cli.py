"""CLI del monitor: orquesta ingesta -> precios -> rentabilidad -> dashboard.

Comandos:
    test   Verifica el ticket de la API (solicítelo en chilecompra.cl/api).
    run    Corrida completa con datos reales (requiere MP_TICKET).
    demo   Corrida completa con datos sintéticos (no requiere ticket).
    web    Regenera el dashboard desde la última corrida.

Ejemplos:
    export MP_TICKET=TU_TICKET
    python -m cam_monitor test
    python -m cam_monitor run --max-ca 20
    python -m cam_monitor demo

Procesamiento incremental: las CAs ya evaluadas en corridas anteriores NO se
vuelven a consultar (ni detalle ni precios); solo se analizan las nuevas.
Use --reanalizar para forzar la reevaluación completa de todas.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict

from .config import cargar_config
from .dashboard import construir_datos_dashboard, generar_html
from .ingest import enriquecer_con_detalle, obtener_vigentes
from .mp_client import CompraAgilClient, MPApiError, TicketInvalido
from .pricing import MotorPrecios
from .pricing.base import normalizar_query
from .profitability import analizar_compra
from .storage import Almacen, resumen_corrida

logger = logging.getLogger("cam_monitor")

FORMATO_LOG = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


# --------------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cam_monitor",
        description="Monitor de rentabilidad de Compras Ágiles de Mercado Público (Chile).",
    )
    parser.add_argument("comando", choices=["test", "run", "demo", "web"],
                        help="test | run | demo | web")
    parser.add_argument("--config", default=None, help="Ruta alternativa a config.yaml")
    parser.add_argument("--max-ca", type=int, default=None,
                        help="Máximo de CAs a enriquecer con detalle (sobrescribe config).")
    parser.add_argument("--sin-cache", action="store_true",
                        help="Ignorar la caché de precios de esta corrida.")
    parser.add_argument("--reanalizar", action="store_true",
                        help="Reanalizar TODAS las CAs aunque ya estuvieran evaluadas.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG, stream=sys.stderr)

    try:
        config = cargar_config(args.config)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    if args.max_ca is not None:
        config._data.setdefault("mercado_publico", {})["max_detalles"] = args.max_ca

    if args.comando == "test":
        return _cmd_test(config)
    if args.comando == "demo":
        return _cmd_corrida(config, demo=True, sin_cache=args.sin_cache,
                            reanalizar=args.reanalizar)
    if args.comando == "run":
        return _cmd_corrida(config, demo=False, sin_cache=args.sin_cache,
                            reanalizar=args.reanalizar)
    if args.comando == "web":
        return _cmd_web(config)
    return 0


# --------------------------------------------------------------------- test
def _cmd_test(config) -> int:
    ticket = config.ticket
    if not ticket:
        logger.error(
            "MP_TICKET no definido. Solicite su ticket en https://www.chilecompra.cl/api/ "
            "y luego: export MP_TICKET=..."
        )
        return 1
    try:
        client = CompraAgilClient(ticket, base_url=config.get("mercado_publico.base_url"))
        items = client.listar(filtros={"estado": "publicada", "tamanio_pagina": 5},
                              tamanio_pagina=5, max_paginas=1)
        logger.info("Ticket OK. %d Compras Ágiles vigentes visibles (muestra).", len(items))
        for item in items[:5]:
            logger.info("  %s | %s | %s CLP",
                        item.get("codigo"), (item.get("nombre") or "")[:60],
                        (item.get("montos") or {}).get("monto_disponible_clp"))
        return 0
    except TicketInvalido as exc:
        logger.error("Ticket inválido o inactivo: %s", exc)
        return 1
    except MPApiError as exc:
        logger.error("Error de API: %s", exc)
        return 1


# ----------------------------------------------------------------- corrida
def _cmd_corrida(config, demo: bool, sin_cache: bool, reanalizar: bool = False) -> int:
    almacen = Almacen(config.dir_datos)
    ruta_corrida = almacen.nueva_corrida()
    logger.info("Corrida iniciada (%s): %s", "DEMO" if demo else "REAL", ruta_corrida)

    # 0) Store incremental: CAs ya evaluadas en corridas anteriores ----------
    #    (data/analisis_previos.json). Reutilizarlas evita gastar cuota de la
    #    API y repetir búsquedas de precios sobre lo ya revisado.
    previos_guardados = almacen.cargar_analisis_previos()
    reutilizar = bool(config.get("rentabilidad.reutilizar_analisis", True)) and not reanalizar

    # 1) Ingesta ------------------------------------------------------------
    if demo:
        compras = _compras_demo(config)
        fuente = "demo (datos sintéticos)"
    else:
        ticket = config.ticket
        if not ticket:
            logger.error("MP_TICKET no definido; ejecute 'demo' para probar sin ticket.")
            return 1
        client = CompraAgilClient(ticket, base_url=config.get("mercado_publico.base_url"))
        try:
            compras = obtener_vigentes(client, config)
        except TicketInvalido:
            logger.error("Ticket inválido. Verifíquelo en https://www.chilecompra.cl/api/")
            return 1
        except MPApiError as exc:
            logger.error("Ingesta falló: %s", exc)
            return 1
        fuente = "API Compra Ágil v2 (api2.mercadopublico.cl)"

    # 1b) Separar: nuevas vs ya evaluadas ------------------------------------
    if reutilizar:
        nuevas, reutilizadas = _separar_previos(compras, previos_guardados, demo, config)
    else:
        nuevas, reutilizadas = list(compras), []
    logger.info("Incremental: %d nuevas, %d ya evaluadas (se reutilizan)",
                len(nuevas), len(reutilizadas))

    # 1c) Detalle solo de las nuevas (cada consulta consume cuota) -----------
    if not demo:
        nuevas = enriquecer_con_detalle(client, nuevas, config)

    almacen.guardar("publicadas.json", compras)

    # 2) Pricing -------------------------------------------------------------
    #    Las ya evaluadas aportan sus resultados almacenados; solo las nuevas
    #    disparan búsquedas de precios (la caché cubre solapamientos).
    if sin_cache:
        config._data["pricing"] = {**config.seccion("pricing"),
                                   "cache_horas": 0}
    motor = MotorPrecios(config, demo=demo)
    max_items = int(config.get("pricing.max_items_por_ca", 8))

    resultados_precio: Dict[str, Dict] = {}
    for ca in reutilizadas:
        for nombre, previo_p in ((ca.get("_previo") or {}).get("resultados_precio") or {}).items():
            resultados_precio.setdefault(nombre, previo_p)

    for ca in nuevas:
        productos = (ca.get("productos") or [])[:max_items]
        for producto in productos:
            clave = producto.get("nombre") or ""
            if not clave or clave in resultados_precio:
                continue
            query = normalizar_query(clave, producto.get("descripcion"))
            resultado = motor.buscar(query)
            resultados_precio[clave] = {
                "query": query,
                "precios": [p.__dict__ for p in resultado.precios],
                "errores": resultado.errores,
            }
            logger.info("Precio '%s': %d resultados%s", query, len(resultado.precios),
                        " (errores: %d)" % len(resultado.errores) if resultado.errores else "")

    # 3) Rentabilidad (aritmética local: se recalcula siempre, sin cuota) ----
    for ca in compras:
        ca["analisis"] = analizar_compra(ca, resultados_precio, config)

    # 4) Salidas -------------------------------------------------------------
    generado = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "demo": demo,
        "fuente": fuente,
        "generado_utc": generado,
        "ruta_corrida": ruta_corrida,
        "nuevas_analizadas": len(nuevas),
        "reutilizadas": len(reutilizadas),
    }
    resumen = resumen_corrida(compras, meta)
    almacen.guardar("analizado.json", {"meta": meta, "resumen": resumen, "compras": compras})
    almacen.guardar("dashboard_data.json",
                    construir_datos_dashboard(compras, resumen, meta))
    almacen.marcar_ultima(ruta_corrida, resumen)
    almacen.actualizar_historial(resumen,
                                 dias=int(config.get("salida.historial_dias", 60)))
    almacen.podar_corridas(conservar=int(config.get("salida.conservar_corridas", 30)))

    # 4b) Guardar el store incremental (base de la próxima corrida) ----------
    for ca in compras:
        productos = ca.get("productos") or []
        if not productos:
            continue  # sin detalle (falló o quedó fuera de max_detalles): será nueva mañana
        res_ca = {}
        for producto in productos:
            nombre = producto.get("nombre") or ""
            if nombre in resultados_precio:
                res_ca[nombre] = resultados_precio[nombre]
        previos_guardados[ca["codigo"]] = {
            "codigo": ca["codigo"],
            "demo": demo,
            "guardado_utc": generado,
            "fecha_cierre": ca.get("fecha_cierre"),
            "veredicto": (ca.get("analisis") or {}).get("veredicto"),
            "productos": productos,
            "resultados_precio": res_ca,
            "mejor_cotizacion_neta": ca.get("mejor_cotizacion_neta"),
        }
    almacen.guardar_analisis_previos(previos_guardados, demo=demo)

    ruta_plantilla = config.get("salida.plantilla", "web/dashboard_template.html")
    ruta_html = config.get("salida.archivo_dashboard", "web/index.html")
    generar_html(ruta_plantilla,
                 construir_datos_dashboard(compras, resumen, meta),
                 ruta_html)

    _imprimir_resumen(resumen)
    return 0


def _compras_demo(config):
    """En demo se recorre el mismo enriquecimiento usando datos sintéticos."""
    from .demo import construir_compras_demo

    return construir_compras_demo()


def _separar_previos(compras, previos: Dict[str, Dict], demo: bool, config):
    """Reparte las CAs vigentes en (nuevas, ya evaluadas).

    Una CA se considera ya evaluada si existe en el store con el mismo modo
    (demo/real) y su veredicto es concluyente (o reintentar_sin_datos está en
    false). A las ya evaluadas se les recicla el detalle de productos y los
    resultados de precios; monto, cierre y ofertas quedan frescos del listado
    de hoy, y la aritmética se recalcula localmente (sin gastar cuota).
    """
    reintentar = bool(config.get("rentabilidad.reintentar_sin_datos", True))
    nuevas, reutilizadas = [], []
    for ca in compras:
        previo = previos.get(ca["codigo"])
        if (not previo
                or bool(previo.get("demo", False)) != bool(demo)
                or (reintentar and (previo.get("veredicto") or "SIN DATOS") == "SIN DATOS")):
            nuevas.append(ca)
            continue
        ca["productos"] = previo.get("productos") or []
        ca["mejor_cotizacion_neta"] = previo.get("mejor_cotizacion_neta")
        ca["analisis_previo_utc"] = previo.get("guardado_utc")
        ca["_previo"] = previo
        reutilizadas.append(ca)
    return nuevas, reutilizadas


def _cmd_web(config) -> int:
    """Regenera web/index.html desde la última corrida guardada."""
    almacen = Almacen(config.dir_datos)
    ruta = almacen.ultima_corrida()
    if not ruta or not os.path.isfile(os.path.join(ruta, "dashboard_data.json")):
        logger.error("No hay corridas previas. Ejecute 'run' o 'demo' primero.")
        return 1
    datos = almacen.leer(os.path.join(ruta, "dashboard_data.json"))
    generar_html(config.get("salida.plantilla", "web/dashboard_template.html"),
                 datos,
                 config.get("salida.archivo_dashboard", "web/index.html"))
    return 0


# ------------------------------------------------------------------ salida
def _imprimir_resumen(resumen: dict) -> None:
    logger.info("=== Resumen de la corrida ===")
    logger.info("Compras analizadas      : %d", resumen.get("total", 0))
    logger.info("  nuevas                : %d", resumen.get("nuevas_analizadas", 0))
    logger.info("  ya evaluadas (reuse)  : %d", resumen.get("reutilizadas", 0))
    for veredicto, cantidad in sorted((resumen.get("por_veredicto") or {}).items()):
        logger.info("  %-11s          : %d", veredicto, cantidad)
    logger.info("Monto total disponible  : $%s CLP",
                f"{resumen.get('monto_total_disponible', 0):,.0f}".replace(",", "."))
    logger.info("Monto en CAs rentables  : $%s CLP",
                f"{resumen.get('monto_rentable', 0):,.0f}".replace(",", "."))
    logger.info("Utilidad potencial      : $%s CLP",
                f"{resumen.get('utilidad_potencial', 0):,.0f}".replace(",", "."))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
