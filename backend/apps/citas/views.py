from django.utils import timezone
from rest_framework import filters, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.utils import get_empresa_id_desde_request
from apps.core.mixins import SoftDeleteDestroyMixin

from .models import Cita
from .serializers import CitaSerializer


class CitaViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    serializer_class = CitaSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'cliente__nombre',
        'cliente__identificacion',
        'vehiculo__placa',
        'vehiculo__marca',
        'vehiculo__modelo',
        'motivo_descripcion',
    ]
    ordering_fields = ['fecha_cita', 'hora_cita', 'created_at', 'id']
    ordering = ['fecha_cita', 'hora_cita']

    delete_identifier_fields = ['fecha_cita']
    delete_relation_fields = ['recepcion_generada']

    def has_related_records(self, instance):
        for field_name in self.delete_relation_fields:
            value = getattr(instance, field_name, None)
            if value is not None and bool(getattr(value, 'id', None)):
                return True
        return False

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return Cita.objects.none()
        queryset = (
            Cita.objects.filter(empresa_id=empresa_id)
            .select_related('cliente', 'vehiculo', 'taller', 'asesor', 'recepcion_generada')
        )

        fecha = self.request.query_params.get('fecha')
        if fecha:
            queryset = queryset.filter(fecha_cita=fecha)

        estado = self.request.query_params.get('estado')
        if estado:
            queryset = queryset.filter(estado=estado)

        desde = self.request.query_params.get('desde')
        if desde:
            try:
                queryset = queryset.filter(fecha_cita__gte=timezone.datetime.strptime(desde, '%Y-%m-%d').date())
            except ValueError:
                pass

        hasta = self.request.query_params.get('hasta')
        if hasta:
            try:
                queryset = queryset.filter(fecha_cita__lte=timezone.datetime.strptime(hasta, '%Y-%m-%d').date())
            except ValueError:
                pass

        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    @action(detail=True, methods=['post'])
    def convertir_a_recepcion(self, request, pk=None):
        cita = self.get_object()
        try:
            recepcion = cita.convertir_a_recepcion(usuario=request.user)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return Response({
            'id': cita.id,
            'estado': cita.estado,
            'recepcion_id': recepcion.id,
            'numero_recepcion': recepcion.numero_recepcion,
        })