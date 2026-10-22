import logging

from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.permissions import TieneRecurso
from apps.authentication.utils import (
    get_empresa_id_desde_request,
    get_taller_desde_request,
)
from apps.core.mixins import FiltroTallerMixin, LookupPublicoMixin
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.export_limits import ExporteDemasiadoGrande
from apps.core.utils.pdf_export import contexto_reporte

from .models import Gasto
from .serializers import GastoSerializer

logger = logging.getLogger(__name__)


class GastoViewSet(LookupPublicoMixin, FiltroTallerMixin, viewsets.ModelViewSet):
    serializer_class = GastoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('gastos')]
    queryset = Gasto.objects.select_related('taller', 'creado_por', 'empresa')
    # Taller activo (`X-Taller-ID`); el filtro manual `?taller_id=` manda si viene.
    taller_filter_field = 'taller'
    taller_query_param = 'taller_id'

    def get_queryset(self):
        queryset = super().get_queryset().filter(is_active=True)
        request = self.request
        if not request or not request.user.is_authenticated:
            return queryset.none()

        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return queryset.none()
        queryset = queryset.filter(empresa_id=empresa_id)

        fecha_desde = self.request.query_params.get('fecha_desde')
        if fecha_desde:
            queryset = queryset.filter(fecha__gte=fecha_desde)

        fecha_hasta = self.request.query_params.get('fecha_hasta')
        if fecha_hasta:
            queryset = queryset.filter(fecha__lte=fecha_hasta)

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria=categoria)

        return self.filtrar_por_taller(queryset)

    @action(detail=False, methods=['get'], url_path='export-excel')
    def export_excel(self, request):
        """Gastos con los mismos filtros y taller que el listado."""
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=403)

        empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)
        config = ExcelExportConfig(
            title='Listado de Gastos',
            filename='listado_gastos.xlsx',
            headers=[('Fecha', 1.0), ('Taller', 1.4), ('Categoría', 1.6), ('Descripción', 2.6), ('Monto', 1.0)],
            empresa=empresa,
            taller=taller,
            usuario=usuario_nombre,
            row_builder=lambda gasto: [
                gasto.fecha.strftime('%d/%m/%Y'),
                gasto.taller.nombre if gasto.taller_id else 'Sin taller',
                gasto.get_categoria_display(),
                gasto.descripcion or '',
                gasto.monto,
            ],
            alignments=['left', 'left', 'left', 'left', 'right'],
        )
        try:
            return ExcelExportService(config, self.filter_queryset(self.get_queryset())).generate_response()
        except ExporteDemasiadoGrande:
            raise  # DRF responde 400 con el detalle
        except Exception:
            logger.exception('Error al generar el Excel del listado de gastos')
            return Response({'detail': 'No se pudo generar el Excel en este momento.'}, status=500)
