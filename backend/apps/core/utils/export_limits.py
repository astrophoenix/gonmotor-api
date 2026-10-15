"""
Límite de filas de los exportes de listados (PDF/Excel).

Los servicios de exporte construyen el archivo completo en memoria; sin tope,
un listado de decenas de miles de registros agota la memoria o el timeout de
la instancia. Por encima del máximo se responde 400 pidiendo aplicar filtros.
"""
from typing import Iterable

from django.conf import settings
from django.db.models import QuerySet
from rest_framework.exceptions import APIException

MAXIMOS_POR_DEFECTO = {'pdf': 5000, 'excel': 20000}
NOMBRES_FORMATO = {'pdf': 'PDF', 'excel': 'Excel'}


def _miles(numero: int) -> str:
    return f'{numero:,}'.replace(',', '.')


def maximo_filas(formato: str) -> int:
    return getattr(settings, f'EXPORT_MAX_FILAS_{formato.upper()}', MAXIMOS_POR_DEFECTO[formato])


class ExporteDemasiadoGrande(APIException):
    status_code = 400
    default_code = 'exporte_demasiado_grande'

    def __init__(self, total: int, maximo: int, formato: str):
        super().__init__(
            f'El listado tiene {_miles(total)} registros y el máximo para exportar a '
            f'{NOMBRES_FORMATO[formato]} es {_miles(maximo)}. Aplica filtros '
            '(fechas, estado, taller…) para reducirlo.'
        )


def validar_limite_exporte(total: int, formato: str) -> None:
    """Lanza ``ExporteDemasiadoGrande`` si ``total`` supera el máximo del formato."""
    maximo = maximo_filas(formato)
    if total > maximo:
        raise ExporteDemasiadoGrande(total, maximo, formato)


def contar_filas(datos: Iterable) -> int:
    return datos.count() if isinstance(datos, QuerySet) else len(datos)


def iterar_filas(datos: Iterable, chunk_size: int = 1000) -> Iterable:
    """Recorre un queryset por bloques (respeta ``prefetch_related``) sin cachearlo entero."""
    return datos.iterator(chunk_size=chunk_size) if isinstance(datos, QuerySet) else datos
