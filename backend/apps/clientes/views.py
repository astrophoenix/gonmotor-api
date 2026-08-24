from rest_framework import viewsets, permissions, filters
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Count, Q
from django.db.models import Prefetch
from .models import Cliente
from apps.vehiculos.models import VehiculoPropietario
from apps.core.mixins import SoftDeleteDestroyMixin
from .serializers import ClienteListSerializer, ClienteSerializer
from apps.authentication.utils import get_empresa_id_desde_request


class ClienteViewSet(SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    parser_classes = (MultiPartParser, FormParser)
    serializer_class = ClienteSerializer
    permission_classes = [permissions.IsAuthenticated]

    delete_identifier_fields = ['nombre']
    delete_relation_fields = ['ordenes_trabajo', 'vehiculos_asociados', 'cotizaciones']

    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre', 'identificacion', 'email', 'telefono']
    ordering_fields = ['nombre', 'created_at']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return ClienteListSerializer
        return ClienteSerializer

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)

        if not empresa_id:
            return Cliente.objects.none()

        queryset = Cliente.objects.filter(empresa_id=empresa_id)

        include_inactive = self.request.query_params.get('include_inactive', 'false').lower() == 'true'
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        if self.action in ['list', 'retrieve']:
            return queryset.select_related('empresa').annotate(
                vehiculos_count=Count(
                    'vehiculos_asociados__vehiculo',
                    filter=Q(vehiculos_asociados__es_actual=True),
                    distinct=True,
                )
            ).prefetch_related(
                Prefetch(
                    'vehiculos_asociados',
                    queryset=VehiculoPropietario.objects.filter(
                        es_actual=True
                    ).select_related('vehiculo'),
                    to_attr='propietarios_actuales',
                )
            )

        return queryset
