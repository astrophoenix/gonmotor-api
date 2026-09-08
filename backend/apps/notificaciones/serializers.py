from rest_framework import serializers

from .models import PreferenciaMantenimiento, RegistroMensajeWhatsApp
from .services.telefonos import normalizar_celular


class RegistroMensajeWhatsAppSerializer(serializers.ModelSerializer):
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    origen_display = serializers.CharField(source='get_origen_display', read_only=True)
    vehiculo_resumen = serializers.SerializerMethodField()

    class Meta:
        model = RegistroMensajeWhatsApp
        fields = [
            'id',
            'vehiculo',
            'vehiculo_resumen',
            'cliente_nombre',
            'celular',
            'mensaje',
            'canal',
            'proveedor',
            'estado',
            'estado_display',
            'origen',
            'origen_display',
            'id_externo',
            'error',
            'metadatos',
            'created_at',
        ]
        read_only_fields = fields

    def get_vehiculo_resumen(self, obj):
        if not obj.vehiculo:
            return None
        return f"{obj.vehiculo.placa} - {obj.vehiculo.marca} {obj.vehiculo.modelo}"


class PreferenciaMantenimientoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PreferenciaMantenimiento
        fields = [
            'id',
            'empresa',
            'intervalo_km',
            'dias_antelacion',
            'frecuencia_dias',
            'notificaciones_activas',
            'notificar_vehiculos_sin_programar',
            'mensaje_plantilla',
        ]
        read_only_fields = ['id', 'empresa']


class EnviarRecordatorioSerializer(serializers.Serializer):
    vehiculo_id = serializers.IntegerField()


class EnviarPruebaSerializer(serializers.Serializer):
    celular = serializers.CharField(max_length=30)
    mensaje = serializers.CharField(required=False, allow_blank=True)

    def validate_celular(self, value):
        normalizado = normalizar_celular(value)
        if len(normalizado) < 8:
            raise serializers.ValidationError(
                'El número de teléfono no parece válido. Ej: 0991234567 o +593 99 123 4567.'
            )
        return value


class EjecutarRecordatoriosSerializer(serializers.Serializer):
    preview = serializers.BooleanField(default=False)
    vehiculos_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True,
    )