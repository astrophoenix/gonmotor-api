import operator
import re
from functools import reduce

from rest_framework import viewsets, permissions, filters, status
from django.db.models import Count, Q, Prefetch
from django_countries import countries
from django.utils import translation
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import PdfExportConfig, PdfExportService
from apps.empresas.models import Empresa, Taller
from reportlab.platypus import Paragraph
from .models import Vehiculo, VehiculoPropietario
from apps.core.mixins import SoftDeleteDestroyMixin
from .serializers import VehiculoSerializer, VehiculoNestedSerializer
from apps.authentication.utils import get_empresa_id_desde_request
from rest_framework.decorators import action


def formatear_placa(placa):
    """Inserta el guion visual de la placa (GSU5812 -> GSU-5812).

    Misma lógica que el frontend (`formatPlate`): si ya trae guion o no
    coincide con el patrón letras+números, se devuelve tal cual.
    """
    if not placa:
        return ''
    placa = str(placa).strip()
    if '-' in placa:
        return placa
    match = re.match(r'^([A-Za-z]+)(\d+)$', placa)
    if not match:
        return placa
    return f'{match.group(1)}-{match.group(2)}'


class PlacaNormalizableSearchFilter(filters.SearchFilter):
    """SearchFilter que además busca placas con/sin guiones o espacios.

    Ej.: almacenado 'GSU5812' también aparece al buscar 'GSU-5812' o 'GSU 5812'.
    """

    def filter_queryset(self, request, queryset, view):
        search_terms = self.get_search_terms(request)
        search_fields = getattr(view, 'search_fields', None)

        if not search_fields or not search_terms:
            return queryset

        lookups = [self.construct_search(str(field), queryset) for field in search_fields]

        conditions = []
        for term in search_terms:
            queries = [Q(**{lookup: term}) for lookup in lookups]
            normalized = term.replace('-', '').replace(' ', '').upper()
            if normalized:
                queries.append(Q(**{'placa__icontains': normalized}))
            conditions.append(reduce(operator.or_, queries))

        return queryset.filter(reduce(operator.and_, conditions))


class VehiculoViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = VehiculoSerializer
    permission_classes = [permissions.IsAuthenticated]

    delete_identifier_fields = ['placa']
    delete_relation_fields = ['ordenes_trabajo', 'propietarios']

    filter_backends = [PlacaNormalizableSearchFilter, filters.OrderingFilter]
    search_fields = [
        'placa',
        'vin',
        'numero_motor',
        'marca',
        'modelo',
        'propietarios__cliente__nombre',
        'propietarios__cliente__identificacion',
        'empresas__nombre_comercial',
        'empresas__ruc'
    ]
    ordering_fields = ['placa', 'marca', 'created_at']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return VehiculoNestedSerializer
        return VehiculoSerializer

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)

        if not empresa_id:
            return Vehiculo.objects.none()

        queryset = Vehiculo.objects.filter(empresas=empresa_id)

        include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'

        # Las acciones de mutación (editar/reactivar/eliminar) deben acceder
        # también a registros desactivados; solo list/retrieve ocultan inactivos.
        estado = self.request.query_params.get('estado')
        if estado == 'inactivo':
            queryset = queryset.filter(is_active=False)
        elif estado == 'activo':
            queryset = queryset.filter(is_active=True)
        elif self.action in ['list', 'retrieve'] and not include_inactive:
            queryset = queryset.filter(is_active=True)

        anio = self.request.query_params.get('anio')
        if anio:
            try:
                anio_int = int(anio)
            except (TypeError, ValueError):
                anio_int = None
            if anio_int:
                queryset = queryset.filter(anio=anio_int)

        cliente_id = self.request.query_params.get('cliente')
        if cliente_id:
            queryset = queryset.filter(propietarios__cliente_id=cliente_id, propietarios__es_actual=True)

        if self.action in ['list', 'retrieve']:
            return queryset.select_related().prefetch_related(
                Prefetch(
                    'propietarios',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('cliente'),
                    to_attr='propietarios_actuales',
                ),
                'empresas'
            ).distinct()

        return queryset.distinct()

    @action(detail=False, methods=['get'])
    def choices(self, request):
        tipo_choices = [
            {'value': choice[0], 'label': choice[1]}
            for choice in Vehiculo.TipoVehiculo.choices
        ]
        with translation.override('es'):
            paises = [
                {'code': country[0], 'name': str(country[1])}
                for country in countries
            ]
        return Response({
            'tipo': tipo_choices,
            'paises': paises,
        })

    @action(detail=True, methods=['post', 'delete'], url_path='imagen')
    def imagen(self, request, pk=None):
        vehiculo = self.get_object()
        if request.method == 'POST':
            file = request.FILES.get('imagen')
            if not file:
                return Response({'detail': 'No se envió el archivo.'}, status=status.HTTP_400_BAD_REQUEST)
            vehiculo.imagen = file
            vehiculo.save()
            return Response({'imagen': vehiculo.imagen.url if vehiculo.imagen else None})
        if request.method == 'DELETE':
            if vehiculo.imagen:
                vehiculo.imagen.delete(save=False)
                vehiculo.imagen = None
                vehiculo.save()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


