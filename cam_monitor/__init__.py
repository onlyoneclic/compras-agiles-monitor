"""
cam_monitor — Monitor de Compras Ágiles de Mercado Público (Chile).

Extrae diariamente las Compras Ágiles vigentes (estado `publicada`) usando la
API oficial de Compra Ágil v2, estima el costo de adquisición de los productos
consultando tiendas nacionales, calcula la rentabilidad de adjudicarse cada
compra y publica los resultados en un dashboard web autocontenido.

Módulos:
    config         Carga de configuración (config.yaml + variables de entorno).
    mp_client      Cliente de la API Compra Ágil v2 (api2.mercadopublico.cl).
    ingest         Ingesta de compras vigentes + enriquecimiento con detalle.
    pricing        Búsqueda de precios en tiendas nacionales (proveedores).
    profitability  Motor de rentabilidad (IVA, flete, comisión, margen).
    storage        Persistencia de corridas (data/runs/..., historial).
    dashboard      Generación del dashboard web (HTML autocontenido).
    demo           Dataset de demostración (sin ticket, para probar todo).
    cli            Interfaz de línea de comandos.
"""

__version__ = "0.1.0"
