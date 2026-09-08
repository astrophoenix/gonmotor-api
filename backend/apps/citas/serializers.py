from rest_framework import serializers

from apps.authentication.utils import get_empresa_id_desde_request

from .models import Cita

# Estados en los que la cita ya no admite edición (porque generó recepción o se cerró).
ESTADOS_FINALES = [
    Cita.EstadoCita.COMPLETADA,
    Cita.EstadoCita.CANCELADA,
    Cita.EstadoCita.NO_ASISTIO,
]


class CitaSerializer(serializers.ModelSerializer):
    estado_display = serializers.CharField(read_only=True, source='get_estado_display')
    motivo_display = serializers.CharField(read_only=True, source='get_motivo_display')

    class Meta:
        model = Cita
        fields = [
            'id',
            'empresa',
            'taller',
            'cliente',
            'vehiculo',
            'asesor',
            'fecha_cita',
            'hora_cita',
            'fecha_hora_programada',
            'estado',
            'estado_display',
            'motivo',
            'motivo_display',
            'motivo_descripcion',
            'kilometraje_aproximado',
            'recepcion_generada',
            'fecha_conversion',
            'notas_internas',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'empresa',
            'fecha_hora_programada',
            'recepcion_generada',
            'fecha_conversion',
            'is_active',
            'created_at',
            'updated_at',
        ]

    def validate(self, attrs):
        request = self.context.get('request')
        empresa_id = get_empresa_id_desde_request(request) if request else None

        if self.instance:
            instancia_empresa_id = self.instance.empresa_id
            instancia_taller = self.instance.taller
            instancia_cliente = self.instance.cliente
            instancia_vehiculo = self.instance.vehiculo
        else:
            instancia_empresa_id = None
            instancia_taller = None
            instancia_cliente = None
            instancia_vehiculo = None

        if empresa_id:
            if instancia_empresa_id is not None and instancia_empresa_id != empresa_id:
                raise serializers.ValidationError(
                    {'empresa': 'La cita no pertenece a tu empresa.'}
                )
            taller = attrs.get('taller', instancia_taller)
            if taller is not None and taller.empresa_id != empresa_id:
                raise serializers.ValidationError(
                    {'taller': 'El taller no pertenece a tu empresa.'}
                )
            cliente = attrs.get('cliente', instancia_cliente)
            if cliente is not None and cliente.empresa_id != empresa_id:
                raise serializers.ValidationError(
                    {'cliente': 'El cliente no pertenece a tu empresa.'}
                )
            vehiculo = attrs.get('vehiculo', instancia_vehiculo)
            if vehiculo is not None and not vehiculo.empresas.filter(id=empresa_id).exists():
                raise serializers.ValidationError(
                    {'vehiculo': 'El vehículo no pertenece a tu empresa.'}
                )

        fecha = attrs.get('fecha_cita') or (self.instance.fecha_cita if self.instance else None)
        hora = attrs.get('hora_cita') or (self.instance.hora_cita if self.instance else None)
        if fecha and hora:
            from django.utils import timezone

            programada = timezone.make_aware(
                timezone.datetime.combine(fecha, hora),
                timezone.get_current_timezone(),
            )
            if programada < timezone.now():
                raise serializers.ValidationError(
                    {'fecha_hora_programada': 'La fecha y hora de la cita no puede estar en el pasado.'}
                )

        estado = attrs.get('estado')
        if self.instance and estado and self.instance.estado in ESTADOS_FINALES:
            if estado != self.instance.estado:
                raise serializers.ValidationError(
                    {'estado': 'Una cita finalizada no puede cambiar de estado.'}
                )
            for campo in ('fecha_cita', 'hora_cita', 'cliente', 'vehiculo', 'motivo'):
                if campo in attrs and attrs[campo] != getattr(self.instance, campo):
                    raise serializers.ValidationError(
                        {campo: 'Una cita finalizada no puede modificarse.'}
                    )

        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            if not validated_data.get('asesor') and request.user.is_authenticated:
                validated_data['asesor'] = request.user
        return super().create(validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if instance.vehiculo_id:
            rep['vehiculo'] = {
                'id': instance.vehiculo_id,
                'placa': instance.vehiculo.placa,
                'marca': instance.vehiculo.marca,
                'modelo': instance.vehiculo.modelo,
                'color': instance.vehiculo.color,
                'tipo': instance.vehiculo.tipo,
            }
        if instance.cliente_id:
            rep['cliente'] = {
                'id': instance.cliente_id,
                'nombre': instance.cliente.nombre,
                'identificacion': instance.cliente.identificacion,
                'telefono': instance.cliente.telefono,
                'email': instance.cliente.email,
            }
        rep['taller_nombre'] = instance.taller.nombre if instance.taller_id else None
        rep['asesor_nombre'] = (
            instance.asesor.get_full_name() or instance.asesor.username
        ) if instance.asesor_id else None
        rep['recepcion_generada_numero'] = (
            instance.recepcion_generada.numero_recepcion
            if instance.recepcion_generada_id
            else None
        )
        rep['es_convertible'] = (
            instance.estado not in ESTADOS_FINALES
            and instance.recepcion_generada_id is None
        )
        return rep