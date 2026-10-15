import datetime
import logging

from django.utils import timezone
from rest_framework import filters, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.utils import get_empresa_id_desde_request
from apps.authentication.permissions import TieneRecurso
from apps.core.mixins import FiltroTallerMixin, SoftDeleteDestroyMixin
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import (
    PdfExportConfig,
    PdfExportService,
    celda_texto,
    contexto_reporte,
    fecha_hora_local,
)
from apps.core.utils.export_limits import ExporteDemasiadoGrande

logger = logging.getLogger(__name__)

from .models import (
    DetalleRepuestoInspeccion,
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioInspeccion,
    DetalleServicioOrdenTrabajo,
    FotoInspeccion,
    FotoOrdenTrabajo,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
    TipoTrabajo,
)
from .relaciones_flujo import responder_relaciones
from .serializers import (
    DetalleRepuestoInspeccionSerializer,
    DetalleRepuestoOrdenTrabajoSerializer,
    DetalleServicioInspeccionSerializer,
    DetalleServicioOrdenTrabajoSerializer,
    FotoInspeccionSerializer,
    FotoOrdenTrabajoSerializer,
    InspeccionVehiculoSerializer,
    OrdenTrabajoSerializer,
    RecepcionVehiculoSerializer,
)


def _check_inspeccion_editable(inspeccion):
    if inspeccion is not None and inspeccion.estado == 'FINALIZADA':
        raise serializers.ValidationError(
            'La inspección está finalizada; reábrela para poder modificar sus detalles.'
        )


def tipo_inspeccion_desde_recepcion(tipo_recepcion):
    """La inspección hereda el motivo de ingreso de la recepción sin degradarlo.

    Recepción, inspección y orden de trabajo comparten la taxonomía `TipoTrabajo`,
    así que el valor se copia tal cual (incluido SINIESTRO u OTRO). Solo se cae al
    default cuando la recepción no tiene un valor válido.
    """
    if tipo_recepcion in TipoTrabajo.values:
        return tipo_recepcion
    return TipoTrabajo.DIAGNOSTICO


class OrdenTrabajoViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = OrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('ordenes')]
    delete_identifier_fields = ['numero_orden']
    # Query param que fija el taller explícitamente (se valida igual que la
    # cabecera X-Taller-ID contra el alcance del rol).
    taller_query_param = 'sucursal'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'numero_orden',
        'cliente__nombre',
        'cliente__identificacion',
        'vehiculo__placa',
        'vehiculo__marca',
        'vehiculo__modelo',
    ]
    ordering_fields = ['numero_orden', 'created_at', 'total']
    ordering = ['-created_at']

    # Filtros del panel "Búsqueda" del listado. Se aplican por query params
    # (no django-filter) siguiendo el criterio de recepciones/inspecciones.
    FILTROS_LISTADO = {
        'estado': 'estado',
        'prioridad': 'prioridad',
        'tipo_trabajo': 'tipo_trabajo',
    }

    @action(detail=False, methods=['get'], url_path='exportar-pdf', url_name='exportar-listado-pdf')
    def exportar_listado_pdf(self, request):
        """Listado PDF con los mismos filtros, búsqueda y recorte por taller del listado."""
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=403)

        # Las filas solo usan relaciones de `select_related`: sin los prefetch del listado.
        queryset = self.filter_queryset(self.get_queryset()).prefetch_related(None)
        empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)

        def row_builder(orden, cell_style):
            return [
                celda_texto(orden.numero_orden or f'#{orden.id}', cell_style),
                celda_texto(orden.vehiculo.placa if orden.vehiculo else None, cell_style),
                celda_texto(orden.cliente.nombre if orden.cliente else None, cell_style),
                celda_texto(orden.get_tipo_trabajo_display(), cell_style),
                celda_texto(orden.get_prioridad_display(), cell_style),
                celda_texto(orden.get_estado_display(), cell_style),
                celda_texto(fecha_hora_local(orden.fecha_ingreso), cell_style),
                celda_texto(fecha_hora_local(orden.fecha_entrega), cell_style),
                celda_texto(f'${orden.total:,.2f}', cell_style),
            ]

        config = PdfExportConfig(
            title='Listado de Órdenes de Trabajo',
            filename='listado_ordenes.pdf',
            headers=[
                ('Nº Orden', 1.1),
                ('Placa', 0.9),
                ('Cliente', 2.0),
                ('Tipo', 1.1),
                ('Prioridad', 0.8),
                ('Estado', 1.1),
                ('Ingreso', 1.1),
                ('Entrega', 1.1),
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
            logger.exception('Error al generar el PDF del listado de órdenes')
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500,
            )

    def get_queryset(self):
        # `para_request` encadena, con las funciones de authentication.utils:
        #   1. empresa activa validada contra UsuarioEmpresa (sin ella -> none());
        #   2. talleres permitidos por empresa + alcance del rol (un taller
        #      ajeno en cabecera o query param -> sin resultados);
        #   3. el manager por defecto ya excluye las OTs eliminadas.
        queryset = (
            OrdenTrabajo.objects.para_request(
                self.request,
                explicito=self.request.query_params.get(self.taller_query_param),
            )
            .select_related(
                'cliente',
                'vehiculo',
                'sucursal',
                'asesor',
                'mecanico_principal',
                'cotizacion_origen',
                'inspeccion',
            )
            .prefetch_related('servicios', 'repuestos', 'recepciones', 'fotos', 'recepciones__fotos', 'inspeccion__fotos')
        )
        return self._filtrar_ordenes(queryset)

    def _filtrar_ordenes(self, queryset):
        """Aplica los filtros del panel de búsqueda del listado de órdenes."""
        params = self.request.query_params

        for parametro, campo in self.FILTROS_LISTADO.items():
            valor = params.get(parametro)
            if valor:
                queryset = queryset.filter(**{campo: valor})

        # Rango de fechas sobre la fecha de creación (comparación por día,
        # ambos extremos incluidos) para que no dependa de la zona horaria.
        fecha_desde = parsear_fecha(params.get('fecha_desde'))
        if fecha_desde:
            queryset = queryset.filter(created_at__date__gte=fecha_desde)

        fecha_hasta = parsear_fecha(params.get('fecha_hasta'))
        if fecha_hasta:
            queryset = queryset.filter(created_at__date__lte=fecha_hasta)

        return queryset

    @action(detail=True, methods=['get', 'post'], url_path='relaciones')
    def relaciones(self, request, pk=None):
        """Vincula o desvincula relaciones de flujo sin borrar entidades."""
        return responder_relaciones(request, self.get_object(), 'orden')


def parsear_fecha(valor):
    """Convierte 'YYYY-MM-DD' en date; un valor inválido se ignora en vez de romper la petición."""
    if not valor:
        return None
    try:
        return datetime.date.fromisoformat(valor.strip())
    except (TypeError, ValueError):
        return None


class RecepcionVehiculoViewSet(FiltroTallerMixin, viewsets.ModelViewSet):
    serializer_class = RecepcionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('recepciones')]
    taller_filter_field = 'sucursal'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['numero_recepcion', 'vehiculo__placa', 'vehiculo__marca', 'cliente__nombre', 'cliente__identificacion', 'orden_trabajo__numero_orden']
    ordering_fields = ['created_at', 'id', 'fecha_ingreso']
    ordering = ['-created_at']

    # Filtros del panel "Búsqueda" del listado. Se aplican por query params
    # (no django-filter) siguiendo el criterio de clientes/vehículos.
    FILTROS_LISTADO = {
        'estado': 'estado',
        'tipo_recepcion': 'tipo_recepcion',
    }

    EXPORT_HEADERS = [
        ('Nº Recepción', 1.2),
        ('Placa', 0.9),
        ('Cliente', 2.0),
        ('Tipo', 1.1),
        ('Estado', 1.1),
        ('Ingreso', 1.1),
        ('Salida', 1.1),
        ('Grúa', 0.6),
    ]

    def _export_queryset(self):
        """Mismo queryset del listado: empresa en sesión, taller, búsqueda y filtros
        (sin sus prefetch: las filas solo usan relaciones de `select_related`)."""
        return self.filter_queryset(self.get_queryset()).prefetch_related(None)

    @staticmethod
    def _export_valores(recepcion):
        return [
            recepcion.numero_recepcion or f'#{recepcion.id}',
            recepcion.vehiculo.placa if recepcion.vehiculo else '-',
            recepcion.cliente.nombre if recepcion.cliente else '-',
            recepcion.get_tipo_recepcion_display(),
            recepcion.get_estado_display(),
            fecha_hora_local(recepcion.fecha_ingreso or recepcion.created_at),
            fecha_hora_local(recepcion.fecha_salida),
            'Sí' if recepcion.ingreso_en_grua else 'No',
        ]

    @action(detail=False, methods=['get'], url_path='exportar-pdf')
    def exportar_pdf(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=403)

        empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)
        config = PdfExportConfig(
            title='Listado de Recepciones',
            filename='listado_recepciones.pdf',
            headers=self.EXPORT_HEADERS,
            empresa=empresa,
            taller=taller,
            usuario=usuario_nombre,
            row_builder=lambda recepcion, cell_style: [
                celda_texto(valor, cell_style) for valor in self._export_valores(recepcion)
            ],
        )
        try:
            return PdfExportService(config, self._export_queryset()).generate_response()
        except ExporteDemasiadoGrande:
            raise  # DRF responde 400 con el detalle
        except Exception as e:
            logger.exception('Error al generar el PDF del listado de recepciones')
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500,
            )

    @action(detail=False, methods=['get'], url_path='export-excel')
    def export_excel(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=403)

        empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)
        config = ExcelExportConfig(
            title='Listado de Recepciones',
            filename='listado_recepciones.xlsx',
            headers=self.EXPORT_HEADERS,
            empresa=empresa,
            taller=taller,
            usuario=usuario_nombre,
            row_builder=self._export_valores,
        )
        try:
            return ExcelExportService(config, self._export_queryset()).generate_response()
        except ExporteDemasiadoGrande:
            raise  # DRF responde 400 con el detalle
        except Exception as e:
            logger.exception('Error al generar el Excel del listado de recepciones')
            return Response(
                {'detail': f'No se pudo generar el Excel en este momento. ({str(e)})'},
                status=500,
            )

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return RecepcionVehiculo.objects.none()
        queryset = (
            RecepcionVehiculo.objects.filter(empresa_id=empresa_id)
            .select_related('cliente', 'vehiculo', 'orden_trabajo', 'sucursal')
            .prefetch_related('inspecciones', 'cotizaciones_generadas')
        )
        return self.filtrar_por_taller(self._filtrar_recepciones(queryset))

    def _filtrar_recepciones(self, queryset):
        """Aplica los filtros del panel de búsqueda del listado de recepciones."""
        params = self.request.query_params

        for parametro, campo in self.FILTROS_LISTADO.items():
            valor = params.get(parametro)
            if valor:
                queryset = queryset.filter(**{campo: valor})

        # Rango de fechas sobre la fecha de ingreso (comparación por día, ambos
        # extremos incluidos) para que el filtro funcione en cualquier zona horaria.
        fecha_desde = parsear_fecha(params.get('fecha_desde'))
        if fecha_desde:
            queryset = queryset.filter(fecha_ingreso__date__gte=fecha_desde)

        fecha_hasta = parsear_fecha(params.get('fecha_hasta'))
        if fecha_hasta:
            queryset = queryset.filter(fecha_ingreso__date__lte=fecha_hasta)

        if params.get('solo_grua') in ('1', 'true', 'True'):
            queryset = queryset.filter(ingreso_en_grua=True)

        return queryset

    @staticmethod
    def _parse_fecha(valor):
        return parsear_fecha(valor)

    def _sincronizar_kilometraje_vehiculo(self, instance):
        if instance.estado == 'NO_ACEPTADA':
            return
        if not instance.vehiculo_id or not instance.kilometraje_ingreso:
            return
        # El odómetro maestro solo sube; una lectura menor es un error de captura.
        if instance.kilometraje_ingreso > (instance.vehiculo.kilometraje_actual or 0):
            instance.vehiculo.kilometraje_actual = instance.kilometraje_ingreso
            instance.vehiculo.save(update_fields=['kilometraje_actual', 'updated_at'])

    def perform_create(self, serializer):
        instance = serializer.save()
        self._sincronizar_kilometraje_vehiculo(instance)
        if instance.firma_cliente and not instance.aceptacion_condiciones:
            instance.fecha_firma_cliente = timezone.now()
            instance.aceptacion_condiciones = True
            instance.estado = 'ACEPTADA'
            instance.save(update_fields=['fecha_firma_cliente', 'aceptacion_condiciones', 'estado'])
        self._conectar_cotizacion(instance)

    def _conectar_cotizacion(self, recepcion):
        """Liga la recepción con la cotización vigente del vehículo, si existe.

        Cubre el caso en que el cliente llega sin cita pero ya tenía una
        cotización abierta (por ejemplo, la que pidió por WhatsApp).
        """
        from apps.cotizaciones.services import conectar_recepcion

        conectar_recepcion(recepcion)

    def perform_update(self, serializer):
        instance = serializer.save()
        self._sincronizar_kilometraje_vehiculo(instance)
        if instance.firma_cliente and not instance.aceptacion_condiciones:
            instance.fecha_firma_cliente = timezone.now()
            instance.aceptacion_condiciones = True
            instance.estado = 'ACEPTADA'
            instance.save(update_fields=['fecha_firma_cliente', 'aceptacion_condiciones', 'estado'])
        elif not instance.firma_cliente and instance.aceptacion_condiciones:
            instance.fecha_firma_cliente = None
            instance.aceptacion_condiciones = False
            instance.estado = 'PENDIENTE'
            instance.save(update_fields=['fecha_firma_cliente', 'aceptacion_condiciones', 'estado'])

    @action(detail=True, methods=['post'], url_path='firmar')
    def firmar(self, request, pk=None):
        """Registra la firma del cliente y deja la recepción como ACEPTADA.

        Cubre dos casos: una recepción PENDIENTE sin firma y una NO_ACEPTADA
        cuando el cliente cambia de opinión. La transición es directa a
        ACEPTADA (nunca pasa por PENDIENTE) y es atómica.
        """
        recepcion = self.get_object()
        if recepcion.estado not in ('PENDIENTE', 'NO_ACEPTADA'):
            return Response(
                {'detail': 'Solo se puede firmar una recepción pendiente o no aceptada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        firma = request.data.get('firma_cliente')
        if not firma or not str(firma).startswith('data:image'):
            return Response(
                {'firma_cliente': 'La firma del cliente es obligatoria.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recepcion.firma_cliente = firma
        recepcion.aceptacion_condiciones = True
        recepcion.fecha_firma_cliente = timezone.now()
        recepcion.estado = 'ACEPTADA'
        recepcion.motivo_no_recepcion = ''
        recepcion.save(update_fields=[
            'firma_cliente',
            'aceptacion_condiciones',
            'fecha_firma_cliente',
            'estado',
            'motivo_no_recepcion',
            'updated_at',
        ])
        self._sincronizar_kilometraje_vehiculo(recepcion)

        return Response(self.get_serializer(recepcion).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get', 'post'], url_path='relaciones')
    def relaciones(self, request, pk=None):
        """Vincula o desvincula relaciones de flujo sin borrar entidades."""
        return responder_relaciones(request, self.get_object(), 'recepcion')

    @action(detail=True, methods=['post'], url_path='crear-inspeccion')
    def crear_inspeccion(self, request, pk=None):
        recepcion = self.get_object()
        if recepcion.estado != 'ACEPTADA':
            return Response(
                {'detail': 'Solo puede crearse una inspección desde una recepción aceptada y firmada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if recepcion.inspecciones.exists():
            return Response(
                {'detail': 'Esta recepción ya tiene una inspección registrada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        testigo_campos = [
            f.name
            for f in RecepcionVehiculo._meta.fields
            if f.name.startswith('testigo_')
        ]
        data = {
            'recepcion': recepcion.id,
            'tipo_inspeccion': tipo_inspeccion_desde_recepcion(recepcion.tipo_recepcion),
            'estado': 'PENDIENTE',
            'motivo_ingreso': recepcion.motivo_ingreso or '',
            'otros_testigos_observaciones': recepcion.otros_testigos_observaciones or '',
            'fecha_inspeccion': recepcion.fecha_ingreso or timezone.now(),
            **{campo: getattr(recepcion, campo, False) for campo in testigo_campos},
        }
        serializer = InspeccionVehiculoSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        inspeccion = serializer.save()
        return Response(
            InspeccionVehiculoSerializer(inspeccion, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class InspeccionVehiculoViewSet(FiltroTallerMixin, viewsets.ModelViewSet):
    serializer_class = InspeccionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('inspecciones')]
    taller_filter_field = 'sucursal'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    # La búsqueda alcanza el número de la inspección, el de su recepción y los
    # datos del vehículo/cliente, que pueden venir en la propia inspección o en
    # la recepción de origen.
    search_fields = [
        'numero_inspeccion',
        'recepcion__numero_recepcion',
        'vehiculo__placa',
        'vehiculo__marca',
        'vehiculo__modelo',
        'cliente__nombre',
        'cliente__identificacion',
        'recepcion__vehiculo__placa',
        'recepcion__vehiculo__marca',
        'recepcion__cliente__nombre',
        'recepcion__cliente__identificacion',
    ]
    ordering_fields = ['created_at', 'id', 'fecha_inspeccion', 'fecha_finalizacion', 'numero_inspeccion']
    ordering = ['-created_at']

    # Filtros del panel "Búsqueda" del listado de inspecciones. Se aplican por
    # query params (no django-filter) siguiendo el criterio de recepciones.
    FILTROS_LISTADO = {
        'estado': 'estado',
        'tipo_inspeccion': 'tipo_inspeccion',
    }

    @action(detail=False, methods=['get'], url_path='exportar-pdf', url_name='exportar-listado-pdf')
    def exportar_listado_pdf(self, request):
        """Listado PDF con los mismos filtros, búsqueda y recorte por taller del listado."""
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response({'detail': 'No se pudo determinar la empresa activa.'}, status=403)

        queryset = self.filter_queryset(self.get_queryset()).select_related('responsable').prefetch_related(None)
        empresa, taller, usuario_nombre = contexto_reporte(request, empresa_id)

        def row_builder(inspeccion, cell_style):
            recepcion = inspeccion.recepcion
            vehiculo = inspeccion.vehiculo or (recepcion.vehiculo if recepcion else None)
            cliente = inspeccion.cliente or (recepcion.cliente if recepcion else None)
            responsable = inspeccion.responsable
            return [
                celda_texto(inspeccion.numero_inspeccion or f'#{inspeccion.id}', cell_style),
                celda_texto(vehiculo.placa if vehiculo else None, cell_style),
                celda_texto(cliente.nombre if cliente else None, cell_style),
                celda_texto(inspeccion.get_tipo_inspeccion_display(), cell_style),
                celda_texto(
                    (responsable.get_full_name() or responsable.username) if responsable else None,
                    cell_style,
                ),
                celda_texto(inspeccion.get_estado_display(), cell_style),
                celda_texto(fecha_hora_local(inspeccion.fecha_inspeccion or inspeccion.created_at), cell_style),
            ]

        config = PdfExportConfig(
            title='Listado de Inspecciones',
            filename='listado_inspecciones.pdf',
            headers=[
                ('Nº Inspección', 1.2),
                ('Placa', 0.9),
                ('Cliente', 2.0),
                ('Tipo', 1.1),
                ('Responsable', 1.5),
                ('Estado', 1.1),
                ('Fecha', 1.1),
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
            logger.exception('Error al generar el PDF del listado de inspecciones')
            return Response(
                {'detail': f'No se pudo generar el PDF en este momento. ({str(e)})'},
                status=500,
            )

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return InspeccionVehiculo.objects.none()
        queryset = (
            InspeccionVehiculo.objects.filter(empresa_id=empresa_id)
            .select_related(
                'recepcion',
                'recepcion__vehiculo',
                'recepcion__cliente',
                'vehiculo',
                'cliente',
                'orden_trabajo',
                'sucursal',
            )
            .prefetch_related('servicios_detectados', 'repuestos_sugeridos', 'fotos', 'cotizaciones_generadas')
        )
        return self.filtrar_por_taller(self._filtrar_inspecciones(queryset))

    def _filtrar_inspecciones(self, queryset):
        """Aplica los filtros del panel de búsqueda del listado de inspecciones."""
        params = self.request.query_params

        for parametro, campo in self.FILTROS_LISTADO.items():
            valor = params.get(parametro)
            if valor:
                queryset = queryset.filter(**{campo: valor})

        if params.get('sin_recepcion') in ('1', 'true', 'True'):
            queryset = queryset.filter(recepcion__isnull=True)

        # Rango de fechas sobre la fecha de inspección (comparación por día,
        # ambos extremos incluidos) para que el filtro no dependa de la zona horaria.
        fecha_desde = parsear_fecha(params.get('fecha_desde'))
        if fecha_desde:
            queryset = queryset.filter(fecha_inspeccion__date__gte=fecha_desde)

        fecha_hasta = parsear_fecha(params.get('fecha_hasta'))
        if fecha_hasta:
            queryset = queryset.filter(fecha_inspeccion__date__lte=fecha_hasta)

        return queryset

    @staticmethod
    def _check_editable(inspeccion):
        if inspeccion.orden_trabajo_id:
            raise serializers.ValidationError(
                'La inspección ya se convirtió en orden de trabajo y no puede eliminarse.'
            )

    def destroy(self, request, *args, **kwargs):
        self._check_editable(self.get_object())
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get', 'post'], url_path='relaciones')
    def relaciones(self, request, pk=None):
        """Vincula o desvincula relaciones de flujo sin borrar entidades."""
        return responder_relaciones(request, self.get_object(), 'inspeccion')

class DetalleServicioInspeccionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleServicioInspeccionSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('inspecciones')]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleServicioInspeccion.objects.none()
        queryset = DetalleServicioInspeccion.objects.filter(
            inspeccion__empresa_id=empresa_id
        ).select_related('inspeccion', 'servicio')
        inspeccion_id = self.request.query_params.get('inspeccion')
        if inspeccion_id:
            queryset = queryset.filter(inspeccion_id=inspeccion_id)
        return queryset

    def perform_create(self, serializer):
        _check_inspeccion_editable(serializer.validated_data.get('inspeccion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_inspeccion_editable(self.get_object().inspeccion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_inspeccion_editable(instance.inspeccion)
        super().perform_destroy(instance)


class DetalleRepuestoInspeccionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleRepuestoInspeccionSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('inspecciones')]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleRepuestoInspeccion.objects.none()
        queryset = DetalleRepuestoInspeccion.objects.filter(
            inspeccion__empresa_id=empresa_id
        ).select_related('inspeccion', 'repuesto')
        inspeccion_id = self.request.query_params.get('inspeccion')
        if inspeccion_id:
            queryset = queryset.filter(inspeccion_id=inspeccion_id)
        return queryset

    def perform_create(self, serializer):
        _check_inspeccion_editable(serializer.validated_data.get('inspeccion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_inspeccion_editable(self.get_object().inspeccion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_inspeccion_editable(instance.inspeccion)
        super().perform_destroy(instance)


class FotoInspeccionViewSet(viewsets.ModelViewSet):
    serializer_class = FotoInspeccionSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('inspecciones')]
    filter_backends = [filters.OrderingFilter]
    ordering = ['created_at', 'id']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return FotoInspeccion.objects.none()
        queryset = FotoInspeccion.objects.filter(
            inspeccion__empresa_id=empresa_id
        ).select_related('inspeccion')
        inspeccion_id = self.request.query_params.get('inspeccion')
        if inspeccion_id:
            queryset = queryset.filter(inspeccion_id=inspeccion_id)
        return queryset

    def perform_create(self, serializer):
        _check_inspeccion_editable(serializer.validated_data.get('inspeccion'))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        _check_inspeccion_editable(self.get_object().inspeccion)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_inspeccion_editable(instance.inspeccion)
        super().perform_destroy(instance)


def _check_orden_editable(orden):
    if orden is None:
        logger.warning('_check_orden_editable called with orden=None (orden_trabajo not resolved)')
        return
    try:
        if orden.estado == 'CANCELADO':
            raise serializers.ValidationError(
                'La orden de trabajo está cancelada; no se pueden modificar sus detalles.'
            )
    except OrdenTrabajo.DoesNotExist:
        logger.error('_check_orden_editable: OrdenTrabajo.DoesNotExist for pk=%s', getattr(orden, 'pk', orden))
        raise


class FotoOrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = FotoOrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('ordenes')]
    filter_backends = [filters.OrderingFilter]
    ordering = ['created_at', 'id']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return FotoOrdenTrabajo.objects.none()
        queryset = FotoOrdenTrabajo.objects.filter(
            orden_trabajo__empresa_id=empresa_id
        ).select_related('orden_trabajo')
        orden_id = self.request.query_params.get('orden')
        if orden_id:
            queryset = queryset.filter(orden_trabajo_id=orden_id)
        return queryset

    def perform_create(self, serializer):
        try:
            _check_orden_editable(serializer.validated_data.get('orden_trabajo'))
            super().perform_create(serializer)
        except OrdenTrabajo.DoesNotExist:
            empresa_id = get_empresa_id_desde_request(self.request)
            logger.error(
                'FotoOrdenTrabajoViewSet.perform_create: OrdenTrabajo.DoesNotExist. '
                'empresa_id=%s, orden_trabajo=%s, user=%s',
                empresa_id,
                serializer.validated_data.get('orden_trabajo'),
                getattr(self.request.user, 'username', None),
            )
            raise serializers.ValidationError(
                {'orden_trabajo': 'La orden de trabajo seleccionada no existe o no pertenece a tu empresa.'}
            )

    def perform_update(self, serializer):
        _check_orden_editable(self.get_object().orden_trabajo)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        _check_orden_editable(instance.orden_trabajo)
        super().perform_destroy(instance)


class DetalleServicioOrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleServicioOrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('ordenes')]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['id']
    ordering = ['id']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleServicioOrdenTrabajo.objects.none()
        queryset = DetalleServicioOrdenTrabajo.objects.filter(
            orden_trabajo__empresa_id=empresa_id
        ).select_related('orden_trabajo')
        orden_id = self.request.query_params.get('orden')
        if orden_id:
            queryset = queryset.filter(orden_trabajo_id=orden_id)
        return queryset

    def perform_create(self, serializer):
        orden = serializer.validated_data.get('orden_trabajo')
        try:
            _check_orden_editable(orden)
            serializer.save()
            orden.calcular_totales()
        except OrdenTrabajo.DoesNotExist:
            empresa_id = get_empresa_id_desde_request(self.request)
            logger.error(
                'DetalleServicioOrdenTrabajoViewSet.perform_create: OrdenTrabajo.DoesNotExist. '
                'empresa_id=%s, orden_trabajo=%s, user=%s',
                empresa_id,
                orden,
                getattr(self.request.user, 'username', None),
            )
            raise serializers.ValidationError(
                {'orden_trabajo': 'La orden de trabajo seleccionada no existe o no pertenece a tu empresa.'}
            )

    def perform_update(self, serializer):
        _check_orden_editable(self.get_object().orden_trabajo)
        serializer.save()
        serializer.instance.orden_trabajo.calcular_totales()

    def perform_destroy(self, instance):
        orden = instance.orden_trabajo
        _check_orden_editable(orden)
        instance.delete()
        orden.calcular_totales()


class DetalleRepuestoOrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleRepuestoOrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('ordenes')]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['id']
    ordering = ['id']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return DetalleRepuestoOrdenTrabajo.objects.none()
        queryset = DetalleRepuestoOrdenTrabajo.objects.filter(
            orden_trabajo__empresa_id=empresa_id
        ).select_related('orden_trabajo')
        orden_id = self.request.query_params.get('orden')
        if orden_id:
            queryset = queryset.filter(orden_trabajo_id=orden_id)
        return queryset

    def perform_create(self, serializer):
        orden = serializer.validated_data.get('orden_trabajo')
        try:
            _check_orden_editable(orden)
            serializer.save()
            orden.calcular_totales()
        except OrdenTrabajo.DoesNotExist:
            empresa_id = get_empresa_id_desde_request(self.request)
            logger.error(
                'DetalleRepuestoOrdenTrabajoViewSet.perform_create: OrdenTrabajo.DoesNotExist. '
                'empresa_id=%s, orden_trabajo=%s, user=%s',
                empresa_id,
                orden,
                getattr(self.request.user, 'username', None),
            )
            raise serializers.ValidationError(
                {'orden_trabajo': 'La orden de trabajo seleccionada no existe o no pertenece a tu empresa.'}
            )

    def perform_update(self, serializer):
        _check_orden_editable(self.get_object().orden_trabajo)
        serializer.save()
        serializer.instance.orden_trabajo.calcular_totales()

    def perform_destroy(self, instance):
        orden = instance.orden_trabajo
        _check_orden_editable(orden)
        instance.delete()
        orden.calcular_totales()
