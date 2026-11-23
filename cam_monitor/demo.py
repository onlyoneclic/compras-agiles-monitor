"""Modo demostración: dataset sintético + proveedor de precios de ejemplo.

El modo demo permite probar TODO el pipeline (ingesta -> precios ->
rentabilidad -> dashboard) sin ticket de la API ni acceso a tiendas. Los
datos de Compras Ágiles son ficticios (códigos y organismos inventados y
marcados como demo) y los precios provienen de DemoProvider.

Esto sirve para: verificar la instalación, ajustar parámetros de rentabilidad
y ver cómo se ve el dashboard antes de configurar el ticket real.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from .pricing.base import PrecioEncontrado, PriceProvider

logger = logging.getLogger(__name__)

# Catálogo de precios de ejemplo en CLP (precio al público, IVA incluido).
CATALOGO_PRECIOS_DEMO = {
    "cuaderno": [950, 1100, 1250, 1400],
    "lapiz": [180, 250, 320],
    "boligrafo": [150, 220, 300],
    "papel bond": [3400, 3900, 4200, 4500],
    "toner": [42000, 48000, 52000],
    "tinta": [28000, 32000, 35000],
    "notebook": [329990, 349990, 379990],
    "monitor": [89990, 94990, 109990],
    "mouse": [4990, 5990, 6990],
    "teclado": [6990, 8490, 9990],
    "guantes nitrilo": [2490, 2890, 3300],
    "mascara": [890, 1150, 1400],
    "alcohol gel": [3600, 4200, 4800],
    "parlante": [19990, 24990, 29990],
    "camara": [45990, 52990, 59990],
    "silla": [54990, 62990, 69990],
    "escritorio": [89990, 97990, 109990],
    "cable": [2490, 3200, 3990],
    "impresora": [89990, 99990, 119990],
}

TIENDAS_DEMO = ["tienda-demo-uno", "tienda-demo-dos", "tienda-demo-tres"]


def precio_demo(nombre_producto: str):
    """Busca en el catálogo de demo por coincidencia de palabra clave."""
    texto = (nombre_producto or "").lower()
    for clave, valores in CATALOGO_PRECIOS_DEMO.items():
        if clave in texto:
            return clave, valores
    return None, [5000, 6500, 8000]  # genérico si no hay coincidencia


class DemoProvider(PriceProvider):
    """Proveedor de precios sintéticos (solo modo demo)."""

    nombre = "demo"

    def buscar(self, query: str) -> List[PrecioEncontrado]:
        clave, valores = precio_demo(query)
        precios = []
        for indice, valor in enumerate(valores[:3]):
            precios.append(
                PrecioEncontrado(
                    fuente="%s (%s)" % (TIENDAS_DEMO[indice % len(TIENDAS_DEMO)],
                                        clave or "generico"),
                    titulo="[DEMO] %s — oferta tienda %d" % (query, indice + 1),
                    precio_clp=int(valor),
                    url="",  # sin enlace: datos ficticios
                    disponible=True,
                )
            )
        return precios


def construir_compras_demo() -> List[dict]:
    """Crea 6 Compras Ágiles ficticias con estructura idéntica a la real."""
    ahora = datetime.now(timezone.utc)

    def cierra_en(horas: int) -> str:
        return (ahora + timedelta(hours=horas)).isoformat(timespec="seconds")

    def publicado(horas: int) -> str:
        return (ahora - timedelta(hours=horas)).isoformat(timespec="seconds")

    compras = [
        {
            "codigo": "900001-100-COT26",
            "nombre": "Adquisición de material de escritorio para oficinas regionales",
            "estado_codigo": "publicada",
            "estado_glosa": "Publicada",
            "fecha_publicacion": publicado(6),
            "fecha_cierre": cierra_en(42),
            "monto_disponible": 1450000,
            "monto_disponible_clp": 1450000,
            "organismo": "[DEMO] Servicio de Salud Ficticio",
            "region": 13,
            "region_nombre": "Metropolitana",
            "ofertas_recibidas": 3,
            "productos": [
                {"nombre": "CUADERNO UNIVERSITARIO 100 HOJAS", "cantidad": 400,
                 "unidad": "EA", "descripcion": "Cuaderno universitario tapa dura"},
                {"nombre": "PAPEL BOND OFICIO 75 GR", "cantidad": 200,
                 "unidad": "EA", "descripcion": "Caja resma papel bond"},
                {"nombre": "BOLIGRAFO TINTA AZUL", "cantidad": 500,
                 "unidad": "EA", "descripcion": None},
            ],
        },
        {
            "codigo": "900002-200-COT26",
            "nombre": "Compra ágil de insumos de impresión y toner",
            "estado_codigo": "publicada",
            "estado_glosa": "Publicada",
            "fecha_publicacion": publicado(20),
            "fecha_cierre": cierra_en(4),
            "monto_disponible": 890000,
            "monto_disponible_clp": 890000,
            "organismo": "[DEMO] Municipalidad de Ejemplo",
            "region": 5,
            "region_nombre": "Valparaíso",
            "ofertas_recibidas": 1,
            "productos": [
                {"nombre": "TONER LASER NEGRO COMPATIBLE", "cantidad": 12,
                 "unidad": "EA", "descripcion": "Toner compatible hp"},
                {"nombre": "TINTA IMPRESORA MULTIFUNCIONAL", "cantidad": 10,
                 "unidad": "EA", "descripcion": None},
            ],
        },
        {
            "codigo": "900003-300-COT26",
            "nombre": "Equipamiento computacional para personal nuevo",
            "estado_codigo": "publicada",
            "estado_glosa": "Publicada",
            "fecha_publicacion": publicado(3),
            "fecha_cierre": cierra_en(70),
            "monto_disponible": 5400000,
            "monto_disponible_clp": 5400000,
            "organismo": "[DEMO] Dirección Regional de Ficción",
            "region": 8,
            "region_nombre": "Biobío",
            "ofertas_recibidas": 0,
            "productos": [
                {"nombre": "NOTEBOOK 15 PULGADAS i5", "cantidad": 10,
                 "unidad": "EA", "descripcion": "Notebook 8GB RAM 512GB SSD"},
                {"nombre": "MOUSE INALAMBRICO", "cantidad": 10, "unidad": "EA",
                 "descripcion": None},
                {"nombre": "TECLADO USB", "cantidad": 10, "unidad": "EA",
                 "descripcion": None},
            ],
        },
        {
            "codigo": "900004-400-COT26",
            "nombre": "Insumos de protección personal para personal de terreno",
            "estado_codigo": "publicada",
            "estado_glosa": "Publicada",
            "fecha_publicacion": publicado(30),
            "fecha_cierre": cierra_en(18),
            "monto_disponible": 620000,
            "monto_disponible_clp": 620000,
            "organismo": "[DEMO] Corporación de Salud Demostración",
            "region": 9,
            "region_nombre": "Araucanía",
            "ofertas_recibidas": 5,
            "productos": [
                {"nombre": "GUANTES NITRILO TALLA M", "cantidad": 100,
                 "unidad": "EA", "descripcion": "Caja 100 unidades"},
                {"nombre": "MASCARILLA QUIRURGICA", "cantidad": 500,
                 "unidad": "EA", "descripcion": None},
                {"nombre": "ALCOHOL GEL 1 LITRO", "cantidad": 60,
                 "unidad": "EA", "descripcion": None},
            ],
        },
        {
            "codigo": "900005-500-COT26",
            "nombre": "Mobiliario de oficina para remodelación de sucursal",
            "estado_codigo": "publicada",
            "estado_glosa": "Publicada",
            "fecha_publicacion": publicado(48),
            "fecha_cierre": cierra_en(96),
            "monto_disponible": 2100000,
            "monto_disponible_clp": 2100000,
            "organismo": "[DEMO] Servicio Público Inventado",
            "region": 2,
            "region_nombre": "Antofagasta",
            "ofertas_recibidas": 2,
            "productos": [
                {"nombre": "SILLA OFICINA GIRATORIA", "cantidad": 20,
                 "unidad": "EA", "descripcion": "Silla ergonómica con ruedas"},
                {"nombre": "ESCRITORIO 120 CM", "cantidad": 20, "unidad": "EA",
                 "descripcion": None},
            ],
        },
        {
            # Esta CA está pensada para salir NO RENTABLE (monto bajo vs costos)
            "codigo": "900006-600-COT26",
            "nombre": "Compra ágil equipamiento audiovisual sala de reuniones",
            "estado_codigo": "publicada",
            "estado_glosa": "Publicada",
            "fecha_publicacion": publicado(10),
            "fecha_cierre": cierra_en(30),
            "monto_disponible": 380000,
            "monto_disponible_clp": 380000,
            "organismo": "[DEMO] Organismo de Prueba",
            "region": 13,
            "region_nombre": "Metropolitana",
            "ofertas_recibidas": 4,
            "productos": [
                {"nombre": "PARLANTE BLUETOOTH 20W", "cantidad": 6,
                 "unidad": "EA", "descripcion": None},
                {"nombre": "CAMARA WEB FULL HD", "cantidad": 6,
                 "unidad": "EA", "descripcion": None},
                {"nombre": "CABLE HDMI 2 METROS", "cantidad": 10,
                 "unidad": "EA", "descripcion": None},
            ],
        },
    ]
    logger.info("Dataset demo construido: %d compras ficticias", len(compras))
    return compras
