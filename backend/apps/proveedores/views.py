from django.db.models import Q
from django.utils import timezone
from reportlab.platypus import Paragraph
from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.mixins import SoftDeleteDestroyMixin
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import PdfExportConfig, PdfExportService

from .models import Proveedor
from .serializers import ProveedorListSerializer, ProveedorSerializer
from apps.authentication.permissions import TieneRecurso



def _usuario_nombre(request):
    if request.user and request.user.is_authenticated:
        return getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''
    return ''


def _contacto_texto(proveedor):
    telefono = proveedor.telefono or ''
    email = proveedor.email or ''
    if telefono and email:
        return f'{telefono} / {email}'
    if telefono:
        return telefono
    if email:
        return email
    return 'Sin contacto'


class ProveedorViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = ProveedorSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('proveedores')]

    delete_identifier_fields = ['nombre']

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre', 'identificacion', 'email', 'telefono', 'contacto']
    ordering_fields = ['nombre', 'created_at']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return ProveedorListSerializer
        return ProveedorSerializer

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)

        if not empresa_id:
            return Proveedor.objects.none()

        queryset = Proveedor.objects.filter(empresa_id=empresa_id)

        # Sin `estado` se devuelven TODOS los proveedores de la empresa
        # (activos e inactivos); `estado` permite acotar a uno de los dos.
        estado = self.request.query_params.get('estado')
        if estado == 'inactivo':
            queryset = queryset.filter(is_active=False)
        elif estado == 'activo':
            queryset = queryset.filter(is_active=True)

        tipo_identificacion = self.request.query_params.get('tipo_identificacion')
        if tipo_identificacion:
            queryset = queryset.filter(tipo_identificacion=tipo_identificacion)

        if self.action in ['list', 'retrieve']:
            return queryset.select_related('empresa')

        return queryset

    @action(detail=True, methods=['post'], url_path='reactivar')
    def reactivar(self, request, pk=None):
        """Reactiva un proveedor desactivado (soft delete)."""
        instance = self.get_object()

        if instance.is_active:
            return Response(
                {'detail': 'El proveedor ya se encuentra activo.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance.is_active = True
        instance.save(update_fields=['is_active'])

        return Response({
            'status': 'success',
            'id': instance.id,
            'is_active': instance.is_active,
            'message': f"El proveedor '{instance.nombre}' fue reactivado correctamente.",
        })


def _proveedor_export_queryset(request, empresa_id):
    """Aplica los mismos filtros del listado (search, estado,
    tipo_identificacion) a las exportaciones PDF/Excel."""
    queryset = Proveedor.objects.filter(empresa_id=empresa_id)

    search = request.query_params.get('search')
    if search:
        queryset = queryset.filter(
            Q(nombre__icontains=search)
            | Q(identificacion__icontains=search)
            | Q(email__icontains=search)
            | Q(telefono__icontains=search)
            | Q(contacto__icontains=search)
        )

    estado = request.query_params.get('estado')
    if estado == 'inactivo':
        queryset = queryset.filter(is_active=False)
    elif estado == 'activo':
        queryset = queryset.filter(is_active=True)

    tipo_identificacion = request.query_params.get('tipo_identificacion')
    if tipo_identificacion:
        queryset = queryset.filter(tipo_identificacion=tipo_identificacion)

    return queryset


EXPORT_HEADERS = [
    ('Identificación', 1.5),
    ('Proveedor', 2.3),
    ('Contacto', 2.4),
    ('Persona de contacto', 1.8),
    ('Dirección', 2.6),
    ('Estado', 0.9),
]


class ProveedorPdfExportView(APIView):
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('proveedores')]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = _proveedor_export_queryset(
                request, empresa_id
            ).select_related('empresa').order_by('nombre')

            empresa = None
            taller = None
            if queryset.exists():
                empresa = queryset.first().empresa
                if empresa:
                    taller = empresa.talleres.first()

            def subtitle_builder(qs):
                if not qs.exists():
                    return None
                return f'Generado: {timezone.localtime().strftime("%d/%m/%Y %H:%M")}'

            def row_builder(proveedor, cell_style):
                return [
                    Paragraph(proveedor.identificacion or '', cell_style),
                    Paragraph(proveedor.nombre or '', cell_style),
                    Paragraph(_contacto_texto(proveedor), cell_style),
                    Paragraph(proveedor.contacto or '', cell_style),
                    Paragraph(proveedor.direccion or '', cell_style),
                    Paragraph('Activo' if proveedor.is_active else 'Inactivo', cell_style),
                ]

            config = PdfExportConfig(
                title='Listado de Proveedores',
                filename='listado_proveedores.pdf',
                headers=EXPORT_HEADERS,
                empresa=empresa,
                taller=taller,
                usuario=_usuario_nombre(request),
                subtitle_builder=subtitle_builder,
                metadata=f'Número total de Proveedores: {queryset.count()}',
                row_builder=row_builder,
            )
            service = PdfExportService(config, queryset)
            return service.generate_response()

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500
            )


class ProveedorExcelExportView(APIView):
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('proveedores')]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = _proveedor_export_queryset(
                request, empresa_id
            ).select_related('empresa').order_by('nombre')

            empresa = None
            taller = None
            if queryset.exists():
                empresa = queryset.first().empresa
                if empresa:
                    taller = empresa.talleres.first()

            def row_builder(proveedor):
                return [
                    proveedor.identificacion or '',
                    proveedor.nombre or '',
                    _contacto_texto(proveedor),
                    proveedor.contacto or '',
                    proveedor.direccion or '',
                    'Activo' if proveedor.is_active else 'Inactivo',
                ]

            config = ExcelExportConfig(
                title='Listado de Proveedores',
                filename='listado_proveedores.xlsx',
                headers=EXPORT_HEADERS,
                empresa=empresa,
                taller=taller,
                usuario=_usuario_nombre(request),
                metadata=f'Número total de Proveedores: {queryset.count()}',
                alignments=['left', 'left', 'left', 'left', 'left', 'left'],
                row_builder=row_builder,
            )
            service = ExcelExportService(config, queryset)
            return service.generate_response()

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el Excel en este momento. ({str(e)})'},
                status=500
            )
