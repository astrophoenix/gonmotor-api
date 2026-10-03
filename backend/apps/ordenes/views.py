import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import filters, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.utils import get_empresa_id_desde_request
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


class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = OrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated]
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
    permission_classes = [permissions.IsAuthenticated]
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
        recepcion = self.get_object()
        if request.method == 'GET':
            return Response(self._serializar_relaciones(recepcion))

        tipo = request.data.get('tipo')
        entidad_id = request.data.get('id')
        accion = request.data.get('accion')
        if tipo not in {'cita', 'inspeccion', 'cotizacion', 'orden'}:
            raise serializers.ValidationError({'tipo': 'Tipo de relación no válido.'})
        if accion not in {'vincular', 'desvincular'}:
            raise serializers.ValidationError({'accion': 'Acción no válida.'})
        if not entidad_id:
            raise serializers.ValidationError({'id': 'Debes indicar la entidad relacionada.'})

        try:
            with transaction.atomic():
                self._actualizar_relacion(recepcion, tipo, entidad_id, accion)
        except IntegrityError as error:
            raise serializers.ValidationError(
                {'detail': 'La relación ya existe o entra en conflicto con otra relación del flujo.'}
            ) from error

        return Response({'ok': True, 'tipo': tipo, 'id': entidad_id, 'accion': accion})

    @staticmethod
    def _serializar_relaciones(recepcion):
        from django.db.models import Q

        from apps.citas.models import Cita
        from apps.cotizaciones.models import Cotizacion

        citas = Cita.objects.filter(recepcion_generada=recepcion).select_related('cliente', 'vehiculo')
        inspecciones = list(
            recepcion.inspecciones.select_related('orden_trabajo').order_by('created_at', 'id')
        )
        cotizaciones = list(
            Cotizacion.objects.filter(
                Q(recepcion_origen=recepcion) | Q(inspeccion_origen__recepcion=recepcion)
            ).select_related('cliente', 'vehiculo').distinct().order_by('created_at', 'id')
        )
        ordenes_generadas = set(
            OrdenTrabajo.objects.filter(cotizacion_origen__in=cotizaciones)
            .values_list('cotizacion_origen_id', flat=True)
        )
        cita_items = [
            {
                'id': cita.id,
                'label': f'Cita #{cita.id}',
                'numero': f'#{cita.id}',
                'estado': cita.estado,
                'estadoDisplay': cita.get_estado_display(),
                'fecha': cita.fecha_cita.isoformat() if cita.fecha_cita else '',
                'hora': cita.hora_cita.strftime('%H:%M') if cita.hora_cita else '',
                'url': f'/crud/citas/?id={cita.id}',
                'canDelete': True,
            }
            for cita in citas.order_by('fecha_cita', 'hora_cita', 'id')
        ]
        inspeccion_items = [
            {
                'id': inspeccion.id,
                'label': inspeccion.numero_inspeccion or f'#{inspeccion.id}',
                'numero': inspeccion.numero_inspeccion or f'#{inspeccion.id}',
                'estado': inspeccion.estado,
                'estadoDisplay': inspeccion.get_estado_display(),
                'url': f'/crud/inspecciones/ver/?id={inspeccion.id}',
                'canDelete': not bool(inspeccion.orden_trabajo_id),
                'deleteReason': 'La inspección está ligada a una orden.' if inspeccion.orden_trabajo_id else '',
            }
            for inspeccion in inspecciones
        ]
        cotizacion_items = [
            {
                'id': cotizacion.id,
                'label': cotizacion.numero_cotizacion or f'#{cotizacion.id}',
                'numero': cotizacion.numero_cotizacion or f'#{cotizacion.id}',
                'estado': cotizacion.estado,
                'estadoDisplay': cotizacion.get_estado_display(),
                'url': f'/crud/cotizaciones/ver/?id={cotizacion.id}',
                'canDelete': cotizacion.estado != 'CONVERTIDA' and cotizacion.id not in ordenes_generadas,
                'deleteReason': 'La cotización ya fue convertida a una orden.'
                if cotizacion.estado == 'CONVERTIDA' or cotizacion.id in ordenes_generadas else '',
            }
            for cotizacion in cotizaciones
        ]
        orden_items = []
        if recepcion.orden_trabajo_id:
            orden = recepcion.orden_trabajo
            orden_items.append({
                'id': orden.id,
                'label': orden.numero_orden,
                'numero': orden.numero_orden,
                'estado': orden.estado,
                'estadoDisplay': orden.get_estado_display(),
                'url': f'/crud/ordenes/ver/?id={orden.id}',
                'canDelete': True,
            })

        return {
            'relaciones': {
                'cita': cita_items,
                'inspeccion': inspeccion_items,
                'cotizacion': cotizacion_items,
                'orden': orden_items,
            },
            'puede_agregar': {
                'cita': True,
                'inspeccion': not inspeccion_items,
                'cotizacion': not any(
                    item['estado'] in Cotizacion.ESTADOS_VIGENTES for item in cotizacion_items
                ),
                'orden': not orden_items,
            },
        }

    @staticmethod
    def _validar_entidad_misma_visita(recepcion, entidad):
        if getattr(entidad, 'empresa_id', None) != recepcion.empresa_id:
            raise serializers.ValidationError({'detail': 'La entidad no pertenece a la empresa actual.'})
        if getattr(entidad, 'cliente_id', None) not in (None, recepcion.cliente_id):
            raise serializers.ValidationError({'detail': 'La entidad pertenece a otro cliente.'})
        if getattr(entidad, 'vehiculo_id', None) not in (None, recepcion.vehiculo_id):
            raise serializers.ValidationError({'detail': 'La entidad pertenece a otro vehículo.'})

    def _actualizar_relacion(self, recepcion, tipo, entidad_id, accion):
        from apps.citas.models import Cita
        from apps.cotizaciones.models import Cotizacion

        if tipo == 'cita':
            entidad = Cita.objects.filter(pk=entidad_id, empresa_id=recepcion.empresa_id).first()
            campo = 'recepcion_generada'
        elif tipo == 'inspeccion':
            entidad = InspeccionVehiculo.objects.filter(pk=entidad_id, empresa_id=recepcion.empresa_id).first()
            campo = 'recepcion'
        elif tipo == 'cotizacion':
            entidad = Cotizacion.objects.filter(pk=entidad_id, empresa_id=recepcion.empresa_id).first()
            campo = 'recepcion_origen'
        else:
            entidad = OrdenTrabajo.objects.filter(pk=entidad_id, empresa_id=recepcion.empresa_id).first()
            campo = 'orden_trabajo'

        if entidad is None:
            raise serializers.ValidationError({'id': 'No se encontró la entidad en la empresa actual.'})

        if tipo == 'orden':
            if accion == 'desvincular':
                if recepcion.orden_trabajo_id != entidad.pk:
                    raise serializers.ValidationError({'detail': 'La orden no está vinculada a esta recepción.'})
                recepcion.orden_trabajo = None
                recepcion.save(update_fields=['orden_trabajo', 'updated_at'])
                return
            self._validar_entidad_misma_visita(recepcion, entidad)
            if recepcion.orden_trabajo_id not in (None, entidad.pk):
                raise serializers.ValidationError({'detail': 'La recepción ya tiene otra orden relacionada.'})
            recepcion.orden_trabajo = entidad
            recepcion.save(update_fields=['orden_trabajo', 'updated_at'])
            return

        if tipo == 'cotizacion' and accion == 'desvincular':
            if OrdenTrabajo.objects.filter(cotizacion_origen=entidad).exists():
                raise serializers.ValidationError({'detail': 'No se puede desvincular una cotización que ya generó una orden.'})
            campos = []
            if entidad.recepcion_origen_id == recepcion.pk:
                entidad.recepcion_origen = None
                campos.append('recepcion_origen')
            if entidad.inspeccion_origen_id and entidad.inspeccion_origen.recepcion_id == recepcion.pk:
                entidad.inspeccion_origen = None
                campos.append('inspeccion_origen')
            if not campos:
                raise serializers.ValidationError({'detail': 'La cotización no está relacionada con esta recepción.'})
            entidad.save(update_fields=[*campos, 'updated_at'])
            return

        actual_id = getattr(entidad, f'{campo}_id')
        if accion == 'desvincular':
            if actual_id != recepcion.pk:
                raise serializers.ValidationError({'detail': 'La entidad no está vinculada a esta recepción.'})
            if tipo == 'inspeccion' and entidad.orden_trabajo_id:
                raise serializers.ValidationError({'detail': 'No se puede desvincular una inspección asociada a una orden.'})
            setattr(entidad, campo, None)
            entidad.save(update_fields=[campo, 'updated_at'])
            return

        self._validar_entidad_misma_visita(recepcion, entidad)
        if actual_id not in (None, recepcion.pk):
            raise serializers.ValidationError({'detail': 'La entidad ya está vinculada a otra recepción.'})
        if tipo == 'cita' and entidad.estado in ('CANCELADA', 'NO_ASISTIO'):
            raise serializers.ValidationError({'detail': 'No se puede relacionar una cita cancelada o no asistida.'})
        if tipo == 'inspeccion' and entidad.orden_trabajo_id:
            raise serializers.ValidationError({'detail': 'La inspección ya está asociada a una orden.'})
        if tipo == 'cotizacion':
            if entidad.estado == 'CONVERTIDA' or OrdenTrabajo.objects.filter(cotizacion_origen=entidad).exists():
                raise serializers.ValidationError({'detail': 'No se puede relacionar una cotización ya convertida en orden.'})
            vigente = Cotizacion.objects.filter(
                recepcion_origen=recepcion,
                estado__in=Cotizacion.ESTADOS_VIGENTES,
            ).exclude(pk=entidad.pk).exists()
            if vigente and entidad.estado in Cotizacion.ESTADOS_VIGENTES:
                raise serializers.ValidationError(
                    {'detail': 'La recepción ya tiene otra cotización vigente relacionada.'}
                )
            if entidad.inspeccion_origen_id and entidad.inspeccion_origen.recepcion_id not in (None, recepcion.pk):
                raise serializers.ValidationError({'detail': 'La inspección de origen pertenece a otra recepción.'})
        setattr(entidad, campo, recepcion)
        entidad.save(update_fields=[campo, 'updated_at'])

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
        # La inspección hereda cliente/vehículo del serializer; además se ata a
        # la cotización vigente del vehículo y arranca con sus ítems copiados,
        # para que el técnico solo tenga que agregar lo que encuentre.
        from apps.cotizaciones.services import conectar_inspeccion

        conectar_inspeccion(inspeccion)
        return Response(
            InspeccionVehiculoSerializer(inspeccion, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class InspeccionVehiculoViewSet(viewsets.ModelViewSet):
    serializer_class = InspeccionVehiculoSerializer
    permission_classes = [permissions.IsAuthenticated]
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


class DetalleServicioInspeccionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleServicioInspeccionSerializer
    permission_classes = [permissions.IsAuthenticated]
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
    permission_classes = [permissions.IsAuthenticated]
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
    permission_classes = [permissions.IsAuthenticated]
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
    permission_classes = [permissions.IsAuthenticated]
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
    permission_classes = [permissions.IsAuthenticated]
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
    permission_classes = [permissions.IsAuthenticated]
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
