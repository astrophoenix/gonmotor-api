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


def _validar_origen_inspeccion(inspeccion):
    if inspeccion is None:
        return
    if inspeccion.orden_trabajo_id:
        raise serializers.ValidationError(
            'La inspección ya se convirtió en orden de trabajo; no se puede cotizar nuevamente.'
        )
    activa = inspeccion.cotizaciones_generadas.filter(
        estado__in=[Cotizacion.EstadoCotizacion.BORRADOR, Cotizacion.EstadoCotizacion.ENVIADA]
    ).exists()
    if activa:
        raise serializers.ValidationError(
            'Esta inspección ya tiene una cotización abierta. Edita la existente antes de crear otra.'
        )


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
            .select_related('cliente', 'vehiculo', 'sucursal', 'recepcion_origen', 'inspeccion_origen', 'orden_trabajo_origen')
            .prefetch_related('servicios', 'repuestos')
        )

    def perform_create(self, serializer):
        _validar_origen_inspeccion(serializer.validated_data.get('inspeccion_origen'))
        super().perform_create(serializer)

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