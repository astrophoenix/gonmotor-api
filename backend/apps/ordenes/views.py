import datetime

from django.db.models import Q
from django.utils import timezone
from rest_framework import filters, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.utils import get_empresa_id_desde_request
from apps.authentication.permissions import TieneRecurso
from apps.core.utils.excel_export import ExcelExportConfig, ExcelExportService
from apps.core.utils.pdf_export import PdfExportConfig, PdfExportService

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


def _serializar_cotizacion_candidata(cotizacion, inspeccion=None, recepcion=None):
    """Vista de una cotización del vehículo para las pantallas de decisión."""
    vinculada = False
    if inspeccion is not None and cotizacion.inspeccion_origen_id == inspeccion.id:
        vinculada = True
    if recepcion is not None and cotizacion.recepcion_origen_id == recepcion.id:
        vinculada = True
    return {
        'id': cotizacion.id,
        'numero': cotizacion.numero_cotizacion,
        'estado': cotizacion.estado,
        'estadoDisplay': cotizacion.get_estado_display(),
        'total': str(cotizacion.total),
        'createdAt': cotizacion.created_at.isoformat(),
        'fechaAceptacion': (
            cotizacion.fecha_aceptacion.isoformat() if cotizacion.fecha_aceptacion else None
        ),
        'vinculada': vinculada,
        'servicios': [
            {
                'codigo': item.codigo or '',
                'descripcion': item.descripcion,
                'cantidad': str(item.horas_estimadas),
                'precio': str(item.precio_unitario),
            }
            for item in cotizacion.servicios.all()
        ],
        'repuestos': [
            {
                'codigo': item.codigo_repuesto or '',
                'descripcion': item.descripcion,
                'cantidad': str(item.cantidad),
                'precio': str(item.precio_unitario_referencial),
            }
            for item in cotizacion.repuestos.all()
        ],
    }


def _clasificar_cotizaciones_candidatas(cotizaciones, inspeccion=None, recepcion=None):
    """Agrupa las cotizaciones del vehículo por lo que el usuario puede hacer."""
    from apps.cotizaciones.models import Cotizacion

    vigente_pediente = (Cotizacion.EstadoCotizacion.PENDIENTE, Cotizacion.EstadoCotizacion.ENVIADA)
    return {
        'aprobadas': [
            _serializar_cotizacion_candidata(c, inspeccion=inspeccion, recepcion=recepcion)
            for c in cotizaciones
            if c.estado == Cotizacion.EstadoCotizacion.ACEPTADA
        ],
        'enCurso': [
            _serializar_cotizacion_candidata(c, inspeccion=inspeccion, recepcion=recepcion)
            for c in cotizaciones
            if c.estado in vigente_pediente
        ],
        'historicas': [
            _serializar_cotizacion_candidata(c, inspeccion=inspeccion, recepcion=recepcion)
            for c in cotizaciones
            if c.estado != Cotizacion.EstadoCotizacion.ACEPTADA and c.estado not in vigente_pediente
        ],
    }


def _cotizaciones_del_vehiculo(empresa_id, vehiculo_id, recepcion_id=None, inspeccion=None):
    """Cotizaciones de la empresa para el vehículo, con sus ítems cargados."""
    from apps.cotizaciones.models import Cotizacion

    queryset = Cotizacion.objects.filter(empresa_id=empresa_id)
    if vehiculo_id:
        queryset = queryset.filter(vehiculo_id=vehiculo_id)
    elif recepcion_id:
        queryset = queryset.filter(
            Q(inspeccion_origen=inspeccion) | Q(recepcion_origen=recepcion_id)
        )
    else:
        queryset = queryset.none()
    return list(
        queryset.select_related('cliente')
        .prefetch_related('servicios', 'repuestos')
        .order_by('-created_at', '-id')
    )


