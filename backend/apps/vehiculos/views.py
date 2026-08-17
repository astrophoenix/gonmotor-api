from rest_framework import viewsets, permissions, filters
from .models import Vehiculo
from .serializers import VehiculoSerializer


class VehiculoViewSet(viewsets.ModelViewSet):
    """
    API endpoint para gestionar Vehículos.
    Filtrado automáticamente por la Empresa del usuario autenticado.
    """
    serializer_class = VehiculoSerializer
    permission_classes = [permissions.IsAuthenticated]

    # Mantener tus búsquedas por placa, VIN, marca, modelo y datos del cliente
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'placa', 
        'vin', 
        'marca', 
        'modelo', 
        'cliente__nombre', 
        'cliente__identificacion'
    ]
    ordering_fields = ['placa', 'marca', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user

        # 1. Base del QuerySet: Superusuario ve todo, usuario normal ve solo los de su empresa
        if user.is_superuser:
            queryset = Vehiculo.objects.all()
        elif hasattr(user, 'profile') and user.profile.empresa:
            queryset = Vehiculo.objects.filter(empresa=user.profile.empresa)
        else:
            return Vehiculo.objects.none()

        # 2. Filtrado adicional opcional por ID de cliente vía query param: ?cliente=15
        cliente_id = self.request.query_params.get('cliente')
        if cliente_id:
            queryset = queryset.filter(cliente_id=cliente_id)

        return queryset