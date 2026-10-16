import operator
import re
from functools import reduce

from rest_framework import viewsets, permissions, filters, status
from django.db.models import Count, Exists, OuterRef, Q, Prefetch
from django_countries import countries
from django.utils import translation
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import (
    PdfExportConfig,
    PdfExportService,
    contexto_reporte,
)
from apps.empresas.models import Empresa, Taller
from reportlab.platypus import Paragraph
from .models import Vehiculo, VehiculoEmpresa, VehiculoPropietario
from apps.core.ids_publicos import filtrar_por_id_publico
from apps.core.mixins import LookupPublicoMixin, SoftDeleteDestroyMixin
from .serializers import VehiculoSerializer, VehiculoNestedSerializer
from apps.authentication.utils import get_empresa_id_desde_request
from rest_framework.decorators import action
from apps.authentication.permissions import TieneRecurso
from apps.core.utils.export_limits import ExporteDemasiadoGrande
from apps.core.filters import BusquedaIndexadaFilter



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


class PlacaNormalizableSearchFilter(BusquedaIndexadaFilter):
    """Búsqueda indexada que además encuentra placas con/sin guiones o espacios.

    Ej.: almacenado 'GSU5812' también aparece al buscar 'GSU-5812' o 'GSU 5812'.
    """

    def condiciones_extra(self, termino):
        normalizada = termino.replace('-', '').replace(' ', '').upper()
        return [Q(placa__sintilde_icontains=normalizada)] if normalizada else []


class VehiculoViewSet(LookupPublicoMixin, SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = VehiculoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('vehiculos')]

    delete_identifier_fields = ['placa']

    filter_backends = [PlacaNormalizableSearchFilter, filters.OrderingFilter]
    search_fields = [
        'placa',
        'vin',
        'numero_motor',
        'marca',
        'modelo',
        'propietarios__cliente__nombre',
        'propietarios__cliente__identificacion',
    ]
    ordering_fields = ['placa', 'marca', 'created_at']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return VehiculoNestedSerializer
        return VehiculoSerializer

    def perform_soft_delete(self, instance):
        # El vehículo es global: se elimina el vínculo con la empresa activa;
        # para las demás empresas que lo usan no cambia nada.
        empresa_id = get_empresa_id_desde_request(self.request)
        VehiculoEmpresa.objects.filter(vehiculo=instance, empresa_id=empresa_id).delete()

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)

        if not empresa_id:
            return Vehiculo.objects.none()

        include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'

        # El estado (activo/suspendido) es el del vínculo con la empresa activa.
        # Las acciones de mutación (editar/reactivar/eliminar) deben acceder
        # también a registros suspendidos; solo list/retrieve los ocultan.
        estado = self.request.query_params.get('estado')
        if estado not in ('activo', 'inactivo'):
            estado = 'activo' if self.action in ['list', 'retrieve'] and not include_inactive else None
        queryset = Vehiculo.objects.por_estado_en(empresa_id, estado).annotate(
            activo_en_empresa=Exists(
                VehiculoEmpresa.objects.operativos().filter(
                    vehiculo=OuterRef('pk'), empresa_id=empresa_id
                )
            )
        )

        anio = self.request.query_params.get('anio')
        if anio:
            try:
                anio_int = int(anio)
            except (TypeError, ValueError):
                anio_int = None
            if anio_int:
                queryset = queryset.filter(anio=anio_int)

        tipo = self.request.query_params.get('tipo')
        if tipo:
            queryset = queryset.filter(tipo=tipo)

        cliente_id = self.request.query_params.get('cliente')
        if cliente_id:
            # Subconsulta en vez de JOIN: no duplica filas, así que el listado
            # no necesita `DISTINCT` (que obligaba a deduplicar todo el tenant).
            propietarios = filtrar_por_id_publico(
                VehiculoPropietario.objects.filter(es_actual=True), 'cliente', cliente_id
            )
            queryset = queryset.filter(pk__in=propietarios.values('vehiculo_id'))

        if self.action in ['list', 'retrieve']:
            return queryset.prefetch_related(
                Prefetch(
                    'propietarios',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('cliente'),
                    to_attr='propietarios_actuales',
                ),
            )

        return queryset

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


def _vehiculo_export_queryset(request, empresa_id):
    """Aplica los mismos filtros del listado (search, estado, anio, tipo) a las
    exportaciones PDF/Excel."""
    estado = request.query_params.get('estado')
    # Misma semántica que el listado: sin filtro de estado solo se exportan
    # los vehículos activos en la empresa.
    queryset = Vehiculo.objects.por_estado_en(
        empresa_id, estado if estado == 'inactivo' else 'activo'
    )

    search = request.query_params.get('search')
    if search:
        search_fields = [
            'placa',
            'vin',
            'numero_motor',
            'marca',
            'modelo',
            'propietarios__cliente__nombre',
            'propietarios__cliente__identificacion',
        ]
        for term in [t for t in search.replace(',', ' ').split() if t]:
            conditions = [Q(**{f'{field}__icontains': term}) for field in search_fields]
            normalized = term.replace('-', '').replace(' ', '').upper()
            if normalized:
                conditions.append(Q(placa__icontains=normalized))
            queryset = queryset.filter(reduce(operator.or_, conditions))

    anio = request.query_params.get('anio')
    if anio:
        try:
            anio_int = int(anio)
        except (TypeError, ValueError):
            anio_int = None
        if anio_int:
            queryset = queryset.filter(anio=anio_int)

    tipo = request.query_params.get('tipo')
    if tipo:
        queryset = queryset.filter(tipo=tipo)

    return queryset.distinct().annotate(
        activo_en_empresa=Exists(
            VehiculoEmpresa.objects.operativos().filter(
                vehiculo=OuterRef('pk'), empresa_id=empresa_id
            )
        )
    )


class VehiculoPdfExportView(APIView):
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('vehiculos')]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = _vehiculo_export_queryset(request, empresa_id).order_by('placa').prefetch_related(
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
                    Paragraph('Activo' if vehiculo.activo_en_empresa and vehiculo.is_active else 'Inactivo', cell_style),
                ]

            # Encabezado con la empresa de la sesión (un vehículo compartido
            # puede estar vinculado a otras empresas).
            empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)

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
                empresa=empresa,
                taller=taller,
                usuario=usuario_nombre,
                row_builder=row_builder,
            )

            service = PdfExportService(config, queryset)
            return service.generate_response()

        except ExporteDemasiadoGrande:
            raise  # DRF responde 400 con el detalle
        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500
            )


class VehiculoExcelExportView(APIView):
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('vehiculos')]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        try:
            queryset = _vehiculo_export_queryset(request, empresa_id).order_by('placa').prefetch_related(
                Prefetch(
                    'propietarios',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('cliente'),
                    to_attr='propietarios_actuales',
                ),
            )

            # Encabezado con la empresa de la sesión (un vehículo compartido
            # puede estar vinculado a otras empresas).
            empresa = Empresa.objects.filter(pk=empresa_id).first()
            taller = empresa.talleres.first() if empresa else None

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
                    'Activo' if vehiculo.activo_en_empresa and vehiculo.is_active else 'Inactivo',
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

        except ExporteDemasiadoGrande:
            raise  # DRF responde 400 con el detalle
        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el Excel en este momento. ({str(e)})'},
                status=500
            )