class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = OrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('ordenes')]
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

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return OrdenTrabajo.objects.none()
        queryset = (
            OrdenTrabajo.objects.filter(empresa_id=empresa_id)
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

        sucursal = params.get('sucursal')
        if sucursal and sucursal.isdigit():
            queryset = queryset.filter(sucursal_id=int(sucursal))

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


class RecepcionVehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = RecepcionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('recepciones')]
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

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return RecepcionVehiculo.objects.none()
        queryset = (
            RecepcionVehiculo.objects.filter(empresa_id=empresa_id)
            .select_related('cliente', 'vehiculo', 'orden_trabajo', 'sucursal')
            .prefetch_related('inspecciones', 'cotizaciones_generadas')
        )
        return self._filtrar_recepciones(queryset)

    def _filtrar_recepciones(self, queryset):
        """Aplica los filtros del panel de búsqueda del listado de recepciones."""
        params = self.request.query_params

        for parametro, campo in self.FILTROS_LISTADO.items():
            valor = params.get(parametro)
            if valor:
                queryset = queryset.filter(**{campo: valor})

        sucursal = params.get('sucursal')
        if sucursal and sucursal.isdigit():
            queryset = queryset.filter(sucursal_id=int(sucursal))

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

    @action(detail=True, methods=['get', 'post'], url_path='relaciones')
    def relaciones(self, request, pk=None):
        """Vincula o desvincula relaciones de flujo sin borrar entidades."""
        return responder_relaciones(request, self.get_object(), 'recepcion')

    @action(detail=True, methods=['get'], url_path='cotizaciones-candidatas')
    def cotizaciones_candidatas(self, request, pk=None):
        """Cotizaciones del vehículo antes de crear la inspección.

        La pantalla de creación necesita mostrarlas para que el usuario elija
        cuáles se cargan como trabajo acordado, en lugar de atar sola la
        cotización vigente más reciente.
        """
        recepcion = self.get_object()
        cotizaciones = _cotizaciones_del_vehiculo(
            recepcion.empresa_id, recepcion.vehiculo_id, recepcion_id=recepcion.id
        )
        grupos = _clasificar_cotizaciones_candidatas(cotizaciones, recepcion=recepcion)
        return Response(
            {
                'recepcion': {
                    'id': recepcion.id,
                    'numero': recepcion.numero_recepcion,
                    'estado': recepcion.estado,
                },
                'vehiculo': {
                    'id': recepcion.vehiculo_id,
                    'placa': getattr(recepcion.vehiculo, 'placa', '') if recepcion.vehiculo_id else '',
                },
                'tieneInspeccion': recepcion.inspecciones.exists(),
                **grupos,
            }
        )

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
        # La inspección hereda cliente/vehículo del serializer; además se ata a las
        # cotizaciones que el usuario eligió y arranca con sus ítems copiados,
        # para que el técnico solo tenga que agregar lo que encuentre. Sin
        # selección explícita se conserva la cotización vigente más reciente.
        self._conectar_cotizaciones_seleccionadas(request, recepcion, inspeccion)
        return Response(
            InspeccionVehiculoSerializer(inspeccion, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _conectar_cotizaciones_seleccionadas(request, recepcion, inspeccion):
        """Ata las cotizaciones marcadas por el usuario y siembra sus ítems."""
        from apps.cotizaciones.models import Cotizacion
        from apps.cotizaciones.services import conectar_inspeccion, sembrar_inspeccion_desde_cotizacion, vincular_cotizacion

        ids = request.data.get('cotizaciones')
        # Sin el parámetro se conserva el vínculo automático histórico; una lista
        # vacía significa que el usuario revisó las cotizaciones y no eligió
        # ninguna, así que la inspección nace sin ítems cotizados.
        if ids is None or ids == '':
            conectar_inspeccion(inspeccion)
            return []

        if not isinstance(ids, list):
            raise serializers.ValidationError(
                {'cotizaciones': 'Envía la lista de ids de cotizaciones a relacionar.'}
            )

        candidatas = _cotizaciones_del_vehiculo(
            recepcion.empresa_id, recepcion.vehiculo_id, recepcion_id=recepcion.id
        )
        por_id = {cotizacion.id: cotizacion for cotizacion in candidatas}

        conectadas = []
        for valor in ids:
            try:
                cotizacion_id = int(valor)
            except (TypeError, ValueError):
                raise serializers.ValidationError({'cotizaciones': f'Id de cotización inválido: {valor!r}.'})
            cotizacion = por_id.get(cotizacion_id)
            if cotizacion is None:
                raise serializers.ValidationError(
                    {'cotizaciones': f'La cotización {cotizacion_id} no pertenece al vehículo de esta recepción.'}
                )
            # Una cotización se considera "convertida" si tiene una orden de trabajo asociada
            if cotizacion.orden_trabajo_origen_id or OrdenTrabajo.objects.filter(cotizacion_origen=cotizacion).exists():
                raise serializers.ValidationError(
                    {'cotizaciones': f'{cotizacion.numero_cotizacion} ya está convertida en orden de trabajo.'}
                )
            if cotizacion.inspeccion_origen_id and cotizacion.inspeccion_origen_id != inspeccion.id:
                raise serializers.ValidationError(
                    {'cotizaciones': f'{cotizacion.numero_cotizacion} ya está ligada a otra inspección.'}
                )
            if cotizacion.recepcion_origen_id and cotizacion.recepcion_origen_id != recepcion.id:
                raise serializers.ValidationError(
                    {'cotizaciones': f'{cotizacion.numero_cotizacion} está ligada a otra recepción.'}
                )
            vincular_cotizacion(cotizacion, recepcion=recepcion, inspeccion=inspeccion)
            # Se siembran todas las seleccionadas, no solo la primera, por eso
            # la siembra no se limita a una inspección vacía.
            sembrar_inspeccion_desde_cotizacion(cotizacion, inspeccion, solo_si_vacia=False)
            conectadas.append(cotizacion)
        return conectadas


class InspeccionVehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = InspeccionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('inspecciones')]
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
        return self._filtrar_inspecciones(queryset)

    def _filtrar_inspecciones(self, queryset):
        """Aplica los filtros del panel de búsqueda del listado de inspecciones."""
        params = self.request.query_params

        for parametro, campo in self.FILTROS_LISTADO.items():
            valor = params.get(parametro)
            if valor:
                queryset = queryset.filter(**{campo: valor})

        if params.get('sin_recepcion') in ('1', 'true', 'True'):
            queryset = queryset.filter(recepcion__isnull=True)

        sucursal = params.get('sucursal')
        if sucursal and sucursal.isdigit():
            queryset = queryset.filter(sucursal_id=int(sucursal))

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

    @staticmethod
    def _vincular_cotizacion_inspeccion(cotizacion, inspeccion):
        """Liga la cotización con la inspección y con su recepción de origen."""
        campos = []
        if cotizacion.inspeccion_origen_id != inspeccion.id:
            cotizacion.inspeccion_origen = inspeccion
            campos.append('inspeccion_origen')
        if inspeccion.recepcion_id and cotizacion.recepcion_origen_id != inspeccion.recepcion_id:
            cotizacion.recepcion_origen_id = inspeccion.recepcion_id
            campos.append('recepcion_origen')
        if campos:
            cotizacion.save(update_fields=[*campos, 'updated_at'])

    @action(detail=True, methods=['get', 'post'], url_path='cotizaciones-candidatas')
    def cotizaciones_candidatas(self, request, pk=None):
        """Cotizaciones del vehículo para decidir el flujo de la inspección.

        No se limita a las cotizaciones ya ligadas a la inspección: también
        expone las aceptadas antes de la visita, que de otro modo el usuario
        no vería al momento de cotizar.
        """
        from apps.cotizaciones.models import Cotizacion

        inspeccion = self.get_object()
        vehiculo_id = inspeccion.vehiculo_id or (
            inspeccion.recepcion.vehiculo_id if inspeccion.recepcion_id else None
        )
        cotizaciones = _cotizaciones_del_vehiculo(
            inspeccion.empresa_id,
            vehiculo_id,
            recepcion_id=inspeccion.recepcion_id,
            inspeccion=inspeccion,
        )

        if request.method == 'GET':
            existentes = [c for c in cotizaciones if c.inspeccion_origen_id == inspeccion.id]
            grupos = _clasificar_cotizaciones_candidatas(cotizaciones, inspeccion=inspeccion)
            return Response(
                {
                    'inspeccion': {
                        'id': inspeccion.id,
                        'numero': inspeccion.numero_inspeccion,
                        'estado': inspeccion.estado,
                        'tieneRecepcion': bool(inspeccion.recepcion_id),
                    },
                    'vehiculo': {
                        'id': vehiculo_id,
                        'placa': getattr(inspeccion.vehiculo, 'placa', '') if inspeccion.vehiculo_id else '',
                    },
                    'cotizacionActivaId': existentes[0].id if existentes else None,
                    **grupos,
                }
            )

        accion = (request.data.get('accion') or 'vincular').strip().lower()
        if accion not in ('vincular', 'desvincular'):
            raise serializers.ValidationError({'accion': 'Usa "vincular" o "desvincular".'})
        ids = request.data.get('cotizaciones')
        if not isinstance(ids, list) or not ids:
            raise serializers.ValidationError(
                {'cotizaciones': 'Envía la lista de ids de cotizaciones a relacionar.'}
            )
        if inspeccion.orden_trabajo_id:
            raise serializers.ValidationError(
                {'detail': 'La inspección ya se convirtió en orden de trabajo.'}
            )

        resultados = []
        for valor in ids:
            try:
                cotizacion_id = int(valor)
            except (TypeError, ValueError):
                raise serializers.ValidationError({'cotizaciones': f'Id de cotización inválido: {valor!r}.'})
            cotizacion = next((c for c in cotizaciones if c.id == cotizacion_id), None)
            if cotizacion is None:
                raise serializers.ValidationError(
                    {'id': f'La cotización {cotizacion_id} no existe para el vehículo de la inspección.'}
                )
            if accion == 'vincular':
                # Una cotización se considera "convertida" si tiene una orden de trabajo asociada
                if cotizacion.orden_trabajo_origen_id or OrdenTrabajo.objects.filter(cotizacion_origen=cotizacion).exists():
                    raise serializers.ValidationError(
                        {'detail': f'{cotizacion.numero_cotizacion} ya está convertida en orden de trabajo.'}
                    )
                if (
                    cotizacion.recepcion_origen_id
                    and cotizacion.recepcion_origen_id != inspeccion.recepcion_id
                ):
                    raise serializers.ValidationError(
                        {
                            'detail': (
                                f'{cotizacion.numero_cotizacion} está ligada a otra recepción. '
                                'Desvincúlala primero.'
                            )
                        }
                    )
                if cotizacion.inspeccion_origen_id and cotizacion.inspeccion_origen_id != inspeccion.id:
                    raise serializers.ValidationError(
                        {'detail': f'{cotizacion.numero_cotizacion} ya está ligada a otra inspección.'}
                    )
                self._vincular_cotizacion_inspeccion(cotizacion, inspeccion)
                resultados.append({'id': cotizacion.id, 'vinculada': True})
            else:
                campos = []
                if cotizacion.inspeccion_origen_id == inspeccion.id:
                    cotizacion.inspeccion_origen = None
                    campos.append('inspeccion_origen')
                if inspeccion.recepcion_id and cotizacion.recepcion_origen_id == inspeccion.recepcion_id:
                    cotizacion.recepcion_origen = None
                    campos.append('recepcion_origen')
                if not campos:
                    raise serializers.ValidationError(
                        {'detail': f'{cotizacion.numero_cotizacion} no está relacionada con esta inspección.'}
                    )
                cotizacion.save(update_fields=[*campos, 'updated_at'])
                resultados.append({'id': cotizacion.id, 'vinculada': False})

        return Response({'ok': True, 'accion': accion, 'resultados': resultados})


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
    if orden is not None and orden.estado == 'CANCELADO':
        raise serializers.ValidationError(
            'La orden de trabajo está cancelada; no se pueden modificar sus detalles.'
        )


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
        _check_orden_editable(serializer.validated_data.get('orden_trabajo'))
        super().perform_create(serializer)

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
        _check_orden_editable(orden)
        serializer.save()
        orden.calcular_totales()

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
        _check_orden_editable(orden)
        serializer.save()
        orden.calcular_totales()

    def perform_update(self, serializer):
        _check_orden_editable(self.get_object().orden_trabajo)
        serializer.save()
        serializer.instance.orden_trabajo.calcular_totales()

    def perform_destroy(self, instance):
        orden = instance.orden_trabajo
        _check_orden_editable(orden)
        instance.delete()
        orden.calcular_totales()


class RecepcionPdfExportView(APIView):
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('recepciones')]
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
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('recepciones')]
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
