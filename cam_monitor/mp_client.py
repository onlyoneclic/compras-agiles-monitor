"""Cliente de la API oficial de Compra Ágil v2 (api2.mercadopublico.cl).

Documentación oficial (ChileCompra, mayo 2026):
  https://www.chilecompra.cl/api/
  http://www.chilecompra.cl/wp-content/uploads/2026/05/Documentacion_API_Compra_Agil.pdf

Autenticación: header HTTP `ticket` con el ticket gratuito solicitado en
https://www.chilecompra.cl/api/ (botón "Pide tu ticket", con Clave Única).

Estructura de respuesta exitosa:
  {"success": "OK", "trace": ..., "payload": {...}, "errors": null}

Errores relevantes:
  401/403 -> ticket inválido/inactivo  (TicketInvalido)
  429     -> cuota diaria agotada      (CuotaAgotada, respeta Retry-After)
  5xx/503 -> error del servidor        (reintenta con backoff)
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

BASE_URL_DEFAULT = "https://api2.mercadopublico.cl"


class MPApiError(Exception):
    """Error genérico de la API de Compra Ágil."""


class TicketInvalido(MPApiError):
    """El ticket no existe, está inactivo o no tiene permisos (401/403)."""


class CuotaAgotada(MPApiError):
    """Se alcanzó el límite diario de solicitudes del ticket (429)."""


class CompraAgilClient:
    """Cliente HTTP para los endpoints /v2/compra-agil y /v2/compra-agil/{codigo}."""

    def __init__(
        self,
        ticket: str,
        base_url: str = BASE_URL_DEFAULT,
        timeout: int = 30,
        reintentos: int = 2,
    ):
        if not ticket:
            raise ValueError(
                "Falta el ticket de la API. Defina la variable de entorno MP_TICKET "
                "(solicítelo gratis en https://www.chilecompra.cl/api/)."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.reintentos = reintentos
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
                mensaje = errores[0].get("mensaje", "?") if errores else "sin detalle"
                codigo = str(errores[0].get("codigo", "?")) if errores else "?"
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

        La API permite hasta 50 resultados por página (`tamanio_pagina`).
        """
        filtros = dict(filtros or {})
        filtros.setdefault("tamanio_pagina", min(int(tamanio_pagina), 50))
        filtros.setdefault("numero_pagina", 1)

        items: List[Dict[str, Any]] = []
        while True:
            payload = self._get("/v2/compra-agil", filtros)
            items.extend(payload.get("items") or [])
            paginacion = payload.get("paginacion") or {}
            total_paginas = int(paginacion.get("total_paginas") or 1)
            numero_pagina = int(paginacion.get("numero_pagina") or filtros["numero_pagina"])
            if numero_pagina >= total_paginas:
                break
            if max_paginas is not None and numero_pagina >= max_paginas:
                break
            filtros["numero_pagina"] = numero_pagina + 1
        return items

    def detalle(self, codigo: str) -> Dict[str, Any]:
        """Detalle completo de una Compra Ágil: productos, proveedores, montos."""
        return self._get("/v2/compra-agil/%s" % codigo)


def _mensaje_error(resp: requests.Response) -> str:
    """Extrae el mensaje legible de una respuesta de error de la API."""
    try:
        data = resp.json()
        errores = data.get("errors") or []
        if errores:
            return str(errores[0].get("mensaje") or resp.text[:200])
    except ValueError:
        pass
    return "HTTP %d: %s" % (resp.status_code, resp.text[:200])
