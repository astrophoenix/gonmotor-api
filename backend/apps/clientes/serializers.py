from rest_framework import serializers
from django.db import transaction
import json
from .models import Cliente
from apps.vehiculos.models import Vehiculo, VehiculoPropietario
from apps.vehiculos.serializers import VehiculoSerializer, VehiculoNestedSerializer
from apps.authentication.utils import get_empresa_id_desde_request
from apps.cotizaciones.models import Cotizacion
from apps.ordenes.models import OrdenTrabajo


class VehiculoResumenSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehiculo
        fields = ['id', 'placa', 'marca', 'modelo', 'color', 'imagen']


class ClienteListSerializer(serializers.ModelSerializer):
    vehiculos_count = serializers.IntegerField(read_only=True)
    vehiculos = serializers.SerializerMethodField()

    def get_vehiculos(self, obj):
        relaciones = getattr(
            obj,
            'propietarios_actuales',
            obj.vehiculos_asociados.filter(es_actual=True)
            .select_related('vehiculo')
            .order_by('vehiculo__id')
        )
        vehiculos = [relacion.vehiculo for relacion in relaciones]
        vehiculos.sort(key=lambda v: v.id)
        return VehiculoResumenSerializer(vehiculos, many=True, context=self.context).data

    class Meta:
        model = Cliente
        fields = [
            'id',
            'tipo_identificacion',
            'identificacion',
            'nombre',
            'email',
            'telefono',
            'is_active',
            'vehiculos_count',
            'vehiculos',
            'created_at',
        ]


