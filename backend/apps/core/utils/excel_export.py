"""
Exportación de Excel (.xlsx) con openpyxl siguiendo LAS MISMAS BUENAS
PRÁCTICAS del exportador PDF (``pdf_export.py``):

REUTILIZACIÓN DE CABECERA
  * La identidad se obtiene de ``pdf_export._datos_identidad`` (mismo origen de
    datos): NOMBRE de la EMPRESA en NEGRILLA (no del taller) y líneas con las
    etiquetas "RUC:", "Dir:" y "Telf:" en gris #555555.
  * A la derecha, alineada con la identidad, va "Generado: …" y "Usuario: …"
    (también en gris #555555), igual que en la página 1 del PDF.
  * Título del reporte en NEGRILLA negro (puro) y, opcionalmente, un metadato
    debajo (p. ej. "Número total de Clientes: X").

DIFERENCIAS INTENCIONADAS CON EL PDF
  * NO hay línea divisoria horizontal entre la cabecera y el título del reporte
    (a petición expresa).
  * No hay paginación de hojas: la cabecera de identidad se escribe una sola vez
    al inicio (no existen cabeceras slim de "Continuación").

TABLA
  * Las líneas que forman la tabla SÍ existen: bordes finos en el MISMO gris
    asulado #D7E0E9 que el PDF, y el fondo de los campos del encabezado usa ese
    mismo color con texto negro en NEGRILLA.
  * Alineación por columna configurable con la MISMA regla para cabecera y datos
    (opcional; por defecto a la izquierda).
  * Paleta HEX idéntica a ``pdf_export``.

API PÚBLICA
  * ``ExcelExportConfig`` + ``ExcelExportService``: espejo de
    ``PdfExportConfig``/``PdfExportService`` para las pantallas (Clientes,
    Vehículos, Configuración, Inventario, Órdenes). Devuelve un ``HttpResponse``
    con el .xlsx listo para Django REST Framework.
"""

from io import BytesIO
from typing import Iterable, List, Optional, Tuple

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .pdf_export import _datos_identidad

# ---------------------------------------------------------------------------
# PALETA IDÉNTICA A pdf_export (HEX, sin '#')
# ---------------------------------------------------------------------------
GRIS_IDENTIDAD = '555555'      # RUC: / Dir: / Telf: / Generado / Usuario
GRIS_OSCURO_TEXTO = '111827'   # texto de los datos
GRIS_ASULADO = 'D7E0E9'        # líneas de la tabla + fondo de la cabecera
NEGRO = '000000'               # títulos / campos del header

FUENTE_DATOS = Font(name='Calibri', size=10, color=GRIS_OSCURO_TEXTO)
FUENTE_SECUNDARIO = Font(name='Calibri', size=9, color=GRIS_IDENTIDAD)
FUENTE_EMPRESA = Font(name='Calibri', bold=True, size=12, color=GRIS_OSCURO_TEXTO)
FUENTE_TITULO = Font(name='Calibri', bold=True, size=13, color=NEGRO)
FUENTE_METADATO = Font(name='Calibri', size=10, color=GRIS_OSCURO_TEXTO)
FUENTE_CABECERA = Font(name='Calibri', bold=True, size=10.5, color=NEGRO)

BORDE_TABLA = Border(
    left=Side(style='thin', color=GRIS_ASULADO),
    right=Side(style='thin', color=GRIS_ASULADO),
    top=Side(style='thin', color=GRIS_ASULADO),
    bottom=Side(style='thin', color=GRIS_ASULADO),
)

RELLENO_CABECERA = PatternFill(start_color=GRIS_ASULADO, end_color=GRIS_ASULADO, fill_type='solid')

ALINEACION_DERECHA = Alignment(horizontal='right', vertical='center', wrap_text=True)
ALINEACION_IZQUIERDA = Alignment(horizontal='left', vertical='center', wrap_text=True)


def _estilo_celda(alineacion: str) -> Alignment:
    """Alignment para una columna ('center', 'left' o 'right')."""
    return Alignment(horizontal=alineacion, vertical='center', wrap_text=True)


class ExcelExportConfig:
    """Configuración de un reporte genérico (espejo de ``PdfExportConfig``)."""

    def __init__(
        self,
        title: str,
        filename: str,
        headers: List[Tuple[str, float]],
        row_builder=None,
        empresa=None,
        taller=None,
        usuario: Optional[str] = None,
        metadata: Optional[str] = None,
        alignments: Optional[List[str]] = None,
    ):
        self.title = title
        self.filename = filename
        self.headers = headers
        self.row_builder = row_builder
        self.empresa = empresa
        self.taller = taller
        self.usuario = usuario
        self.metadata = metadata
        self.alignments = alignments


