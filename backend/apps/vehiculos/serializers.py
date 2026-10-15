from rest_framework import serializers
from django.utils import timezone
from .models import Vehiculo, VehiculoPropietario, vincular_empresa
from apps.clientes.models import Cliente
from apps.core.utils.images import validate_image_extension
from apps.authentication.utils import get_empresa_id_desde_request


class EstadoPorEmpresaMixin:
    """`is_active` del vehículo evaluado en la empresa activa.

    El vehículo físico es global; lo que cada empresa activa, suspende o
    elimina es su vínculo (`VehiculoEmpresa`). Al leer, `is_active` refleja ese
    vínculo; al escribir, se aplica sobre él y no sobre el vehículo global.
    """

    def _empresa_id(self):
        request = self.context.get('request')
        return get_empresa_id_desde_request(request) if request else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        empresa_id = self._empresa_id()
        if empresa_id and 'is_active' in data:
            activo = getattr(instance, 'activo_en_empresa', None)
            if activo is None:
                data['is_active'] = instance.activo_en(empresa_id)
            else:
                data['is_active'] = bool(activo) and instance.esta_operativo
        return data

    def _aplicar_estado_vinculo(self, instance, is_active):
        empresa_id = self._empresa_id()
        if not empresa_id or is_active is None:
            return
        vinculo = vincular_empresa(instance, empresa_id, activar=False)
        # La anotación del queryset se calculó antes de la edición.
        instance.activo_en_empresa = is_active
        if vinculo.is_active != is_active:
            vinculo.is_active = is_active
            vinculo.save(update_fields=['is_active', 'updated_at'])
        if is_active and not instance.is_active:
            # Reactivar desde la empresa también levanta una suspensión global
            # heredada (antes el estado del vehículo era único).
            instance.is_active = True
            instance.save(update_fields=['is_active', 'updated_at'])


class ClienteNullablePkField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        # Solo clientes (no eliminados) de la empresa activa: evita enlazar un
        # cliente de otra empresa enviando su id. Se resuelve aquí (no en
        # __init__) porque el contexto del request llega al enlazar el campo.
        request = self.context.get('request')
        if request is None:
            # Uso interno sin request (comandos, tests): no hay tenant que aplicar.
            return Cliente.objects.all()
        return Cliente.objects.de_empresa(get_empresa_id_desde_request(request))

    def to_internal_value(self, data):
        if data in (None, ''):
            return None
        return super().to_internal_value(data)


