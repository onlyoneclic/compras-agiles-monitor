"""Cliente de la API oficial de Compra Ágil v2 (api2.mercadopublico.cl).

Documentación oficial (ChileCompra, mayo 2026):
  https://www.chilecompra.cl/api/
  http://www.chilecompra.cl/wp-content/uploads/2026/05/Documentacion_API_Compra_Agil.pdf

COMPORTAMIENTO REAL de la API en producción (verificado nov-2026, difiere del PDF):
  - El parámetro de tamaño de página se llama `tamano_pagina` (SIN la "i"),
    aunque el PDF dice `tamanio_pagina` (errata). El nombre del PDF provoca
    NOK "PARAMETROS_DESCONOCIDOS: Parámetros de consulta desconocidos".
  - La API rechaza con NOK 400 "Parámetros de consulta inválidos" valores o
    combinaciones que no acepta (p. ej. tamano_pagina=5, o una consulta
    nacional sin `region`). Los clientes probados en producción recorren las
    16 regiones y deduplican en vez de consultar nacional.
  - Por eso `listar()` auto-diagnostica en su primera llamada y usa el primer
    modo que la API acepta:
      1) la consulta pedida tal cual;
      2) igual pero con tamano_pagina=50;
      3) modo región: 16 consultas (1..16), 2 páginas por región, deduplicando.
    El modo elegido queda registrado en el log de la corrida.
  - Autenticación: header HTTP `ticket` (gratuito en https://www.chilecompra.cl/api/).
  - La API exige un intervalo mínimo (~1 s) entre consultas: sin él responde
    "peticiones simultáneas" o 429. `_get()` lo respeta.
  - El WAF rechaza peticiones sin User-Agent (403).

Estructura de respuesta exitosa:
  {"success": "OK", "trace": ..., "payload": {...}, "errors": null}

Errores relevantes:
  401/403 -> ticket inválido/inactivo  (TicketInvalido)
  429     -> cuota diaria agotada      (CuotaAgotada, respeta Retry-After)
  5xx/503 -> error del servidor        (reintenta con backoff)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL_DEFAULT = "https://api2.mercadopublico.cl"

# Parámetros que la API real acepta en GET /v2/compra-agil (nov-2026).
# Cualquier otro nombre recibe NOK PARAMETROS_DESCONOCIDOS.
PARAMETROS_ACEPTADOS = {"estado", "tamano_pagina", "numero_pagina", "region", "q", "id"}

REGIONES_DE_CHILE = tuple(range(1, 17))


class MPApiError(Exception):
    """Error genérico de la API de Compra Ágil."""


class TicketInvalido(MPApiError):
    """El ticket no existe, está inactivo o no tiene permisos (401/403)."""


class CuotaAgotada(MPApiError):
    """Se alcanzó el límite diario de solicitudes del ticket (429)."""


def _es_error_de_parametros(exc: Exception) -> bool:
    """¿El error es por nombres/valores de parámetros (y no por red/cuota)?"""
    txt = str(exc)
    return any(
        marca in txt
        for marca in ("400", "PARAMETROS", "inválid", "desconocid")
    )


class CompraAgilClient:
    """Cliente HTTP para los endpoints /v2/compra-agil y /v2/compra-agil/{codigo}."""

    def __init__(
        self,
        ticket: str,
        base_url: str = BASE_URL_DEFAULT,
        timeout: int = 30,
        reintentos: int = 2,
        intervalo_ms: int = 1100,
    ):
        if not ticket:
            raise ValueError(
                "Falta el ticket de la API. Defina la variable de entorno MP_TICKET "
                "(solicítelo gratis en https://www.chilecompra.cl/api/)."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.reintentos = reintentos
        self.intervalo_ms = max(int(intervalo_ms), 0)
        self._ultima_consulta = 0.0  # monotonic de la última petición hecha
        self._modo: Optional[str] = None    # None | "nacional" | "region"
        self._tamanio_fijo: Optional[int] = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "ticket": ticket,
                "User-Agent": "cam-monitor/0.1 (uso responsable; contact: ver README)",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ base
    def _get(self, ruta: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET con manejo de errores y backoff. Devuelve el payload."""
        url = "%s%s" % (self.base_url, ruta)
        intento = 0
        ultimo_error: Optional[Exception] = None
        while intento <= self.reintentos:
            if intento:
                time.sleep(min(2 ** intento * 2, 10))  # 2s, 4s, 8s...
            # Intervalo mínimo entre consultas: la API rechaza ráfagas con
            # "peticiones simultáneas" o 429 (requiere ~1 req/segundo).
            espera = self.intervalo_ms / 1000.0 - (time.monotonic() - self._ultima_consulta)
            if espera > 0:
                time.sleep(espera)
            self._ultima_consulta = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                ultimo_error = MPApiError("Error de red: %s" % exc)
                intento += 1
                continue

            if resp.status_code in (401, 403):
                raise TicketInvalido(_mensaje_error(resp))
            if resp.status_code == 429:
                reintentar_despues = resp.headers.get("Retry-After")
                raise CuotaAgotada(
                    "%s (Retry-After: %s)"
                    % (_mensaje_error(resp), reintentar_despues or "después de medianoche")
                )
            if resp.status_code >= 500:
                ultimo_error = MPApiError(
                    "HTTP %d en %s: %s" % (resp.status_code, ruta, resp.text[:200])
                )
                intento += 1
                continue
            if resp.status_code == 404:
                raise MPApiError("No existe el recurso solicitado (404): %s" % ruta)
            try:
                data = resp.json()
            except ValueError as exc:
                raise MPApiError("Respuesta no-JSON (HTTP %d): %s" % (resp.status_code, exc))

            if data.get("success") != "OK":
                errores = data.get("errors") or []
                if errores:
                    primero = errores[0]
                    codigo = str(primero.get("codigo", "?"))
                    partes = [str(primero.get("mensaje") or "sin detalle")]
                    if primero.get("detalle"):
                        partes.append("detalle: %s" % primero["detalle"])
                    if data.get("trace"):
                        partes.append("trace: %s" % data["trace"])
                    mensaje = " | ".join(partes)
                else:
                    codigo, mensaje = "?", "sin detalle"
                if codigo in ("401", "403"):
                    raise TicketInvalido(mensaje)
                raise MPApiError("API respondió NOK (%s): %s" % (codigo, mensaje))
            return data.get("payload") or {}

        raise ultimo_error or MPApiError("Error desconocido en %s" % ruta)

    # -------------------------------------------------------------- endpoints
    def listar(
        self,
        filtros: Optional[Dict[str, Any]] = None,
        max_paginas: Optional[int] = None,
        tamanio_pagina: int = 50,
    ) -> List[Dict[str, Any]]:
        """Itera /v2/compra-agil hasta agotar total_paginas (o max_paginas).

        `tamanio_pagina` (nombre interno) se envía como `tamano_pagina`, que es
        el nombre real que acepta la API (errata del PDF oficial). Máximo 50.
        Solo se envían parámetros probados; el resto se omite con un aviso.

        En la primera llamada auto-diagnostica el modo que la API acepta
        (nacional, nacional con página 50, o recorrido por regiones).
        """
        filtros = dict(filtros or {})
        tamanio = int(filtros.pop("tamanio_pagina", 0) or tamanio_pagina)
        tamanio = min(max(tamanio, 1), 50)
        estado = filtros.get("estado", "publicada")
        region = filtros.get("region")
        texto = filtros.get("q")
        identificador = filtros.get("id")

        omitidos = sorted(
            set(filtros) - {"estado", "region", "q", "id", "tamanio_pagina"}
        )
        if omitidos:
            logger.warning(
                "Parámetros omitidos en el listado (la API real los rechaza con "
                "PARAMETROS_DESCONOCIDOS): %s",
                ", ".join(omitidos),
            )

        if self._tamanio_fijo and not texto and not identificador:
            tamanio = self._tamanio_fijo

        # Consultas acotadas (region/q/id) o modo nacional ya validado: directo.
        if region or texto or identificador or self._modo == "nacional":
            query: Dict[str, Any] = {
                "estado": estado,
                "tamano_pagina": tamanio,
                "numero_pagina": 1,
            }
            if region:
                query["region"] = region
            if texto:
                query["q"] = texto
            if identificador:
                query["id"] = identificador
            return self._paginar(query, max_paginas)

        if self._modo == "region":
            return self._listar_por_regiones(estado, tamanio, max_paginas)

        # Primera consulta nacional del proceso: sondeo escalonado.
        query = {"estado": estado, "tamano_pagina": tamanio, "numero_pagina": 1}
        try:
            items = self._paginar(query, max_paginas)
            self._modo = "nacional"
            logger.info("Modo de consulta validado: nacional (tamano_pagina=%d).", tamanio)
            return items
        except MPApiError as exc:
            if not _es_error_de_parametros(exc):
                raise
            logger.warning("Consulta nacional rechazada por la API (%s).", exc)

        if tamanio != 50:
            try:
                items = self._paginar(
                    {"estado": estado, "tamano_pagina": 50, "numero_pagina": 1},
                    max_paginas,
                )
                self._modo = "nacional"
                self._tamanio_fijo = 50
                logger.info(
                    "La API rechazó tamano_pagina=%d pero acepta 50: se usará 50.",
                    tamanio,
                )
                return items
            except MPApiError as exc2:
                if not _es_error_de_parametros(exc2):
                    raise
                logger.warning(
                    "Nacional con tamano_pagina=50 también rechazada (%s).", exc2
                )

        self._modo = "region"
        logger.info(
            "La API exige filtro por región: recorriendo las 16 regiones "
            "(2 páginas por región, deduplicando)."
        )
        return self._listar_por_regiones(estado, 50, max_paginas)

    def detalle(self, codigo: str) -> Dict[str, Any]:
        """Detalle completo de una Compra Ágil: productos, proveedores, montos."""
        return self._get("/v2/compra-agil/%s" % codigo)

    # --------------------------------------------------------------- internos
    def _paginar(self, query: Dict[str, Any], max_paginas: Optional[int]) -> List[Dict[str, Any]]:
        """Itera páginas de una consulta hasta agotar total_paginas o max_paginas."""
        items: List[Dict[str, Any]] = []
        while True:
            payload = self._get("/v2/compra-agil", query)
            items.extend(payload.get("items") or [])
            paginacion = payload.get("paginacion") or {}
            total_paginas = int(paginacion.get("total_paginas") or 1)
            numero_pagina = int(paginacion.get("numero_pagina") or query["numero_pagina"])
            if numero_pagina >= total_paginas:
                break
            if max_paginas is not None and numero_pagina >= max_paginas:
                break
            query["numero_pagina"] = numero_pagina + 1
        return items

    def _listar_por_regiones(
        self, estado: str, tamanio: int, max_paginas: Optional[int]
    ) -> List[Dict[str, Any]]:
        """Recorre las 16 regiones (patrón de los clientes probados en producción)."""
        por_region = max(1, min(2, max_paginas or 2))
        items: List[Dict[str, Any]] = []
        vistos = set()
        ultimo_error: Optional[MPApiError] = None
        for region in REGIONES_DE_CHILE:
            query: Dict[str, Any] = {
                "estado": estado,
                "tamano_pagina": tamanio,
                "numero_pagina": 1,
                "region": region,
            }
            try:
                while True:
                    payload = self._get("/v2/compra-agil", query)
                    for item in payload.get("items") or []:
                        codigo = item.get("codigo")
                        if codigo and codigo in vistos:
                            continue
                        vistos.add(codigo)
                        items.append(item)
                    paginacion = payload.get("paginacion") or {}
                    total_paginas = int(paginacion.get("total_paginas") or 1)
                    actual = int(paginacion.get("numero_pagina") or query["numero_pagina"])
                    if actual >= total_paginas or actual >= por_region:
                        break
                    query["numero_pagina"] = actual + 1
            except MPApiError as exc:
                logger.warning("Región %d falló (se continúa): %s", region, exc)
                ultimo_error = exc
                continue
        if not items and ultimo_error is not None:
            raise ultimo_error
        logger.info("Listado por regiones: %d Compras Ágiles únicas.", len(items))
        return items


def _mensaje_error(resp: requests.Response) -> str:
    """Extrae el mensaje legible de una respuesta de error de la API.

    Incluye `detalle` y `trace` cuando la API los entrega, para poder
    diagnosticar rechazos de parámetros desde el log de Actions.
    """
    try:
        data = resp.json()
        errores = data.get("errors") or []
        if errores:
            primero = errores[0]
            partes = [str(primero.get("mensaje") or resp.text[:200])]
            if primero.get("detalle"):
                partes.append("detalle: %s" % primero["detalle"])
            if data.get("trace"):
                partes.append("trace: %s" % data["trace"])
            return " | ".join(partes)
    except ValueError:
        pass
    return "HTTP %d: %s" % (resp.status_code, resp.text[:200])