class ClienteSerializer(serializers.ModelSerializer):
    vehiculos = serializers.JSONField(required=False, write_only=True)
    is_active = serializers.BooleanField(required=False, default=True)

    class Meta:
        model = Cliente
        fields = [
            'id',
            'tipo_identificacion',
            'identificacion',
            'nombre',
            'email',
            'telefono',
            'direccion',
            'contifico_id',
            'is_active',
            'vehiculos',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'empresa', 'created_at', 'updated_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        relaciones = getattr(
            instance,
            'propietarios_actuales',
            instance.vehiculos_asociados.filter(es_actual=True).select_related('vehiculo')
        )
        vehiculos = [relacion.vehiculo for relacion in relaciones]
        data['vehiculos'] = VehiculoNestedSerializer(vehiculos, many=True, context=self.context).data
        return data

    def _procesar_vehiculos(self, cliente, vehiculos_data):
        request = self.context.get('request')
        empresa_id = get_empresa_id_desde_request(request)
        vehiculos_actuales_ids = set()

        for index, vehiculo_data in enumerate(vehiculos_data):
            imagen = request.FILES.get(f'vehiculo_{index}_imagen')
            if imagen:
                vehiculo_data['imagen'] = imagen

            eliminar_imagen = vehiculo_data.pop('eliminar_imagen', False)

            vehiculo_id = vehiculo_data.get('id')
            placa = (vehiculo_data.get('placa') or '').strip().upper()

            vehiculo = None
            if vehiculo_id:
                try:
                    vehiculo = Vehiculo.objects.get(id=vehiculo_id)
                except Vehiculo.DoesNotExist:
                    pass

            if not vehiculo and placa:
                vehiculo = Vehiculo.objects.filter(placa=placa).first()

            if vehiculo:
                # Reactiva el vehículo si fue desactivado (soft delete).
                if not vehiculo.is_active:
                    vehiculo.is_active = True
                    vehiculo.save(update_fields=['is_active'])
                if eliminar_imagen and not imagen and vehiculo.imagen:
                    vehiculo.imagen.delete(save=False)
                    vehiculo.imagen = None
                    vehiculo.save()
                serializer = VehiculoNestedSerializer(
                    vehiculo,
                    data=vehiculo_data,
                    partial=True,
                    context=self.context
                )
                serializer.is_valid(raise_exception=True)
                vehiculo = serializer.save()
            else:
                serializer = VehiculoNestedSerializer(data=vehiculo_data, context=self.context)
                serializer.is_valid(raise_exception=True)
                vehiculo = serializer.save()

            if empresa_id:
                vehiculo.empresas.add(empresa_id)

            VehiculoPropietario.objects.update_or_create(
                vehiculo=vehiculo,
                cliente=cliente,
                defaults={'es_actual': True}
            )
            vehiculos_actuales_ids.add(vehiculo.id)

        relaciones_actuales = VehiculoPropietario.objects.filter(
            cliente=cliente,
            es_actual=True
        )
        
        if not vehiculos_actuales_ids:
            vehiculos_desactivados_ids = list(relaciones_actuales.values_list('vehiculo_id', flat=True))
            relaciones_actuales.update(es_actual=False)
        else:
            vehiculos_desactivados = relaciones_actuales.exclude(vehiculo_id__in=vehiculos_actuales_ids)
            vehiculos_desactivados_ids = list(vehiculos_desactivados.values_list('vehiculo_id', flat=True))
            vehiculos_desactivados.update(es_actual=False)
        
        for vehiculo_id in vehiculos_desactivados_ids:
            try:
                vehiculo = Vehiculo.objects.get(id=vehiculo_id)
            except Vehiculo.DoesNotExist:
                continue
            
            otras_asociaciones = VehiculoPropietario.objects.filter(
                vehiculo=vehiculo
            ).exclude(cliente=cliente).exists()
            
            if not otras_asociaciones:
                tiene_cotizaciones = Cotizacion.objects.filter(vehiculo=vehiculo).exists()
                tiene_ordenes = OrdenTrabajo.objects.filter(vehiculo=vehiculo).exists()
                
                if not tiene_cotizaciones and not tiene_ordenes:
                    vehiculo.delete()

    def _parsear_vehiculos_data(self, validated_data, request):
        vehiculos_data = validated_data.pop('vehiculos', [])
        if isinstance(vehiculos_data, str):
            try:
                vehiculos_data = json.loads(vehiculos_data)
            except json.JSONDecodeError as e:
                vehiculos_data = []
        if not isinstance(vehiculos_data, list):
            vehiculos_data = []
        return vehiculos_data

    def create(self, validated_data):
        request = self.context.get('request')
        vehiculos_data = self._parsear_vehiculos_data(validated_data, request)

        if request and request.user.is_authenticated:
            empresa_id = get_empresa_id_desde_request(request)

            if empresa_id:
                validated_data['empresa_id'] = empresa_id

                # Normaliza la identificación para búsquedas y almacenamiento.
                identificacion = (validated_data.get('identificacion') or '').strip().upper()
                validated_data['identificacion'] = identificacion

                # Un cliente ACTIVO con la misma identificación en esta empresa
                # impide crear un duplicado (se replica el error que antes daba
                # el UniqueValidator de DRF).
                existe_activo = Cliente.objects.filter(
                    empresa_id=empresa_id,
                    identificacion=identificacion,
                    is_active=True,
                ).first()
                if existe_activo:
                    raise serializers.ValidationError({
                        "identificacion": [
                            "Ya existe un cliente activo con esta identificación."
                        ]
                    })

                # Propuesta 1: si existe un cliente desactivado con la misma
                # identificación en ESTA empresa, se informa al usuario con el id
                # para que decida activarlo (el frontend hace PATCH con is_active=True).
                existe_inactivo = Cliente.objects.filter(
                    empresa_id=empresa_id,
                    identificacion=identificacion,
                    is_active=False,
                ).first()
                if existe_inactivo:
                    raise serializers.ValidationError({
                        'inactive_duplicate': {
                            'id': existe_inactivo.id,
                            'identificacion': identificacion,
                            'nombre': existe_inactivo.nombre,
                            'message': (
                                f"Ya existe un cliente desactivado con la identificación "
                                f"{identificacion}. Puedes activarlo."
                            ),
                        }
                    })
            else:
                raise serializers.ValidationError({
                    "empresa": "No se pudo determinar la empresa activa del usuario en sesión."
                })

        with transaction.atomic():
            cliente = super().create(validated_data)
            self._procesar_vehiculos(cliente, vehiculos_data)

        return cliente

    def update(self, instance, validated_data):
        request = self.context.get('request')
        vehiculos_data = self._parsear_vehiculos_data(validated_data, request)

        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if vehiculos_data is not None:
                self._procesar_vehiculos(instance, vehiculos_data)

        return instance