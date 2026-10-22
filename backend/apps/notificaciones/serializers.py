from rest_framework import serializers

from .models import Campana, NotificacionProgramada, PreferenciaMantenimiento, RegistroMensajeWhatsApp
from .services.telefonos import normalizar_celular
from apps.core.serializers import ModelSerializerPublico


class RegistroMensajeWhatsAppSerializer(ModelSerializerPublico):
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
            'correo',
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


class PreferenciaMantenimientoSerializer(ModelSerializerPublico):
    class Meta:
        model = PreferenciaMantenimiento
        fields = [
            'id',
            'empresa',
            'intervalo_km',
            'intervalo_meses',
            'dias_antelacion',
            'frecuencia_dias',
            'notificaciones_activas',
            'recordatorios_cita_activos',
            'mensaje_plantilla',
        ]
        read_only_fields = ['id', 'empresa']


class EnviarRecordatorioSerializer(serializers.Serializer):
    vehiculo_id = serializers.UUIDField()


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


CANALES = [('whatsapp', 'WhatsApp'), ('correo', 'Correo')]

# Lo único que se cambia de una notificación pendiente; el resto lo fija su disparador.
CAMPOS_EDITABLES_NOTIFICACION = ('canal', 'fecha_programada', 'km_objetivo', 'fecha_tope', 'asunto', 'mensaje')


class NotificacionProgramadaSerializer(ModelSerializerPublico):
    """Lectura de la cola y edición de una notificación PENDIENTE."""

    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    origen_display = serializers.CharField(source='get_origen_display', read_only=True)
    cliente_nombre = serializers.CharField(source='cliente.nombre', read_only=True, default='')
    vehiculo_resumen = serializers.SerializerMethodField()
    orden_numero = serializers.CharField(source='orden.numero_orden', read_only=True, default=None)
    campana_nombre = serializers.CharField(source='campana.nombre', read_only=True, default=None)

    class Meta:
        model = NotificacionProgramada
        fields = [
            'id',
            'tipo',
            'tipo_display',
            'canal',
            'estado',
            'estado_display',
            'origen',
            'origen_display',
            'fecha_programada',
            'km_objetivo',
            'fecha_tope',
            'cliente',
            'cliente_nombre',
            'vehiculo',
            'vehiculo_resumen',
            'orden',
            'orden_numero',
            'cita',
            'campana',
            'campana_nombre',
            'asunto',
            'mensaje',
            'intentos',
            'ultimo_error',
            'fecha_envio',
            'created_at',
        ]
        read_only_fields = [f for f in fields if f not in CAMPOS_EDITABLES_NOTIFICACION]

    def get_vehiculo_resumen(self, obj):
        if not obj.vehiculo:
            return None
        return f"{obj.vehiculo.placa} - {obj.vehiculo.marca} {obj.vehiculo.modelo}"

    def validate_mensaje(self, value):
        if not value.strip():
            raise serializers.ValidationError('El mensaje no puede quedar vacío.')
        return value


class CrearMantenimientoSerializer(serializers.Serializer):
    """Alta manual de un próximo mantenimiento (reemplaza el pendiente del vehículo)."""

    vehiculo_id = serializers.UUIDField()
    km_objetivo = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    fecha_objetivo = serializers.DateField(required=False, allow_null=True)
    fecha_programada = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text='Vacío = fecha objetivo menos la antelación de las preferencias.',
    )
    canal = serializers.ChoiceField(choices=CANALES, default='whatsapp')
    mensaje = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get('km_objetivo') and not attrs.get('fecha_objetivo'):
            raise serializers.ValidationError(
                'Indica el kilometraje o la fecha del próximo mantenimiento.'
            )
        return attrs


class NotificarRetiroSerializer(serializers.Serializer):
    canal = serializers.ChoiceField(choices=CANALES, default='whatsapp')
    reenviar = serializers.BooleanField(default=False)


class SegmentoSerializer(serializers.Serializer):
    marca = serializers.CharField(required=False, allow_blank=True, max_length=100)
    sin_visita_dias = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=3650)
    con_visita_dias = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=3650)
    clientes = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)


class CampanaSerializer(ModelSerializerPublico):
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    creado_por_nombre = serializers.SerializerMethodField()
    # Anotados por `CampanaViewSet.get_queryset` (ver `campanas_con_conteos`).
    pendientes = serializers.IntegerField(source='n_pendientes', read_only=True, default=0)
    enviadas = serializers.IntegerField(source='n_enviadas', read_only=True, default=0)
    errores = serializers.IntegerField(source='n_errores', read_only=True, default=0)

    class Meta:
        model = Campana
        fields = [
            'id',
            'nombre',
            'asunto',
            'mensaje',
            'canal',
            'segmento',
            'estado',
            'estado_display',
            'fecha_programada',
            'fecha_lanzamiento',
            'total_destinatarios',
            'pendientes',
            'enviadas',
            'errores',
            'creado_por_nombre',
            'created_at',
        ]
        read_only_fields = ['id', 'estado', 'fecha_lanzamiento', 'total_destinatarios', 'created_at']

    def validate_segmento(self, value):
        segmento = SegmentoSerializer(data=value or {})
        segmento.is_valid(raise_exception=True)
        # Se guarda serializable (UUIDs como texto) y sin claves vacías.
        datos = {k: v for k, v in segmento.validated_data.items() if v not in (None, '', [])}
        if 'clientes' in datos:
            datos['clientes'] = [str(u) for u in datos['clientes']]
        return datos

    def validate_mensaje(self, value):
        if not value.strip():
            raise serializers.ValidationError('El mensaje no puede quedar vacío.')
        return value

    def validate(self, attrs):
        if self.instance is not None and self.instance.estado != Campana.Estado.BORRADOR:
            raise serializers.ValidationError('Solo se edita una campaña en borrador.')
        return attrs

    def get_creado_por_nombre(self, obj):
        user = obj.creado_por
        if not user:
            return ''
        return user.get_full_name() or user.username
