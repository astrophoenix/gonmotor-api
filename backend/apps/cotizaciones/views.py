from rest_framework import filters, permissions, viewsets

from apps.authentication.utils import get_empresa_id_desde_request

from .models import Cotizacion
from .serializers import CotizacionSerializer


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
        return Cotizacion.objects.filter(empresa_id=empresa_id)
