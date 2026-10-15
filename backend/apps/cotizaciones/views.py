from django.http import FileResponse

import datetime
import logging

from io import BytesIO

from rest_framework import filters, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.utils import get_empresa_id_desde_request
from apps.authentication.permissions import TieneRecurso
from apps.core.mixins import FiltroTallerMixin
from apps.core.utils.pdf_export import (
    PdfExportConfig,
    PdfExportService,
    celda_texto,
    contexto_reporte,
    fecha_hora_local,
)
from apps.ordenes.relaciones_flujo import responder_relaciones
from apps.core.utils.export_limits import ExporteDemasiadoGrande

from .models import Cotizacion, DetalleRepuestoCotizacion, DetalleServicioCotizacion
from .pdf import exportar_cotizacion_pdf
from .serializers import (
    CotizacionSerializer,
    DetalleRepuestoCotizacionSerializer,
    DetalleServicioCotizacionSerializer,
    ESTADOS_EDITABLES,
)

logger = logging.getLogger(__name__)


def _parsear_fecha(valor):
    """Convierte 'YYYY-MM-DD' en date; un valor inválido se ignora en vez de romper la petición."""
    if not valor:
        return None
    try:
        return datetime.date.fromisoformat(valor.strip())
    except (TypeError, ValueError):
        return None


def _validar_origen_cotizacion(inspeccion=None, recepcion=None):
    """Impide crear cotizaciones desde inspecciones ya convertidas en OT o sin ítems."""
    if inspeccion is not None:
        if inspeccion.orden_trabajo_id:
            raise serializers.ValidationError(
                'La inspección ya se convirtió en orden de trabajo; no se puede cotizar nuevamente.'
            )
        if not (
            inspeccion.servicios_detectados.exists()
            or inspeccion.repuestos_sugeridos.exists()
        ):
            raise serializers.ValidationError(
                {
                    'inspeccion_origen': 'La inspección no tiene servicios ni repuestos. '
                    'Agrega al menos un ítem antes de crear la cotización.'
                }
            )


def _validar_origen_inspeccion(inspeccion):
    """Valida solo el origen inspección (compatibilidad con llamadas existentes)."""
    _validar_origen_cotizacion(inspeccion=inspeccion)