class ExcelExportService:
    """
    Construye un .xlsx a partir de un ``ExcelExportConfig`` con la misma
    identidad y paleta que el PDF. Devuelve un ``HttpResponse`` con el archivo.
    """

    def __init__(self, config: ExcelExportConfig, queryset: Iterable):
        self.config = config
        self.queryset = queryset

    def _lineas_identidad(self) -> List[Tuple[str, bool]]:
        """[(texto, es_negrita)] a partir de los datos comunes del PDF."""
        datos = _datos_identidad(self.config.empresa, self.config.taller)
        lineas: List[Tuple[str, bool]] = []
        if datos.get('nombre'):
            lineas.append((datos['nombre'], True))
        if datos.get('ruc'):
            lineas.append((f"RUC: {datos['ruc']}", False))
        if datos.get('direccion'):
            lineas.append((f"Dir: {datos['direccion']}", False))
        if datos.get('telefono'):
            lineas.append((f"Telf: {datos['telefono']}", False))
        if not lineas:
            lineas.append(('', False))
        return lineas

    def _lineas_meta(self) -> List[str]:
        generado = 'Generado: {}'.format(timezone.localtime().strftime('%d/%m/%Y %H:%M'))
        usuario = (self.config.usuario or '').strip() or '—'
        return [generado, f'Usuario: {usuario}']

    def _build_data_rows(self) -> List[List]:
        rows = []
        if self.config.row_builder:
            for instance in self.queryset:
                rows.append(self.config.row_builder(instance))
        else:
            for instance in self.queryset:
                rows.append([str(instance) if instance is not None else ''])
        return rows

    def _escribir_identidad(self, ws, ncols: int) -> int:
        """
        Cabecera asimétrica de la hoja (una sola vez). Fila por fila:
          - Izquierda (~60% de las columnas): líneas de identidad (REUTILIZADAS
            de ``pdf_export``). El nombre de la empresa va en NEGRILLA.
          - Derecha (resto): "Generado: …" y "Usuario: …" alineados a la derecha.
        Devuelve la siguiente fila libre.
        """
        izquierda = self._lineas_identidad()
        derecha = self._lineas_meta()

        left_cols = max(1, round(ncols * 0.6))
        if left_cols >= ncols:
            left_cols = ncols - 1 if ncols > 1 else 1

        fila = 1
        total = max(len(izquierda), len(derecha))
        for i in range(total):
            texto_izq, es_negrita = izquierda[i] if i < len(izquierda) else ('', False)
            texto_der = derecha[i] if i < len(derecha) else ''

            if ncols > 1 and left_cols > 1:
                ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=left_cols)
            ws.cell(row=fila, column=1, value=texto_izq).font = (
                FUENTE_EMPRESA if es_negrita else FUENTE_SECUNDARIO
            )
            ws.cell(row=fila, column=1).alignment = ALINEACION_IZQUIERDA

            if ncols > 1 and left_cols < ncols:
                ws.merge_cells(start_row=fila, start_column=left_cols + 1, end_row=fila, end_column=ncols)
                celda = ws.cell(row=fila, column=left_cols + 1, value=texto_der)
                celda.font = FUENTE_SECUNDARIO
                celda.alignment = ALINEACION_DERECHA
            fila += 1

        return fila

    def _escribir_titulo(self, ws, fila: int, ncols: int) -> int:
        """Título del reporte (NEGRILLA, negro, en mayúsculas) + metadato."""
        if ncols > 1:
            ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=ncols)
        celda = ws.cell(row=fila, column=1, value=self.config.title.upper())
        celda.font = FUENTE_TITULO
        celda.alignment = ALINEACION_IZQUIERDA
        fila += 1

        if self.config.metadata:
            if ncols > 1:
                ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=ncols)
            celda = ws.cell(row=fila, column=1, value=self.config.metadata)
            celda.font = FUENTE_METADATO
            celda.alignment = ALINEACION_IZQUIERDA
            fila += 1

        return fila

    def _escribir_tabla(self, ws, fila_inicio: int) -> int:
        """Tabla con grilla completa y cabecera con fondo gris asulado."""
        alineaciones = self.config.alignments or ['left'] * len(self.config.headers)
        alineaciones += ['left'] * (len(self.config.headers) - len(alineaciones))

        celda_cabecera = ws.cell(
            row=fila_inicio, column=1,
            value=self.config.headers[0][0],
        )
        celda_cabecera.font = FUENTE_CABECERA
        celda_cabecera.fill = RELLENO_CABECERA
        celda_cabecera.alignment = _estilo_celda(alineaciones[0])
        celda_cabecera.border = BORDE_TABLA

        for col_idx, (texto, _) in enumerate(self.config.headers[1:], start=2):
            celda = ws.cell(row=fila_inicio, column=col_idx, value=texto)
            celda.font = FUENTE_CABECERA
            celda.fill = RELLENO_CABECERA
            celda.alignment = _estilo_celda(alineaciones[col_idx - 1])
            celda.border = BORDE_TABLA

        fila = fila_inicio + 1
        for row_data in self._build_data_rows():
            for col_idx, valor in enumerate(row_data, start=1):
                texto = '' if valor is None else valor
                celda = ws.cell(row=fila, column=col_idx, value=texto)
                celda.font = FUENTE_DATOS
                celda.alignment = _estilo_celda(alineaciones[col_idx - 1])
                celda.border = BORDE_TABLA
            fila += 1

        return fila

    def generate_response(self) -> HttpResponse:
        wb = Workbook()
        ws = wb.active
        ws.title = self.config.title[:31]

        ncols = len(self.config.headers)

        fila = self._escribir_identidad(ws, ncols)
        fila = self._escribir_titulo(ws, fila, ncols)
        fila += 1  # respiración entre el título y la tabla (sin línea divisoria)

        fila = self._escribir_tabla(ws, fila)

        for col_idx, (_, ancho_pulgadas) in enumerate(self.config.headers, start=1):
            ws.column_dimensions[get_column_letter(col_idx)].width = max(ancho_pulgadas * 8, 12)

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{self.config.filename}"'
        return response