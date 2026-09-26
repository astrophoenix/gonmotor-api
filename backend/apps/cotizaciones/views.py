from django.db import IntegrityError

from rest_framework import filters, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.utils import get_empresa_id_desde_request

from .models import Cotizacion, DetalleRepuestoCotizacion, DetalleServicioCotizacion
from .serializers import (
    CotizacionSerializer,
    DetalleRepuestoCotizacionSerializer,
    DetalleServicioCotizacionSerializer,
    ESTADOS_EDITABLES,
)


def _buscar_cotizacion_vigente(queryset, excluir_id=None):
    queryset = queryset.filter(estado__in=Cotizacion.ESTADOS_VIGENTES)
    if excluir_id is not None:
        queryset = queryset.exclude(pk=excluir_id)
    return queryset.first()


def _validar_origen_cotizacion(inspeccion=None, recepcion=None):
    """Impide dos cotizaciones vigentes para la misma recepción o inspección.

    Una cotización vigente (borrador, enviada o aceptada) mantiene el trabajo
    comprometido, así que solo puede existir una. El trabajo adicional se
    cotiza desde la orden de trabajo, no desde una segunda cotización del
    mismo origen.

    Una cotización puede crearse sin inspección previa (consulta por
    teléfono, mantenimientos programados), por lo que ninguno de los dos
    orígenes es obligatorio.
    """
    if inspeccion is not None:
        if inspeccion.orden_trabajo_id:
            raise serializers.ValidationError(
                'La inspección ya se convirtió en orden de trabajo; no se puede cotizar nuevamente.'
            )
        vigente = _buscar_cotizacion_vigente(inspeccion.cotizaciones_generadas)
        if vigente is not None:
            raise serializers.ValidationError(
                f'La inspección {inspeccion.numero_inspeccion} ya tiene la cotización vigente '
                f'{vigente.numero_cotizacion}. Edítala o ciérrala antes de crear otra.'
            )

    if recepcion is not None:
        vigente = _buscar_cotizacion_vigente(recepcion.cotizaciones_generadas)
        if vigente is not None:
            raise serializers.ValidationError(
                f'La recepción {recepcion.numero_recepcion} ya tiene la cotización vigente '
                f'{vigente.numero_cotizacion}. Edítala o ciérrala antes de crear otra.'
            )


def _validar_origen_inspeccion(inspeccion):
    """Valida solo el origen inspección (compatibilidad con llamadas existentes)."""
    _validar_origen_cotizacion(inspeccion=inspeccion)


CONSTRAINT_RECEPCION = 'cotizacion_recepcion_vigente_unica'
CONSTRAINT_INSPECCION = 'cotizacion_inspeccion_vigente_unica'


def _detalle_cotizacion_conflicto(origen, excluir_id=None):
    """Describe la cotización vigente que impede continuar, para el mensaje de error."""
    if origen is None:
        return ''
    vigente = _buscar_cotizacion_vigente(origen.cotizaciones_generadas, excluir_id=excluir_id)
    if vigente is None:
        return ''
    return f' Ya está abierta la cotización {vigente.numero_cotizacion} ({vigente.get_estado_display()}).'


def _traducir_integridad(error, inspeccion=None, recepcion=None, excluir_id=None):
    """Convierte el IntegrityError de los constraints de vigencia en un error de validación.

    La validación de origen corre antes de guardar, pero dos peticiones
    simultáneas pueden pasar el chequeo a la vez; en ese caso el constraint de
    base de datos es el que resuelve la carrera. Los IntegrityError que no son
    de vigencia se devuelven sin tocar.
    """
    if CONSTRAINT_INSPECCION in str(error):
        detalle = _detalle_cotizacion_conflicto(inspeccion, excluir_id=excluir_id)
        raise serializers.ValidationError(
            f'La inspección ya tiene una cotización vigente (borrador, enviada o aceptada).{detalle} '
            f'Ciérrala o edítala antes de continuar.'
        ) from error
    if CONSTRAINT_RECEPCION in str(error):
        detalle = _detalle_cotizacion_conflicto(recepcion, excluir_id=excluir_id)
        raise serializers.ValidationError(
            f'La recepción ya tiene una cotización vigente (borrador, enviada o aceptada).{detalle} '
            f'Ciérrala o edítala antes de continuar.'
        ) from error
    return error


class CotizacionViewSet(viewsets.ModelViewSet):
    serializer_class = CotizacionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['numero_cotizacion', 'cliente__nombre', 'vehiculo__placa']
    ordering_fields = ['numero_cotizacion', 'created_at', 'total']
    ordering = ['-created_at']

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return Cotizacion.objects.none()
        return (
            Cotizacion.objects.filter(empresa_id=empresa_id)
            .select_related('cliente', 'vehiculo', 'sucursal', 'recepcion_origen', 'inspeccion_origen', 'orden_trabajo_origen', 'orden_trabajo')
            .prefetch_related('servicios', 'repuestos')
        )

    def perform_create(self, serializer):
        inspeccion = serializer.validated_data.get('inspeccion_origen')
        recepcion = serializer.validated_data.get('recepcion_origen')
        _validar_origen_cotizacion(inspeccion=inspeccion, recepcion=recepcion)
        try:
            super().perform_create(serializer)
        except IntegrityError as error:
            _traducir_integridad(error, inspeccion=inspeccion, recepcion=recepcion)
            raise

    def perform_update(self, serializer):
        # Reabrir una cotización cerrada (RECHAZADA/VENCIDA/ACEPTADA → ENVIADA)
        # la vuelve a poner en juego y puede chocar con otra vigente del mismo
        # origen. Aquí el constraint de la base de datos es el que resuelve la
        # carrera, porque la cotización que se está guardando es ella misma una
        # vigente y la validación de origen no aplica.
        instancia = serializer.instance
        try:
            super().perform_update(serializer)
        except IntegrityError as error:
            _traducir_integridad(
                error,
                inspeccion=instancia.inspeccion_origen,
                recepcion=instancia.recepcion_origen,
                excluir_id=instancia.pk,
            )
            raise

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
    def convertir_a_orden(self, request, pk=None):
        cotizacion = self.get_object()
        try:
            ot = cotizacion.convertir_a_orden(usuario=request.user)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return Response({
            'id': ot.id,
            'numero_orden': ot.numero_orden,
            'estado': cotizacion.estado,
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


def _check_cotizacion_editable(cotizacion):
    if cotizacion is not None and cotizacion.estado not in ESTADOS_EDITABLES:
        raise serializers.ValidationError(
            f'La cotización está "{cotizacion.get_estado_display()}" y sus ítems no pueden modificarse.'
        )


class DetalleServicioCotizacionViewSet(viewsets.ModelViewSet):
    serializer_class = DetalleServicioCotizacionSerializer
    permission_classes = [permissions.IsAuthenticated]
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
    permission_classes = [permissions.IsAuthenticated]
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