class CotizacionViewSet(FiltroTallerMixin, viewsets.ModelViewSet):
    serializer_class = CotizacionSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('cotizaciones')]
    taller_filter_field = 'sucursal'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'numero_cotizacion',
        'cliente__nombre',
        'cliente__identificacion',
        'vehiculo__placa',
        'vehiculo__marca',
        'vehiculo__modelo',
    ]
    ordering_fields = ['numero_cotizacion', 'created_at', 'total']
    ordering = ['-created_at']

    # Filtros del panel "Búsqueda" del listado. Se aplican por query params
    # (no django-filter) siguiendo el criterio de recepciones/inspecciones.
    FILTROS_LISTADO = {
        'estado': 'estado',
    }

    @action(detail=False, methods=['get'], url_path='exportar-pdf', url_name='exportar-listado-pdf')
    def exportar_listado_pdf(self, request):
        """
        Listado PDF con los mismos filtros, búsqueda y recorte por taller del
        listado. No confundir con ``exportar_pdf`` (detail): el documento formal
        de una cotización.
        """
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=403)

        # Las filas solo usan relaciones de `select_related`: sin los prefetch del listado.
        queryset = self.filter_queryset(self.get_queryset()).prefetch_related(None)
        empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)

        def row_builder(cotizacion, cell_style):
            asesor = cotizacion.asesor
            return [
                celda_texto(cotizacion.numero_cotizacion or f'#{cotizacion.id}', cell_style),
                celda_texto(cotizacion.vehiculo.placa if cotizacion.vehiculo else None, cell_style),
                celda_texto(cotizacion.cliente.nombre if cotizacion.cliente else None, cell_style),
                celda_texto((asesor.get_full_name() or asesor.username) if asesor else None, cell_style),
                celda_texto(cotizacion.get_estado_display(), cell_style),
                celda_texto(fecha_hora_local(cotizacion.created_at), cell_style),
                celda_texto(f'${cotizacion.total:,.2f}', cell_style),
            ]

        config = PdfExportConfig(
            title='Listado de Cotizaciones',
            filename='listado_cotizaciones.pdf',
            headers=[
                ('Nº Cotización', 1.2),
                ('Placa', 0.9),
                ('Cliente', 2.0),
                ('Asesor', 1.5),
                ('Estado', 1.1),
                ('Fecha', 1.1),
                ('Total', 0.9, 'R'),
            ],
            empresa=empresa,
            taller=taller,
            usuario=usuario_nombre,
            row_builder=row_builder,
        )
        try:
            return PdfExportService(config, queryset).generate_response()
        except ExporteDemasiadoGrande:
            raise  # DRF responde 400 con el detalle
        except Exception as e:
            logger.exception('Error al generar el PDF del listado de cotizaciones')
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500,
            )

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return Cotizacion.objects.none()
        queryset = (
            Cotizacion.objects.filter(empresa_id=empresa_id)
            .select_related('cliente', 'vehiculo', 'sucursal', 'empresa', 'asesor', 'aceptada_por', 'recepcion_origen', 'inspeccion_origen', 'orden_trabajo_origen', 'orden_trabajo')
            .prefetch_related('servicios', 'repuestos')
        )
        return self.filtrar_por_taller(self._filtrar_cotizaciones(queryset))

    def _filtrar_cotizaciones(self, queryset):
        """Aplica los filtros del panel de búsqueda del listado de cotizaciones."""
        params = self.request.query_params

        for parametro, campo in self.FILTROS_LISTADO.items():
            valor = params.get(parametro)
            if valor:
                queryset = queryset.filter(**{campo: valor})

        # Filtro por vehículo: lo usa la cotización para detectar si el auto ya
        # tiene una cotización vigente antes de crear otra.
        vehiculo = params.get('vehiculo')
        if vehiculo and vehiculo.isdigit():
            queryset = queryset.filter(vehiculo_id=int(vehiculo))

        # Rango de fechas sobre la fecha de creación (comparación por día,
        # ambos extremos incluidos) para que no dependa de la zona horaria.
        fecha_desde = _parsear_fecha(params.get('fecha_desde'))
        if fecha_desde:
            queryset = queryset.filter(created_at__date__gte=fecha_desde)

        fecha_hasta = _parsear_fecha(params.get('fecha_hasta'))
        if fecha_hasta:
            queryset = queryset.filter(created_at__date__lte=fecha_hasta)

        if params.get('sin_recepcion') in ('1', 'true', 'True'):
            queryset = queryset.filter(recepcion_origen__isnull=True)

        return queryset

    def perform_create(self, serializer):
        inspeccion = serializer.validated_data.get('inspeccion_origen')
        _validar_origen_cotizacion(inspeccion=inspeccion)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        super().perform_update(serializer)

    @action(detail=True, methods=['get', 'post'])
    def relaciones(self, request, pk=None):
        """Vincula o desvincula relaciones de flujo sin borrar entidades."""
        return responder_relaciones(request, self.get_object(), 'cotizacion')

    @action(detail=True, methods=['post'])
    def sincronizar_inspeccion(self, request, pk=None):
        cotizacion = self.get_object()
        _check_cotizacion_editable(cotizacion)
        try:
            cotizacion.sincronizar_desde_inspeccion()
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return Response({
            'id': cotizacion.id,
            'numero_cotizacion': cotizacion.numero_cotizacion,
            'cantidad_servicios': cotizacion.servicios.count(),
            'cantidad_repuestos': cotizacion.repuestos.count(),
            'subtotal': cotizacion.subtotal,
            'total': cotizacion.total,
        })

    @action(detail=True, methods=['post'])
    def generar_orden(self, request, pk=None):
        cotizacion = self.get_object()
        metodo = request.data.get('metodo_aceptacion')
        try:
            ot = cotizacion.generar_orden(usuario=request.user, metodo_aceptacion=metodo)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return Response({
            'id': ot.id,
            'numero_orden': ot.numero_orden,
            'estado': cotizacion.estado,
        })

    @action(detail=True, methods=['get'], url_path='exportar-pdf')
    def exportar_pdf(self, request, pk=None):
        """
        PDF de la cotización (documento formal, no listado).

        Disponible para cualquier estado: el botón del frontend lo muestra desde
        que la cotización tiene ID, así que un borrador también se descarga.
        El tenant se respeta con ``get_object()``: una cotización de otra empresa
        responde 404, nunca se filtra.
        """
        cotizacion = self.get_object()

        usuario = ''
        if request.user and request.user.is_authenticated:
            usuario = request.user.get_full_name() or getattr(request.user, 'username', '') or ''

        buffer = BytesIO()
        exportar_cotizacion_pdf(
            buffer,
            cotizacion,
            empresa=cotizacion.empresa,
            taller=cotizacion.sucursal,
            usuario=usuario,
        )
        buffer.seek(0)

        numero = cotizacion.numero_cotizacion or cotizacion.pk
        return FileResponse(
            buffer,
            content_type='application/pdf',
            filename=f'cotizacion_{numero}.pdf',
            as_attachment=True,
        )


def _check_cotizacion_editable(cotizacion):
    if cotizacion is not None and cotizacion.estado not in ESTADOS_EDITABLES:
        raise serializers.ValidationError(
            f'La cotización está "{cotizacion.get_estado_display()}" y sus ítems no pueden modificarse.'
        )


class DetalleServicioCotizacionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleServicioCotizacionSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('cotizaciones')]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['created_at', 'id']
    ordering = ['created_at', 'id']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleServicioCotizacion.objects.none()
        queryset = DetalleServicioCotizacion.objects.filter(
            cotizacion__empresa_id=empresa_id
        ).select_related('cotizacion')
        cotizacion_id = self.request.query_params.get('cotizacion')
        if cotizacion_id:
            queryset = queryset.filter(cotizacion_id=cotizacion_id)
        return queryset

    def perform_create(self, serializer):
        _check_cotizacion_editable(serializer.validated_data.get('cotizacion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_cotizacion_editable(self.get_object().cotizacion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_cotizacion_editable(instance.cotizacion)
        super().perform_destroy(instance)


class DetalleRepuestoCotizacionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleRepuestoCotizacionSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('cotizaciones')]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['created_at', 'id']
    ordering = ['created_at', 'id']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleRepuestoCotizacion.objects.none()
        queryset = DetalleRepuestoCotizacion.objects.filter(
            cotizacion__empresa_id=empresa_id
        ).select_related('cotizacion')
        cotizacion_id = self.request.query_params.get('cotizacion')
        if cotizacion_id:
            queryset = queryset.filter(cotizacion_id=cotizacion_id)
        return queryset

    def perform_create(self, serializer):
        _check_cotizacion_editable(serializer.validated_data.get('cotizacion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_cotizacion_editable(self.get_object().cotizacion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_cotizacion_editable(instance.cotizacion)
        super().perform_destroy(instance)