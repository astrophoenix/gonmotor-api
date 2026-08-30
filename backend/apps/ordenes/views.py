from django.utils import timezone
from rest_framework import filters, permissions, viewsets
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import PdfExportConfig, PdfExportService

from .models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo
from .serializers import InspeccionVehiculoSerializer, OrdenTrabajoSerializer, RecepcionVehiculoSerializer


class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = OrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['numero_orden', 'cliente__nombre', 'vehiculo__placa']
    ordering_fields = ['numero_orden', 'created_at', 'total']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return OrdenTrabajo.objects.none()
        return OrdenTrabajo.objects.filter(empresa_id=empresa_id)


class RecepcionVehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = RecepcionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['orden_trabajo__numero_orden', 'orden_trabajo__vehiculo__placa', 'orden_trabajo__cliente__nombre']
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return RecepcionVehiculo.objects.none()
        return RecepcionVehiculo.objects.filter(empresa_id=empresa_id)


class InspeccionVehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = InspeccionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['recepcion__vehiculo__placa', 'recepcion__cliente__nombre']
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return InspeccionVehiculo.objects.none()
        return InspeccionVehiculo.objects.filter(empresa_id=empresa_id)


class RecepcionPdfExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = RecepcionVehiculo.objects.filter(
                orden_trabajo__empresa=empresa_id,
                is_active=True
            ).select_related('orden_trabajo', 'orden_trabajo__vehiculo', 'orden_trabajo__cliente').order_by('-created_at')

            def subtitle_builder(qs):
                if not qs.exists():
                    return None
                return f'Generado: {timezone.localtime().strftime("%d/%m/%Y %H:%M")}'

            def row_builder(recepcion, cell_style):
                orden = recepcion.orden_trabajo
                vehiculo = orden.vehiculo if orden else None
                cliente = orden.cliente if orden else None
                return [
                    Paragraph(f'#{recepcion.id}', cell_style),
                    Paragraph(vehiculo.placa if vehiculo else '-', cell_style),
                    Paragraph(cliente.nombre if cliente else '-', cell_style),
                    Paragraph('Sí' if recepcion.ingreso_en_grua else 'No', cell_style),
                    Paragraph(timezone.localtime(recepcion.created_at).strftime('%d/%m/%Y %H:%M'), cell_style),
                ]

            empresa = None
            taller = None
            if queryset.exists():
                primera = queryset.first()
                if primera.orden_trabajo:
                    empresa = primera.orden_trabajo.empresa
                    taller = primera.orden_trabajo.empresa.talleres.first() if hasattr(primera.orden_trabajo.empresa, 'talleres') else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            config = PdfExportConfig(
                title='Listado de Recepciones',
                filename='listado_recepciones.pdf',
                headers=[
                    ('ID', 0.8),
                    ('Placa', 1.4),
                    ('Cliente', 2.0),
                    ('Grúa', 0.8),
                    ('Fecha ingreso', 1.6),
                ],
                empresa=empresa,
                taller=taller,
                usuario=usuario_nombre,
                subtitle_builder=subtitle_builder,
                row_builder=row_builder,
            )

            service = PdfExportService(config, queryset)
            return service.generate_response()

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500
            )


class RecepcionExcelExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = RecepcionVehiculo.objects.filter(
                orden_trabajo__empresa=empresa_id,
                is_active=True
            ).select_related('orden_trabajo', 'orden_trabajo__vehiculo', 'orden_trabajo__cliente').order_by('-created_at')

            empresa = None
            taller = None
            if queryset.exists():
                primera = queryset.first()
                if primera.orden_trabajo:
                    empresa = primera.orden_trabajo.empresa
                    taller = primera.orden_trabajo.empresa.talleres.first() if hasattr(primera.orden_trabajo.empresa, 'talleres') else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def row_builder(recepcion):
                orden = recepcion.orden_trabajo
                vehiculo = orden.vehiculo if orden else None
                cliente = orden.cliente if orden else None
                return [
                    str(recepcion.id),
                    vehiculo.placa if vehiculo else '-',
                    cliente.nombre if cliente else '-',
                    'Sí' if recepcion.ingreso_en_grua else 'No',
                    timezone.localtime(recepcion.created_at).strftime('%d/%m/%Y %H:%M'),
                ]

            config = ExcelExportConfig(
                title='Listado de Recepciones',
                filename='listado_recepciones.xlsx',
                headers=[
                    ('ID', 0.8),
                    ('Placa', 1.4),
                    ('Cliente', 2.0),
                    ('Grúa', 0.8),
                    ('Fecha ingreso', 1.6),
                ],
                empresa=empresa,
                taller=taller,
                usuario=usuario_nombre,
                row_builder=row_builder,
            )

            service = ExcelExportService(config, queryset)
            return service.generate_response()

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el Excel en este momento. ({str(e)})'},
                status=500
            )
