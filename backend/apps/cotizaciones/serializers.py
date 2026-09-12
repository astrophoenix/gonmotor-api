from django.utils import timezone
from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request
from apps.empresas.services import generar_codigo_secuencial, resolver_taller

from .models import Cotizacion, DetalleRepuestoCotizacion, DetalleServicioCotizacion

ESTADOS_EDITABLES = {
    Cotizacion.EstadoCotizacion.BORRADOR,
    Cotizacion.EstadoCotizacion.ENVIADA,
}

# Transiciones de estado permitidas para el flujo del taller.
TRANSICIONES_VALIDAS = {
    Cotizacion.EstadoCotizacion.BORRADOR: {Cotizacion.EstadoCotizacion.ENVIADA},
    Cotizacion.EstadoCotizacion.ENVIADA: {
        Cotizacion.EstadoCotizacion.ACEPTADA,
        Cotizacion.EstadoCotizacion.RECHAZADA,
    },
    Cotizacion.EstadoCotizacion.RECHAZADA: {Cotizacion.EstadoCotizacion.ENVIADA},
    Cotizacion.EstadoCotizacion.VENCIDA: {Cotizacion.EstadoCotizacion.ENVIADA},
    Cotizacion.EstadoCotizacion.ACEPTADA: set(),
    Cotizacion.EstadoCotizacion.CONVERTIDA: set(),
}


def transicion_estado_valida(actual, nuevo):
    """Indica si se permite pasar del estado `actual` al estado `nuevo`."""
    if actual == nuevo:
        return True
    return nuevo in TRANSICIONES_VALIDAS.get(actual, set())


class DetalleServicioCotizacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleServicioCotizacion
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class DetalleRepuestoCotizacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleRepuestoCotizacion
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class CotizacionSerializer(serializers.ModelSerializer):
    servicios = DetalleServicioCotizacionSerializer(many=True, read_only=True)
    repuestos = DetalleRepuestoCotizacionSerializer(many=True, read_only=True)

    class Meta:
        model = Cotizacion
        fields = [
            'id',
            'empresa',
            'sucursal',
            'cliente',
            'vehiculo',
            'numero_cotizacion',
            'estado',
            'validez_dias',
            'subtotal',
            'total_iva',
            'total',
            'observaciones',
            'recepcion_origen',
            'inspeccion_origen',
            'orden_trabajo_origen',
            'fecha_aceptacion',
            'aceptada_por',
            'metodo_aceptacion',
            'servicios',
            'repuestos',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'empresa', 'numero_cotizacion', 'subtotal', 'total_iva', 'total', 'created_at', 'updated_at']

    def _derivar_cliente_vehiculo(self, attrs):
        """Completa cliente/vehículo desde la inspección o recepción de origen."""
        if attrs.get('cliente') and attrs.get('vehiculo'):
            return
        inspeccion = attrs.get('inspeccion_origen')
        recepcion = attrs.get('recepcion_origen')
        if recepcion is None and inspeccion is not None and inspeccion.recepcion_id:
            recepcion = inspeccion.recepcion
        if recepcion is None:
            return
        if not attrs.get('cliente') and recepcion.cliente_id:
            attrs['cliente'] = recepcion.cliente
        if not attrs.get('vehiculo') and recepcion.vehiculo_id:
            attrs['vehiculo'] = recepcion.vehiculo

    def validate(self, attrs):
        request = self.context.get('request')
        empresa_id = get_empresa_id_desde_request(request) if request else None
        instance = self.instance

        if instance is None:
            if empresa_id:
                inspeccion = attrs.get('inspeccion_origen')
                recepcion = attrs.get('recepcion_origen')
                cliente = attrs.get('cliente')
                vehiculo = attrs.get('vehiculo')
                if inspeccion is not None and inspeccion.empresa_id != empresa_id:
                    raise serializers.ValidationError(
                        {'inspeccion_origen': 'La inspección no pertenece a tu empresa.'}
                    )
                if recepcion is not None and recepcion.empresa_id != empresa_id:
                    raise serializers.ValidationError(
                        {'recepcion_origen': 'La recepción no pertenece a tu empresa.'}
                    )
                if cliente is not None and cliente.empresa_id != empresa_id:
                    raise serializers.ValidationError({'cliente': 'El cliente no pertenece a tu empresa.'})
                if vehiculo is not None and not vehiculo.empresas.filter(pk=empresa_id).exists():
                    raise serializers.ValidationError({'vehiculo': 'El vehículo no pertenece a tu empresa.'})
            self._derivar_cliente_vehiculo(attrs)
            if not attrs.get('cliente'):
                raise serializers.ValidationError({'cliente': 'Debes indicar el cliente de la cotización.'})
        else:
            for campo in ('inspeccion_origen', 'recepcion_origen', 'orden_trabajo_origen', 'sucursal'):
                if campo in attrs and attrs[campo] != getattr(instance, campo):
                    raise serializers.ValidationError(
                        {campo: 'No se puede modificar el origen de una cotización existente.'}
                    )

        if 'estado' in attrs:
            actual = instance.estado if instance else Cotizacion.EstadoCotizacion.BORRADOR
            nuevo = attrs['estado']
            if not transicion_estado_valida(actual, nuevo):
                raise serializers.ValidationError(
                    {'estado': f'No se permite pasar de "{actual}" a "{nuevo}".'}
                )
            if nuevo == Cotizacion.EstadoCotizacion.ACEPTADA:
                metodo = attrs.get('metodo_aceptacion') or (
                    instance.metodo_aceptacion if instance else None
                )
                if not metodo:
                    raise serializers.ValidationError(
                        {'metodo_aceptacion': 'Indica el método por el cual el cliente aceptó la cotización.'}
                    )
        elif instance is not None and instance.estado not in ESTADOS_EDITABLES:
            raise serializers.ValidationError(
                {'detail': f'La cotización está "{instance.get_estado_display()}" y no puede modificarse.'}
            )

        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            sucursal = validated_data.get('sucursal')
            recepcion = validated_data.get('recepcion_origen')
            if sucursal is None and recepcion is not None and recepcion.sucursal_id:
                sucursal = recepcion.sucursal
            if sucursal is None:
                inspeccion = validated_data.get('inspeccion_origen')
                if inspeccion is not None:
                    if inspeccion.sucursal_id:
                        sucursal = inspeccion.sucursal
                    elif inspeccion.recepcion_id and inspeccion.recepcion.sucursal_id:
                        sucursal = inspeccion.recepcion.sucursal
            if sucursal is None:
                orden = validated_data.get('orden_trabajo_origen')
                if orden is not None and orden.sucursal_id:
                    sucursal = orden.sucursal
            taller = resolver_taller(empresa_id, sucursal)
            if taller is not None:
                validated_data.setdefault('sucursal', taller)
                validated_data['numero_cotizacion'] = generar_codigo_secuencial(taller, 'cotizacion')
        cotizacion = super().create(validated_data)
        inspeccion = validated_data.get('inspeccion_origen')
        if inspeccion is not None and inspeccion.estado != 'FINALIZADA':
            inspeccion.estado = 'FINALIZADA'
            inspeccion.save(update_fields=['estado', 'updated_at'])
        return cotizacion

    def update(self, instance, validated_data):
        request = self.context.get('request')

        if 'estado' in validated_data and validated_data['estado'] == Cotizacion.EstadoCotizacion.ACEPTADA:
            validated_data['fecha_aceptacion'] = timezone.now()
            if request and request.user.is_authenticated:
                validated_data['aceptada_por'] = request.user

        return super().update(instance, validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['estado_display'] = instance.get_estado_display()
        rep['sucursal_nombre'] = instance.sucursal.nombre if instance.sucursal_id else None
        if instance.cliente_id:
            rep['cliente_nombre'] = instance.cliente.nombre
            rep['cliente_identificacion'] = instance.cliente.identificacion
            rep['cliente_telefono'] = instance.cliente.telefono
            rep['cliente_email'] = instance.cliente.email
        if instance.vehiculo_id:
            vh = instance.vehiculo
            rep['vehiculo_placa'] = vh.placa
            rep['vehiculo_marca'] = vh.marca
            rep['vehiculo_modelo'] = vh.modelo
        rep['recepcion_numero'] = (
            instance.recepcion_origen.numero_recepcion if instance.recepcion_origen_id else None
        )
        rep['inspeccion_numero'] = (
            instance.inspeccion_origen.numero_inspeccion if instance.inspeccion_origen_id else None
        )
        rep['inspeccion_tipo'] = (
            instance.inspeccion_origen.tipo_inspeccion if instance.inspeccion_origen_id else None
        )
        rep['orden_trabajo_numero'] = (
            instance.orden_trabajo_origen.numero_orden if instance.orden_trabajo_origen_id else None
        )
        orden_generada = getattr(instance, 'orden_trabajo', None)
        rep['orden_generada_numero'] = orden_generada.numero_orden if orden_generada else None
        rep['es_convertible'] = (
            instance.estado == Cotizacion.EstadoCotizacion.ACEPTADA
            and instance.orden_trabajo_origen_id is None
        )
        return rep