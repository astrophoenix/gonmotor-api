from rest_framework import serializers

from apps.authentication.models import UsuarioEmpresa
from apps.authentication.utils import get_empresa_id_desde_request

from .models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
    FotoRecepcion,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)


class DetalleServicioOrdenTrabajoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleServicioOrdenTrabajo
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class DetalleRepuestoOrdenTrabajoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetalleRepuestoOrdenTrabajo
        fields = '__all__'
        read_only_fields = ['id', 'subtotal']


class InspeccionVehiculoSerializer(serializers.ModelSerializer):
    class Meta:
        model = InspeccionVehiculo
        fields = '__all__'
        read_only_fields = ['id']

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
        return super().create(validated_data)

    def validate(self, attrs):
        recepcion = attrs.get('recepcion')
        if recepcion is not None:
            inspecciones = recepcion.inspecciones.all()
            if self.instance:
                inspecciones = inspecciones.exclude(pk=self.instance.pk)
            if inspecciones.exists():
                raise serializers.ValidationError(
                    {'recepcion': 'Esta recepción ya tiene una inspección registrada.'}
                )
        return attrs

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['tipo_inspeccion_display'] = instance.get_tipo_inspeccion_display()
        rep['estado_display'] = instance.get_estado_display()
        if instance.recepcion_id:
            rec = instance.recepcion
            rv = rec.vehiculo if rec.vehiculo_id else None
            rc = rec.cliente if rec.cliente_id else None
            rep['recepcion'] = {
                'id': rec.id,
                'vehiculo': {
                    'id': rv.id,
                    'placa': rv.placa,
                    'marca': rv.marca,
                    'modelo': rv.modelo,
                    'color': rv.color,
                } if rv else None,
                'cliente': {
                    'id': rc.id,
                    'nombre': rc.nombre,
                    'identificacion': rc.identificacion,
                    'telefono': rc.telefono,
                    'email': rc.email,
                } if rc else None,
                'placa': rv.placa if rv else None,
                'marca': rv.marca if rv else None,
                'modelo': rv.modelo if rv else None,
                'cliente_nombre': rc.nombre if rc else None,
                'motivo_ingreso': rec.motivo_ingreso,
                'created_at': rec.created_at.isoformat() if rec.created_at else None,
            }
        return rep


class FotoRecepcionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FotoRecepcion
        fields = ['id', 'tipo_vista', 'tipo_vista_display', 'imagen', 'descripcion', 'created_at']
        read_only_fields = ['id', 'tipo_vista_display', 'created_at']

    tipo_vista_display = serializers.CharField(source='get_tipo_vista_display', read_only=True)


