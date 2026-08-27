from io import BytesIO
from typing import List, Tuple, Callable, Optional

from django.conf import settings
from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class PdfExportConfig:
    def __init__(
        self,
        title: str,
        filename: str,
        headers: List[Tuple[str, float]],
        subtitle_builder: Optional[Callable] = None,
        row_builder: Optional[Callable] = None,
        empresa=None,
        taller=None,
        usuario: Optional[str] = None,
    ):
        self.title = title
        self.filename = filename
        self.headers = headers  # (header_text, width_in_inches)
        self.subtitle_builder = subtitle_builder
        self.row_builder = row_builder
        self.empresa = empresa
        self.taller = taller
        self.usuario = usuario


class PdfExportService:
    def __init__(self, config: PdfExportConfig, queryset):
        self.config = config
        self.queryset = queryset

    def _build_header(self) -> Optional[Table]:
        empresa = getattr(self.config, 'empresa', None)
        taller = getattr(self.config, 'taller', None)
        usuario = getattr(self.config, 'usuario', None) or ''

        styles = getSampleStyleSheet()
        style_normal = styles['Normal']
        style_bold = ParagraphStyle(
            'HeaderBold',
            parent=style_normal,
            fontSize=9,
            leading=11,
            textColor=colors.HexColor('#1f2937'),
        )
        style_small = ParagraphStyle(
            'HeaderSmall',
            parent=style_normal,
            fontSize=8,
            leading=10,
            textColor=colors.HexColor('#6b7280'),
        )
        style_title = ParagraphStyle(
            'HeaderTitle',
            parent=style_normal,
            fontSize=11,
            leading=13,
            textColor=colors.HexColor('#111827'),
            spaceAfter=2,
        )

        logo_paragraph = Paragraph('', style_small)
        if empresa and getattr(empresa, 'logo', None):
            try:
                logo_paragraph = Image(
                    empresa.logo.path,
                    width=1.2 * inch,
                    height=0.9 * inch,
                )
            except Exception:
                logo_paragraph = Paragraph(
                    getattr(empresa, 'nombre_comercial', '') or '',
                    style_title,
                )
        elif empresa:
            logo_paragraph = Paragraph(
                getattr(empresa, 'nombre_comercial', '') or '',
                style_title,
            )

        nombre_empresa = ''
        ruc = ''
        direccion = ''
        telefono = ''
        if empresa:
            nombre_empresa = getattr(empresa, 'razon_social', '') or getattr(empresa, 'nombre_comercial', '') or ''
            ruc = getattr(empresa, 'ruc', '') or ''
            telefono = getattr(empresa, 'telefono', '') or ''
        if taller:
            direccion = getattr(taller, 'direccion', '') or ''
            telefono = telefono or getattr(taller, 'telefono', '') or ''

        center_parts = []
        if nombre_empresa:
            center_parts.append(f'<b>{nombre_empresa}</b>')
        if ruc:
            center_parts.append(f'RUC: {ruc}')
        if direccion:
            center_parts.append(direccion)
        if telefono:
            center_parts.append(f'Tel: {telefono}')
        center_block = Paragraph('<br/>'.join(center_parts), style_normal)

        from django.utils import timezone
        fecha_hora = timezone.localtime().strftime('%d/%m/%Y %H:%M')

        right_parts = [self.config.title, f'Generado: {fecha_hora}']
        if usuario:
            right_parts.append(f'Usuario: {usuario}')
        right_block = Paragraph('<br/>'.join(right_parts), style_normal)

        header_data = [[logo_paragraph, center_block, right_block]]
        header_table = Table(
            header_data,
            colWidths=[1.4 * inch, 3.0 * inch, 2.2 * inch],
        )
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))

        return header_table

    def _build_subtitle(self) -> Optional[Paragraph]:
        if not self.config.subtitle_builder:
            return None

        subtitle_text = self.config.subtitle_builder(self.queryset)
        if not subtitle_text:
            return None

        styles = getSampleStyleSheet()
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#6b7280'),
            spaceAfter=18,
        )
        return Paragraph(subtitle_text, subtitle_style)

    def _build_table(self) -> Table:
        styles = getSampleStyleSheet()
        title_style = styles['Heading2']
        title_style.fontSize = 16
        title_style.textColor = colors.HexColor('#1f2937')
        title_style.spaceAfter = 12

        cell_style = ParagraphStyle(
            'CellText',
            parent=styles['Normal'],
            fontSize=9,
            leading=11,
            textColor=colors.HexColor('#1f2937'),
        )

        header_row = [Paragraph(h[0], cell_style) for h in self.config.headers]
        data = [header_row]

        if self.config.row_builder:
            for instance in self.queryset:
                row_cells = self.config.row_builder(instance, cell_style)
                data.append(row_cells)
        else:
            for instance in self.queryset:
                row_cells = [Paragraph(str(instance), cell_style)]
                data.append(row_cells)

        col_widths = [h[1] * inch for h in self.config.headers]

        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f6')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ]))

        return table

    def generate_response(self) -> HttpResponse:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=0.5 * inch,
            rightMargin=0.5 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        elements = []

        header = self._build_header()
        if header:
            elements.append(header)
            elements.append(Spacer(1, 12))

        table = self._build_table()
        elements.append(table)

        doc.build(elements)
        buffer.seek(0)

        response = HttpResponse(buffer.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{self.config.filename}"'
        return response