class VehiculoPdfExportView(APIView):
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
            queryset = Vehiculo.objects.filter(empresas=empresa_id, is_active=True).order_by('placa').prefetch_related(
                Prefetch(
                    'propietarios',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('cliente'),
                    to_attr='propietarios_actuales',
                ),
            )

            def row_builder(vehiculo, cell_style):
                propietario = getattr(vehiculo, 'propietarios_actuales', [])
                cliente_nombre = propietario[0].cliente_nombre_estado if propietario else ''
                return [
                    Paragraph(formatear_placa(vehiculo.placa), cell_style),
                    Paragraph(vehiculo.marca or '', cell_style),
                    Paragraph(vehiculo.modelo or '', cell_style),
                    Paragraph(str(vehiculo.anio) if vehiculo.anio else '', cell_style),
                    Paragraph(cliente_nombre or 'Sin dueño', cell_style),
                    Paragraph('Activo' if vehiculo.is_active else 'Inactivo', cell_style),
                ]

            empresa = None
            taller = None
            if queryset.exists():
                primera_empresa = queryset.first().empresas.first()
                if primera_empresa:
                    empresa = primera_empresa
                    taller = primera_empresa.talleres.first()

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            config = PdfExportConfig(
                title='Listado de Vehículos',
                filename='listado_vehiculos.pdf',
                headers=[
                    ('Placa', 1.0),
                    ('Marca', 1.3),
                    ('Modelo', 1.4),
                    ('Año', 0.7),
                    ('Dueño', 1.9),
                    ('Estado', 0.9),
                ],
                metadata=f'Número total de Vehículos: {queryset.count()}',
                empresa=empresa,
                taller=taller,
                usuario=usuario_nombre,
                row_builder=row_builder,
            )

            service = PdfExportService(config, queryset)
            return service.generate_response()

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500
            )


class VehiculoExcelExportView(APIView):
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
            queryset = Vehiculo.objects.filter(empresas=empresa_id, is_active=True).order_by('placa').prefetch_related(
                Prefetch(
                    'propietarios',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('cliente'),
                    to_attr='propietarios_actuales',
                ),
            )

            empresa = None
            taller = None
            if queryset.exists():
                primera_empresa = queryset.first().empresas.first()
                if primera_empresa:
                    empresa = primera_empresa
                    taller = primera_empresa.talleres.first()

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def row_builder(vehiculo):
                propietario = getattr(vehiculo, 'propietarios_actuales', [])
                cliente_nombre = propietario[0].cliente_nombre_estado if propietario else ''
                return [
                    formatear_placa(vehiculo.placa),
                    vehiculo.marca or '',
                    vehiculo.modelo or '',
                    str(vehiculo.anio) if vehiculo.anio else '',
                    cliente_nombre or 'Sin dueño',
                    'Activo' if vehiculo.is_active else 'Inactivo',
                ]

            config = ExcelExportConfig(
                title='Listado de Vehículos',
                filename='listado_vehiculos.xlsx',
                headers=[
                    ('Placa', 1.0),
                    ('Marca', 1.3),
                    ('Modelo', 1.4),
                    ('Año', 0.7),
                    ('Dueño', 2.3),
                    ('Estado', 0.9),
                ],
                metadata=f'Número total de Vehículos: {queryset.count()}',
                alignments=['left', 'left', 'left', 'center', 'left', 'center'],
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