class VehiculoNestedSerializer(EstadoPorEmpresaMixin, serializers.ModelSerializer):
    imagen = serializers.ImageField(required=False, allow_null=True)
    cliente_id = ClienteNullablePkField(
        queryset=Cliente.objects.none(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Vehiculo
        fields = [
            'id',
            'placa',
            'vin',
            'numero_motor',
            'marca',
            'modelo',
            'anio',
            'color',
            'transmision',
            'combustible',
            'tipo',
            'pais_origen',
            'kilometraje_actual',
            'proximo_mantenimiento_km',
            'proxima_mantenimiento_fecha',
            'observaciones',
            'is_active',
            'imagen',
            'cliente_id',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            # La unicidad de placa se valida manualmente en create() para poder
            # distinguir entre una placa activa (error normal) y una desactivada
            # (recurso de reactivación/inactive_duplicate).
            'placa': {'validators': []},
        }

    def validate_imagen(self, value):
        if not value:
            return value

        validate_image_extension(value)

        if value.size > 2 * 1024 * 1024:
            raise serializers.ValidationError(
                'La imagen no debe superar los 2MB.'
            )

        return value

    def _aplicar_propietario(self, instance, cliente):
        if cliente is not None:
            relacion_activa = instance.propietarios.filter(es_actual=True).first()
            if relacion_activa and relacion_activa.cliente_id == cliente.id:
                return
            if relacion_activa:
                relacion_activa.es_actual = False
                relacion_activa.fecha_fin = timezone.localdate()
                relacion_activa.save(update_fields=['es_actual', 'fecha_fin'])
            VehiculoPropietario.objects.update_or_create(
                vehiculo=instance,
                cliente=cliente,
                es_actual=True,
                defaults={'fecha_fin': None},
            )
        else:
            instance.propietarios.filter(es_actual=True).update(
                es_actual=False,
                fecha_fin=timezone.localdate(),
            )

    def create(self, validated_data):
        cliente = validated_data.pop('cliente_id', None)
        imagen = validated_data.pop('imagen', None)
        # Al crear, el vínculo con la empresa siempre nace activo (un FormData
        # sin `is_active` llega como False y no debe ocultar el vehículo).
        validated_data.pop('is_active', None)
        empresa_id = self._empresa_id()

        # Normaliza la placa y valida unicidad manualmente (mismo criterio que
        # VehiculoSerializer). La placa es única entre vehículos no eliminados;
        # el estado se evalúa en el vínculo con la empresa activa:
        #   - vínculo activo     -> error normal;
        #   - vínculo suspendido -> inactive_duplicate (el frontend ofrece activarlo);
        #   - sin vínculo vivo   -> el vehículo ya existe en el sistema (otra
        #     empresa o eliminado aquí): se vincula a esta empresa y se actualiza.
        placa = (validated_data.get('placa') or '').strip().upper()
        validated_data['placa'] = placa

        existente = Vehiculo.objects.filter(placa=placa).first()
        if existente:
            vinculo = existente.vinculo_de(empresa_id) if empresa_id else None
            if vinculo and vinculo.is_active:
                raise serializers.ValidationError({
                    'placa': ['Ya existe Vehículo con este Placa.']
                })
            if vinculo:
                raise serializers.ValidationError({
                    'inactive_duplicate': {
                        'id': existente.id,
                        'placa': placa,
                        'marca': existente.marca,
                        'modelo': existente.modelo,
                        'message': (
                            f"Ya existe un vehículo desactivado con la placa {placa}. "
                            f"Puedes activarlo."
                        ),
                    }
                })
            instance = super().update(existente, validated_data)
        else:
            instance = super().create(validated_data)

        if imagen:
            instance.imagen = imagen
            instance.save()
        if empresa_id:
            vincular_empresa(instance, empresa_id)
        if cliente is not None:
            self._aplicar_propietario(instance, cliente)
        return instance

    def update(self, instance, validated_data):
        cliente = validated_data.pop('cliente_id', None)
        imagen = validated_data.pop('imagen', None)
        is_active = validated_data.pop('is_active', None)
        has_cliente = 'cliente_id' in self.get_initial()

        placa = validated_data.get('placa')
        if placa:
            placa = placa.strip().upper()
            validated_data['placa'] = placa
            existe = Vehiculo.objects.filter(placa=placa).exclude(pk=instance.pk).first()
            if existe:
                raise serializers.ValidationError({
                    'placa': ['Ya existe Vehículo con este Placa.']
                })

        instance = super().update(instance, validated_data)
        if imagen is not None:
            instance.imagen = imagen
            instance.save()
        self._aplicar_estado_vinculo(instance, is_active)
        if has_cliente:
            self._aplicar_propietario(instance, cliente)
        return instance


class VehiculoSerializer(EstadoPorEmpresaMixin, serializers.ModelSerializer):
    cliente_nombre = serializers.SerializerMethodField()
    cliente_id = serializers.SerializerMethodField()

    def _propietario_actual(self, obj):
        # El listado precarga `propietarios_actuales`; sin él (create/update) se consulta.
        precargados = getattr(obj, 'propietarios_actuales', None)
        if precargados is not None:
            return precargados[0] if precargados else None
        return obj.propietarios.filter(es_actual=True).select_related('cliente').first()

    def get_cliente_nombre(self, obj):
        propietario = self._propietario_actual(obj)
        return propietario.cliente_nombre_estado if propietario else None

    def get_cliente_id(self, obj):
        propietario = self._propietario_actual(obj)
        return propietario.cliente_id if propietario else None

    def validate_imagen(self, value):
        if not value:
            return value

        validate_image_extension(value)

        if value.size > 2 * 1024 * 1024:
            raise serializers.ValidationError(
                'La imagen no debe superar los 2MB.'
            )

        return value

    class Meta:
        model = Vehiculo
        # `empresas` no se expone: el vehículo es global y su lista de empresas
        # revelaría qué otros tenants lo atienden.
        exclude = ['empresas']
        read_only_fields = ['id', 'created_at', 'updated_at', 'cliente_nombre', 'cliente_id']
        extra_kwargs = {
            # La unicidad de placa se valida manualmente en create() para poder
            # distinguir entre una placa activa (error normal) y una desactivada
            # (recurso de reactivación/inactive_duplicate).
            'placa': {'validators': []},
        }
