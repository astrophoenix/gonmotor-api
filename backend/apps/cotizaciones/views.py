from rest_framework import filters, permissions, viewsets

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
        user = self.request.user
        if user.is_superuser:
            return Cotizacion.objects.all()
        if hasattr(user, 'profile') and user.profile.empresa:
            return Cotizacion.objects.filter(empresa=user.profile.empresa)
        return Cotizacion.objects.none()
