from rest_framework import viewsets, permissions, filters, status
from rest_framework.renderers import JSONRenderer
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Count, Q
from django.db.models import Prefetch
from django.utils import timezone
from django.http import HttpResponse
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.empresas.models import Empresa, Taller
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
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

        include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        if self.action in ['list', 'retrieve']:
            return queryset.select_related('empresa').annotate(
                vehiculos_count=Count(
                    'vehiculos_asociados__vehiculo',
                    filter=Q(vehiculos_asociados__es_actual=True),
                    distinct=True,
                )
            ).prefetch_related(
                Prefetch(
                    'vehiculos_asociados',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('vehiculo'),
                    to_attr='propietarios_actuales',
                )
            )

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
            queryset = Cliente.objects.filter(empresa_id=empresa_id, is_active=True).select_related(
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

            def subtitle_builder(qs):
                if not qs.exists():
                    return None

                primera_empresa = qs.first().empresa
                if not primera_empresa:
                    return None

                empresa_nombre = primera_empresa.nombre_comercial or primera_empresa.razon_social
                return f'Empresa: {empresa_nombre}<br/>Generado: {timezone.localtime().strftime("%d/%m/%Y %H:%M")}'

            def row_builder(cliente, cell_style):
                identificacion = cliente.identificacion or ''
                nombre = cliente.nombre or ''

                telefono = cliente.telefono or ''
                email = cliente.email or ''
                if telefono and email:
                    contacto = f'{telefono}<br/>{email}'
                elif telefono:
                    contacto = telefono
                elif email:
                    contacto = email
                else:
                    contacto = 'Sin contacto'

                relaciones = getattr(cliente, 'propietarios_actuales', [])
                vehiculos = [relacion.vehiculo for relacion in relaciones]
                vehiculos.sort(key=lambda v: v.id)
                if vehiculos:
                    placas = [v.placa for v in vehiculos if v.placa]
                    grupos = []
                    for i in range(0, len(placas), 3):
                        grupos.append(', '.join(placas[i:i + 3]))
                    vehiculos_texto = '<br/>'.join(grupos) if grupos else 'Sin vehículos'
                else:
                    vehiculos_texto = 'Sin vehículos'

                return [
                    Paragraph(identificacion, cell_style),
                    Paragraph(nombre, cell_style),
                    Paragraph(contacto, cell_style),
                    Paragraph(vehiculos_texto, cell_style),
                ]

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

            config = PdfExportConfig(
                title='Listado de Clientes',
                filename='listado_clientes.pdf',
                headers=[
                    ('Identificación', 1.4),
                    ('Nombre / Razón Social', 2.2),
                    ('Contacto', 2.0),
                    ('Vehículos', 2.0),
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
            queryset = Cliente.objects.filter(empresa_id=empresa_id, is_active=True).select_related(
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
                if vehiculos:
                    placas = [v.placa for v in vehiculos if v.placa]
                    vehiculos_texto = ', '.join(placas) if placas else 'Sin vehículos'
                else:
                    vehiculos_texto = 'Sin vehículos'

                return [
                    cliente.identificacion or '',
                    cliente.nombre or '',
                    contacto,
                    vehiculos_texto,
                ]

            config = ExcelExportConfig(
                title='Listado de Clientes',
                filename='listado_clientes.xlsx',
                headers=[
                    ('Identificación', 1.4),
                    ('Nombre / Razón Social', 2.2),
                    ('Contacto', 2.0),
                    ('Vehículos', 2.0),
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
