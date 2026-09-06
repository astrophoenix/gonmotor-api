from django.db.models import F
from django.utils import timezone
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from reportlab.platypus import Paragraph

from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.mixins import SoftDeleteDestroyMixin
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import PdfExportConfig, PdfExportService

from .models import Repuesto, Servicio
from .serializers import RepuestoSerializer, ServicioSerializer


class RepuestoViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = RepuestoSerializer
    permission_classes = [permissions.IsAuthenticated]

    delete_identifier_fields = ['codigo']
    delete_relation_fields = ['detalles_inspeccion']

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['codigo', 'nombre', 'descripcion', 'marca', 'numero_parte', 'ubicacion', 'proveedor']
    ordering_fields = ['codigo', 'nombre', 'categoria', 'stock_actual', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return Repuesto.objects.none()

        include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'
        queryset = Repuesto.objects.filter(empresa_id=empresa_id)

        if self.action in ['list', 'retrieve'] and not include_inactive:
            queryset = queryset.filter(is_active=True)

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria=categoria)

        if self.request.query_params.get('stock_bajo', 'false').lower() == 'true':
            queryset = queryset.filter(stock_actual__lte=F('stock_minimo'))

        return queryset

    @action(detail=False, methods=['get'])
    def opciones(self, request):
        """Lista compacta de repuestos activos para selectores (inspecciones/cotizaciones)."""
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'results': []})
        items = Repuesto.objects.filter(empresa_id=empresa_id, is_active=True).order_by('nombre')
        data = [{
            'id': item.pk,
            'codigo': item.codigo,
            'nombre': item.nombre,
            'marca': item.marca,
            'precio_venta': str(item.precio_venta),
            'stock_actual': str(item.stock_actual),
        } for item in items]
        return Response({'results': data})


class ServicioViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = ServicioSerializer
    permission_classes = [permissions.IsAuthenticated]

    delete_identifier_fields = ['codigo']
    delete_relation_fields = ['detalles_inspeccion']

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['codigo', 'nombre', 'descripcion', 'tareas_estandar']
    ordering_fields = ['codigo', 'nombre', 'categoria', 'tiempo_estimado_minutos', 'precio_referencial', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return Servicio.objects.none()

        include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'
        queryset = Servicio.objects.filter(empresa_id=empresa_id)

        if self.action in ['list', 'retrieve'] and not include_inactive:
            queryset = queryset.filter(is_active=True)

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria=categoria)

        return queryset

    @action(detail=False, methods=['get'])
    def opciones(self, request):
        """Lista compacta de servicios activos para selectores (inspecciones/cotizaciones)."""
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'results': []})
        items = Servicio.objects.filter(empresa_id=empresa_id, is_active=True).order_by('nombre')
        data = [{
            'id': item.pk,
            'codigo': item.codigo,
            'nombre': item.nombre,
            'tiempo_estimado_minutos': item.tiempo_estimado_minutos,
            'precio_referencial': str(item.precio_referencial),
        } for item in items]
        return Response({'results': data})


class RepuestoPdfExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403,
            )

        try:
            queryset = Repuesto.objects.filter(
                empresa_id=empresa_id,
                is_active=True,
            ).order_by('codigo')

            empresa = queryset.first().empresa if queryset.exists() else None
            taller = empresa.talleres.first() if empresa else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def subtitle_builder(qs):
                if not qs.exists():
                    return None
                return f'Generado: {timezone.localtime().strftime("%d/%m/%Y %H:%M")}'

            def row_builder(rep, cell_style):
                return [
                    Paragraph(rep.codigo or '', cell_style),
                    Paragraph(rep.nombre or '', cell_style),
                    Paragraph(rep.get_categoria_display() or '', cell_style),
                    Paragraph(rep.marca or '', cell_style),
                    Paragraph(str(rep.stock_actual), cell_style),
                    Paragraph(str(rep.stock_minimo), cell_style),
                    Paragraph('Sí' if rep.stock_bajo else 'No', cell_style),
                    Paragraph(f"${rep.precio_venta:,.2f}", cell_style),
                ]

            config = PdfExportConfig(
                title='Listado de Repuestos',
                filename='listado_repuestos.pdf',
                headers=[
                    ('Código', 1.1),
                    ('Nombre', 1.9),
                    ('Categoría', 1.3),
                    ('Marca', 1.3),
                    ('Stock', 0.7),
                    ('Mínimo', 0.7),
                    ('Bajo', 0.6),
                    ('P. Venta', 0.9),
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
                status=500,
            )


class RepuestoExcelExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403,
            )

        try:
            queryset = Repuesto.objects.filter(
                empresa_id=empresa_id,
                is_active=True,
            ).order_by('codigo')

            empresa = queryset.first().empresa if queryset.exists() else None
            taller = empresa.talleres.first() if empresa else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def row_builder(rep):
                return [
                    rep.codigo or '',
                    rep.nombre or '',
                    rep.get_categoria_display() or '',
                    rep.marca or '',
                    rep.numero_parte or '',
                    str(rep.stock_actual),
                    str(rep.stock_minimo),
                    'Sí' if rep.stock_bajo else 'No',
                    f'{rep.precio_venta:.2f}',
                    rep.proveedor or '',
                ]

            config = ExcelExportConfig(
                title='Listado de Repuestos',
                filename='listado_repuestos.xlsx',
                headers=[
                    ('Código', 1.2),
                    ('Nombre', 2.0),
                    ('Categoría', 1.4),
                    ('Marca', 1.4),
                    ('N° Parte', 1.4),
                    ('Stock', 0.8),
                    ('Mínimo', 0.8),
                    ('Bajo', 0.6),
                    ('P. Venta', 0.9),
                    ('Proveedor', 1.6),
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
                status=500,
            )


class ServicioPdfExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403,
            )

        try:
            queryset = Servicio.objects.filter(
                empresa_id=empresa_id,
                is_active=True,
            ).order_by('codigo')

            empresa = queryset.first().empresa if queryset.exists() else None
            taller = empresa.talleres.first() if empresa else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def subtitle_builder(qs):
                if not qs.exists():
                    return None
                return f'Generado: {timezone.localtime().strftime("%d/%m/%Y %H:%M")}'

            def row_builder(svc, cell_style):
                return [
                    Paragraph(svc.codigo or '', cell_style),
                    Paragraph(svc.nombre or '', cell_style),
                    Paragraph(svc.get_categoria_display() or '', cell_style),
                    Paragraph(str(svc.tiempo_estimado_minutos), cell_style),
                    Paragraph(f"${svc.precio_referencial:,.2f}", cell_style),
                ]

            config = PdfExportConfig(
                title='Listado de Servicios (Mano de Obra)',
                filename='listado_servicios.pdf',
                headers=[
                    ('Código', 1.1),
                    ('Nombre', 2.4),
                    ('Categoría', 1.6),
                    ('Minutos', 0.9),
                    ('P. Referencial', 1.2),
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
                status=500,
            )


class ServicioExcelExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403,
            )

        try:
            queryset = Servicio.objects.filter(
                empresa_id=empresa_id,
                is_active=True,
            ).order_by('codigo')

            empresa = queryset.first().empresa if queryset.exists() else None
            taller = empresa.talleres.first() if empresa else None

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def row_builder(svc):
                return [
                    svc.codigo or '',
                    svc.nombre or '',
                    svc.get_categoria_display() or '',
                    str(svc.tiempo_estimado_minutos),
                    f'{svc.precio_referencial:.2f}',
                ]

            config = ExcelExportConfig(
                title='Listado de Servicios',
                filename='listado_servicios.xlsx',
                headers=[
                    ('Código', 1.2),
                    ('Nombre', 2.4),
                    ('Categoría', 1.6),
                    ('Minutos', 0.9),
                    ('P. Referencial', 1.2),
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
                status=500,
            )