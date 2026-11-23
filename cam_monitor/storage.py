"""Persistencia de corridas: data/runs/<fecha_hora>/ y data/latest.json.

Estructura por corrida:
    publicadas.json       listado normalizado de CAs vigentes (crudo del paso 1)
    analizado.json        resultado completo (CAs + pricing + rentabilidad)
    dashboard_data.json   datos ya recortados para el dashboard web

data/latest.json apunta a la última corrida; el dashboard y el historial la usan.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class Almacen:
    def __init__(self, dir_datos: str):
        self.dir_datos = dir_datos
        self.dir_runs = os.path.join(dir_datos, "runs")

    # ------------------------------------------------------------------ run
    def nueva_corrida(self) -> str:
        """Crea el directorio de la corrida y lo devuelve."""
        sello = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        ruta = os.path.join(self.dir_runs, sello)
        os.makedirs(ruta, exist_ok=True)
        self.ruta_corrida = ruta
        return ruta

    def guardar(self, nombre: str, datos) -> str:
        ruta = os.path.join(getattr(self, "ruta_corrida", self.dir_datos), nombre)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, ensure_ascii=False, indent=1)
        logger.info("Guardado %s", ruta)
        return ruta

    def leer(self, ruta: str):
        with open(ruta, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def marcar_ultima(self, ruta_corrida: str, resumen: Dict) -> str:
        puntero = {
            "corrida": os.path.basename(ruta_corrida),
            "ruta": ruta_corrida,
            "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "resumen": resumen,
        }
        ruta = os.path.join(self.dir_datos, "latest.json")
        os.makedirs(self.dir_datos, exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(puntero, fh, ensure_ascii=False, indent=1)
        return ruta

    def ultima_corrida(self):
        """Devuelve la ruta de la última corrida (o None)."""
        puntero_ruta = os.path.join(self.dir_datos, "latest.json")
        if os.path.isfile(puntero_ruta):
            try:
                with open(puntero_ruta, "r", encoding="utf-8") as fh:
                    return json.load(fh).get("ruta")
            except (OSError, ValueError):
                return None
        return None

    # ------------------------------------------------------------- historial
    def actualizar_historial(self, resumen_corrida: Dict, dias: int = 60) -> List[Dict]:
        """Agrega el resumen de la corrida a data/historial.json (podado a N días)."""
        ruta = os.path.join(self.dir_datos, "historial.json")
        historial: List[Dict] = []
        if os.path.isfile(ruta):
            try:
                with open(ruta, "r", encoding="utf-8") as fh:
                    historial = json.load(fh)
            except (OSError, ValueError):
                historial = []
        historial.append(resumen_corrida)
        historial = historial[-dias:]
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(historial, fh, ensure_ascii=False, indent=1)
        return historial

    # ------------------------------------------------- análisis previos (store)
    def cargar_analisis_previos(self) -> Dict[str, Dict]:
        """Lee data/analisis_previos.json ({codigo: entrada}).

        Cada entrada guarda lo caro de re-obtener: detalle de productos,
        resultados de precios y veredicto. Si el archivo no existe o está
        corrupto se parte con {} (la corrida analiza todo).
        """
        ruta = os.path.join(self.dir_datos, "analisis_previos.json")
        if not os.path.isfile(ruta):
            return {}
        try:
            with open(ruta, "r", encoding="utf-8") as fh:
                datos = json.load(fh)
            return datos if isinstance(datos, dict) else {}
        except (OSError, ValueError) as exc:
            logger.warning("analisis_previos.json ilegible, se ignora: %s", exc)
            return {}

    def guardar_analisis_previos(self, previos: Dict[str, Dict], demo: bool) -> int:
        """Fusiona y poda el store incremental, y lo persiste.

        Podado automático:
            - CAs cerradas (fecha_cierre en el pasado): ya no pueden aparecer
              como vigentes, no vuelven a consumir cuota.
            - Entradas del otro modo (demo vs real) para no cruzar datasets.
        """
        ahora = datetime.now(timezone.utc)
        limpio: Dict[str, Dict] = {}
        for codigo, entrada in previos.items():
            if bool(entrada.get("demo", False)) != bool(demo):
                continue
            cierre = _parse_iso(entrada.get("fecha_cierre"))
            if cierre is not None and cierre < ahora:
                continue
            limpio[codigo] = entrada
        ruta = os.path.join(self.dir_datos, "analisis_previos.json")
        os.makedirs(self.dir_datos, exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(limpio, fh, ensure_ascii=False, indent=1)
        logger.info("Store incremental: %d CAs reutilizables -> %s", len(limpio), ruta)
        return len(limpio)

    # --------------------------------------------------------------- limpieza
    def podar_corridas(self, conservar: int = 30) -> None:
        """Borra corridas viejas para no llenar el disco/repo."""
        if not os.path.isdir(self.dir_runs):
            return
        corridas = sorted(os.listdir(self.dir_runs))
        for vieja in corridas[:-conservar]:
            shutil.rmtree(os.path.join(self.dir_runs, vieja), ignore_errors=True)


def _parse_iso(valor) -> Optional[datetime]:
    """Convierte fechas ISO de la API a datetime aware (UTC si no trae zona)."""
    if not valor:
        return None
    texto = str(valor).strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def resumen_corrida(compras: List[Dict], meta: Dict) -> Dict:
    """Construye el resumen KPI de una corrida (para historial y dashboard)."""
    conteo: Dict[str, int] = {}
    for c in compras:
        v = c.get("analisis", {}).get("veredicto", "SIN DATOS")
        conteo[v] = conteo.get(v, 0) + 1
    rentables = [c for c in compras
                 if c.get("analisis", {}).get("veredicto") == "RENTABLE"]
    return {
        **meta,
        "total": len(compras),
        "por_veredicto": conteo,
        "monto_total_disponible": sum(
            c.get("monto_disponible_clp") or 0 for c in compras
        ),
        "monto_rentable": sum(
            c.get("monto_disponible_clp") or 0 for c in rentables
        ),
        "utilidad_potencial": sum(
            c.get("analisis", {}).get("utilidad_estimada") or 0 for c in rentables
        ),
    }
