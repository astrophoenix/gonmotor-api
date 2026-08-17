from rest_framework import viewsets, permissions, filters
from .models import Cliente
from .serializers import ClienteSerializer


class ClienteViewSet(viewsets.ModelViewSet):
    """
    API endpoint que permite ver, crear, editar y eliminar Clientes.
    Filtrado automáticamente por la Empresa del usuario conectado.
    """
    serializer_class = ClienteSerializer
    permission_classes = [permissions.IsAuthenticated]

    # Habilitar búsqueda rápida y ordenamiento
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre', 'identificacion', 'email', 'telefono']
    ordering_fields = ['nombre', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user

        # 1. Si es Superusuario/Admin global, puede ver TODOS los clientes
        if user.is_superuser:
            return Cliente.objects.all()

        # 2. Si es un usuario normal, filtra únicamente los clientes de SU empresa
        if hasattr(user, 'profile') and user.profile.empresa:
            return Cliente.objects.filter(empresa=user.profile.empresa)

        # 3. Si no tiene perfil o empresa asignada, no retorna registros
        return Cliente.objects.none()