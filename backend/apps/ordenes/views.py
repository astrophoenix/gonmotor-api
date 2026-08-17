from rest_framework import filters, permissions, viewsets

from .models import OrdenTrabajo
from .serializers import OrdenTrabajoSerializer


class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    serializer_class = OrdenTrabajoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['numero_orden', 'cliente__nombre', 'vehiculo__placa']
    ordering_fields = ['numero_orden', 'created_at', 'total']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return OrdenTrabajo.objects.all()
        if hasattr(user, 'profile') and user.profile.empresa:
            return OrdenTrabajo.objects.filter(empresa=user.profile.empresa)
        return OrdenTrabajo.objects.none()
