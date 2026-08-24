from rest_framework import viewsets, permissions
from .models import Empresa
from .serializers import EmpresaSerializer


class EmpresaViewSet(viewsets.ModelViewSet):
    queryset = Empresa.objects.filter(is_active=True).order_by('nombre_comercial')
    serializer_class = EmpresaSerializer
    permission_classes = [permissions.IsAuthenticated]