class RecepcionVehiculoSerializer(serializers.ModelSerializer):
    inspecciones = InspeccionVehiculoSerializer(many=True, read_only=True)
    fotos = FotoRecepcionSerializer(many=True, read_only=True)

    class Meta:
        model = RecepcionVehiculo
        fields = '__all__'
        read_only_fields = ['id', 'fecha_firma_receptor', 'fecha_firma_cliente', 'aceptacion_condiciones']

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['estado_display'] = instance.get_estado_display()
        if instance.vehiculo_id:
            rep['vehiculo'] = {
                'id': instance.vehiculo_id,
                'placa': instance.vehiculo.placa,
                'marca': instance.vehiculo.marca,
                'modelo': instance.vehiculo.modelo,
                'color': instance.vehiculo.color,
                'tipo': instance.vehiculo.tipo,
                'grupo_blueprint': instance.vehiculo.grupo_blueprint,
                'kilometraje_actual': instance.vehiculo.kilometraje_actual,
            }
        if instance.cliente_id:
            rep['cliente'] = {
                'id': instance.cliente_id,
                'nombre': instance.cliente.nombre,
                'identificacion': instance.cliente.identificacion,
                'telefono': instance.cliente.telefono,
                'email': instance.cliente.email,
            }
        if instance.recibido_por_id:
            rep['recibido_por_nombre'] = instance.recibido_por.get_full_name() or instance.recibido_por.username
            usuario_empresa = UsuarioEmpresa.objects.filter(user=instance.recibido_por, empresa=instance.empresa).first()
            if usuario_empresa:
                rep['recibido_por_rol'] = usuario_empresa.rol
                rep['recibido_por_rol_display'] = usuario_empresa.get_rol_display()
            else:
                rep['recibido_por_rol'] = None
                rep['recibido_por_rol_display'] = None
        else:
            rep['recibido_por_nombre'] = None
            rep['recibido_por_rol'] = None
            rep['recibido_por_rol_display'] = None
        return rep

    def _recolectar_fotos(self, request):
        """Lee el bloque de hasta 5 fotos enviado por FormData.

        Cada archivo viaja en un campo con nombre igual al tipo de vista, p.ej.
        `foto_FRONTAL`, `foto_LATERAL_IZQ`, ... Solo se consideran las vistas
        obligatorias definidas en FotoRecepcion.VISTAS_OBLIGATORIAS.
        """
        archivos = request.FILES or {}
        fotos = {}
        for tipo in FotoRecepcion.VISTAS_OBLIGATORIAS:
            campo = f'foto_{tipo}'
            archivo = archivos.get(campo)
            if archivo:
                fotos[tipo] = archivo
        return fotos

    def _validar_bloque_fotos(self, fotos):
        """Valida que estén presentes exactamente las 5 vistas obligatorias."""
        faltantes = [
            tipo for tipo in FotoRecepcion.VISTAS_OBLIGATORIAS
            if tipo not in fotos
        ]
        if faltantes:
            nombres = ', '.join(
                dict(FotoRecepcion.TipoVista.choices)[t] for t in faltantes
            )
            raise serializers.ValidationError({
                'fotos': (
                    'Debes subir las 5 fotos obligatorias de la recepción. '
                    f'Faltan: {nombres}.'
                )
            })

    def _guardar_fotos(self, instance, fotos):
        """Crea el bloque de fotos de la recepción (reemplaza cada vista existente)."""
        for tipo, archivo in fotos.items():
            valor = tipo.value if isinstance(tipo, FotoRecepcion.TipoVista) else tipo
            instance.fotos.filter(tipo_vista=valor).delete()
            FotoRecepcion.objects.create(
                recepcion=instance,
                tipo_vista=valor,
                imagen=archivo,
            )

    def validate(self, attrs):
        if getattr(self.instance, 'estado', None) or attrs.get('estado'):
            estado = attrs.get('estado') or (self.instance.estado if self.instance else 'PENDIENTE')
        else:
            estado = 'PENDIENTE'
        if estado == 'NO_ACEPTADA':
            motivo = attrs.get('motivo_no_recepcion')
            if motivo is None and self.instance:
                motivo = self.instance.motivo_no_recepcion
            if not (motivo or '').strip():
                raise serializers.ValidationError({
                    'motivo_no_recepcion': 'El motivo de la no aceptación es obligatorio.'
                })
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        fotos = {}
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
            if not validated_data.get('recibido_por') and request.user.is_authenticated:
                validated_data['recibido_por'] = request.user
            fotos = self._recolectar_fotos(request)
        if validated_data.get('estado') != 'NO_ACEPTADA':
            self._validar_bloque_fotos(fotos)
        instance = super().create(validated_data)
        self._guardar_fotos(instance, fotos)
        return instance

    def update(self, instance, validated_data):
        if instance.aceptacion_condiciones and instance.fecha_firma_cliente:
            raise serializers.ValidationError({
                'detail': 'No se puede editar una recepción cuyo cliente ya aceptó y firmó las condiciones de recepción.'
            })
        validated_data.pop('fecha_firma_receptor', None)
        validated_data.pop('fecha_firma_cliente', None)
        validated_data.pop('aceptacion_condiciones', None)

        request = self.context.get('request')
        fotos = {}
        if request:
            fotos = self._recolectar_fotos(request)
        if validated_data.get('estado') != 'NO_ACEPTADA':
            self._validar_bloque_fotos(fotos)

        instance = super().update(instance, validated_data)
        self._guardar_fotos(instance, fotos)
        return instance


class OrdenTrabajoSerializer(serializers.ModelSerializer):
    servicios = DetalleServicioOrdenTrabajoSerializer(many=True, read_only=True)
    repuestos = DetalleRepuestoOrdenTrabajoSerializer(many=True, read_only=True)
    recepciones = RecepcionVehiculoSerializer(many=True, read_only=True)
    inspeccion = InspeccionVehiculoSerializer(read_only=True)

    class Meta:
        model = OrdenTrabajo
        fields = [
            'id',
            'empresa',
            'sucursal',
            'cliente',
            'vehiculo',
            'asesor',
            'mecanico_principal',
            'cotizacion_origen',
            'numero_orden',
            'estado',
            'prioridad',
            'tipo_trabajo',
            'observaciones_internas',
            'fecha_ingreso',
            'fecha_entrega',
            'subtotal_servicios',
            'subtotal_repuestos',
            'descuento',
            'subtotal_neto',
            'monto_iva',
            'total',
            'servicios',
            'repuestos',
            'recepciones',
            'inspeccion',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'subtotal_servicios', 'subtotal_repuestos', 'subtotal_neto', 'monto_iva', 'total', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            empresa_id = get_empresa_id_desde_request(request)
            if empresa_id:
                validated_data['empresa_id'] = empresa_id
        return super().create(validated_data)
