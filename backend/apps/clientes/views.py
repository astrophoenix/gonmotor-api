from io import BytesIO

from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Count, Q
from django.db.models import Prefetch
from django.http import FileResponse
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import _formatear_saldo, exportar_clientes_pdf
# from apps.empresas.models import Empresa, Taller
from .models import Cliente
from apps.vehiculos.models import VehiculoPropietario
from apps.core.mixins import SoftDeleteDestroyMixin
from .serializers import ClienteListSerializer, ClienteSerializer
from apps.authentication.utils import get_empresa_id_desde_request
from .excel_import import (
    CLIENT_COLUMNS,
    importar_clientes_desde_xlsx,
    generar_reporte_errores_xlsx,
    generar_plantilla_xlsx,
)


class ClienteViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    parser_classes = (MultiPartParser, FormParser)
    serializer_class = ClienteSerializer
    permission_classes = [permissions.IsAuthenticated]

    delete_identifier_fields = ['nombre']
    delete_relation_fields = ['ordenes_trabajo', 'vehiculos_asociados', 'cotizaciones']

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre', 'identificacion', 'email', 'telefono']
    ordering_fields = ['nombre', 'created_at']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return ClienteListSerializer
        return ClienteSerializer

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)

        if not empresa_id:
            return Cliente.objects.none()

        queryset = Cliente.objects.filter(empresa_id=empresa_id)

        # Sin `estado` se devuelven TODOS los clientes de la empresa (activos e
        # inactivos); el listado los distingue con la columna Estado. `estado`
        # permite acotar a activos o inactivos. Los selectores de búsqueda que
        # solo deben ofrecer activos envían `estado=activo`.
        estado = self.request.query_params.get('estado')
        if estado == 'inactivo':
            queryset = queryset.filter(is_active=False)
        elif estado == 'activo':
            queryset = queryset.filter(is_active=True)

        tipo_identificacion = self.request.query_params.get('tipo_identificacion')
        if tipo_identificacion:
            queryset = queryset.filter(tipo_identificacion=tipo_identificacion)

        if self.action in ['list', 'retrieve']:
            queryset = queryset.select_related('empresa').annotate(
                vehiculos_count=Count(
                    'vehiculos_asociados__vehiculo',
                    filter=Q(vehiculos_asociados__es_actual=True),
                    distinct=True,
                )
            )
            min_vehiculos = self.request.query_params.get('min_vehiculos')
            if min_vehiculos and min_vehiculos.isdigit():
                queryset = queryset.filter(vehiculos_count__gte=int(min_vehiculos))
            queryset = queryset.prefetch_related(
                Prefetch(
                    'vehiculos_asociados',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('vehiculo'),
                    to_attr='propietarios_actuales',
                )
            )
            return queryset

        return queryset

    @action(detail=True, methods=['post'], url_path='reactivar')
    def reactivar(self, request, pk=None):
        """Reactiva un cliente desactivado (soft delete) sin tocar sus vehículos."""
        instance = self.get_object()

        if instance.is_active:
            return Response(
                {'detail': 'El cliente ya se encuentra activo.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance.is_active = True
        instance.save(update_fields=['is_active'])

        return Response({
            'status': 'success',
            'id': instance.id,
            'is_active': instance.is_active,
            'message': f"El cliente '{instance.nombre}' fue reactivado correctamente.",
        })


def _cliente_export_queryset(request, empresa_id):
    """Aplica los mismos filtros del listado (search, estado, tipo_identificacion,
    min_vehiculos) a las exportaciones PDF/Excel."""
    queryset = Cliente.objects.filter(empresa_id=empresa_id)

    search = request.query_params.get('search')
    if search:
        queryset = queryset.filter(
            Q(nombre__icontains=search)
            | Q(identificacion__icontains=search)
            | Q(email__icontains=search)
            | Q(telefono__icontains=search)
        )

    estado = request.query_params.get('estado')
    if estado == 'inactivo':
        queryset = queryset.filter(is_active=False)
    elif estado == 'activo':
        queryset = queryset.filter(is_active=True)

    tipo_identificacion = request.query_params.get('tipo_identificacion')
    if tipo_identificacion:
        queryset = queryset.filter(tipo_identificacion=tipo_identificacion)

    min_vehiculos = request.query_params.get('min_vehiculos')
    if min_vehiculos and min_vehiculos.isdigit():
        queryset = queryset.annotate(
            vehiculos_count=Count(
                'vehiculos_asociados__vehiculo',
                filter=Q(vehiculos_asociados__es_actual=True),
                distinct=True,
            )
        ).filter(vehiculos_count__gte=int(min_vehiculos))

    return queryset


class ClientePdfExportView(APIView):
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
            queryset = _cliente_export_queryset(request, empresa_id).select_related(
                'empresa'
            ).prefetch_related(
                Prefetch(
                    'vehiculos_asociados',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('vehiculo'),
                    to_attr='propietarios_actuales',
                )
            ).order_by('nombre')

            empresa = None
            taller = None
            if queryset.exists():
                primera_empresa = queryset.first().empresa
                if primera_empresa:
                    empresa = primera_empresa
                    taller = primera_empresa.talleres.first()

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            buffer = BytesIO()
            exportar_clientes_pdf(
                buffer,
                queryset,
                total_registros=queryset.count(),
                titulo='Listado de Clientes',
                empresa=empresa,
                taller=taller,
                usuario=usuario_nombre,
                logo=empresa.logo if empresa else None,
            )
            buffer.seek(0)
            return FileResponse(
                buffer,
                content_type='application/pdf',
                filename='listado_clientes.pdf',
                as_attachment=False,
            )

        except Exception as e:
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500
            )


class ClienteExcelExportView(APIView):
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
            queryset = _cliente_export_queryset(request, empresa_id).select_related(
                'empresa'
            ).prefetch_related(
                Prefetch(
                    'vehiculos_asociados',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('vehiculo'),
                    to_attr='propietarios_actuales',
                )
            ).order_by('nombre')

            empresa = None
            taller = None
            if queryset.exists():
                primera_empresa = queryset.first().empresa
                if primera_empresa:
                    empresa = primera_empresa
                    taller = primera_empresa.talleres.first()

            usuario_nombre = ''
            if request.user and request.user.is_authenticated:
                usuario_nombre = getattr(request.user, 'username', '') or getattr(request.user, 'email', '') or ''

            def row_builder(cliente):
                telefono = cliente.telefono or ''
                email = cliente.email or ''
                if telefono and email:
                    contacto = f'{telefono} / {email}'
                elif telefono:
                    contacto = telefono
                elif email:
                    contacto = email
                else:
                    contacto = 'Sin contacto'

                relaciones = getattr(cliente, 'propietarios_actuales', [])
                vehiculos = [relacion.vehiculo for relacion in relaciones]
                vehiculos.sort(key=lambda v: v.id)
                lineas_vehiculos = []
                for v in vehiculos:
                    placa = (v.placa or '').strip()
                    detalle = f'{v.marca} {v.modelo}'.strip()
                    if placa and detalle:
                        lineas_vehiculos.append(f'• {placa} - {detalle}')
                    elif placa:
                        lineas_vehiculos.append(f'• {placa}')
                    elif detalle:
                        lineas_vehiculos.append(f'• {detalle}')
                vehiculos_texto = '\n'.join(lineas_vehiculos) if lineas_vehiculos else 'Sin vehículos'

                saldo, _ = _formatear_saldo(getattr(cliente, 'saldo', 0))

                return [
                    cliente.identificacion or '',
                    cliente.nombre or '',
                    contacto,
                    vehiculos_texto,
                    saldo,
                ]

            config = ExcelExportConfig(
                title='Listado de Clientes',
                filename='listado_clientes.xlsx',
                headers=[
                    ('Identificación', 2.0),
                    ('Cliente', 2.6),
                    ('Contacto', 3.2),
                    ('Vehículos', 2.6),
                    ('Saldo', 1.2),
                ],
                metadata=f'Número total de Clientes: {queryset.count()}',
                alignments=['left', 'left', 'left', 'left', 'right'],
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


class ClienteImportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        empresa_id = get_empresa_id_desde_request(request)

        if not empresa_id:
            return Response(
                {'detail': 'No se pudo determinar la empresa activa.'},
                status=403
            )

        archivo = request.FILES.get('archivo')
        if not archivo:
            return Response(
                {'detail': 'No se envió ningún archivo. Debes adjuntar un archivo .xlsx.'},
                status=400
            )

        nombre = (archivo.name or '').lower()
        if not nombre.endswith('.xlsx'):
            return Response(
                {'detail': 'El archivo debe tener extensión .xlsx.'},
                status=400
            )

        try:
            resultado = importar_clientes_desde_xlsx(archivo, empresa_id)
            if resultado['errores'] and not resultado['exitosos']:
                estado = 400
            else:
                estado = 200
            return Response(resultado, status=estado)
        except Exception as e:
            return Response(
                {'detail': f'No se pudo procesar el archivo. Verifica el formato de las columnas. ({str(e)})'},
                status=400
            )


class ClienteImportErroresView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        errores = request.data.get('errores')
        if not isinstance(errores, list):
            return Response(
                {'detail': 'El campo "errores" debe ser una lista.'},
                status=400
            )

        buffer = generar_reporte_errores_xlsx(errores)

        response = HttpResponse(
            buffer.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="errores_importacion_clientes.xlsx"'
        return response


class ClienteImportTemplateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        buffer = generar_plantilla_xlsx()

        response = HttpResponse(
            buffer.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="plantilla_importacion_clientes.xlsx"'
        return response
