from django.db.models import Q
from rest_framework import viewsets, permissions

from apps.authentication.permissions import EsAdminSaaS
from apps.core.mixins import LookupPublicoMixin, SoftDeleteDestroyMixin
from .models import Empresa
from .serializers import EmpresaSerializer


class EmpresaViewSet(LookupPublicoMixin, SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    """CRUD de empresas (tenants) exclusivo del administrador del SaaS.

    No depende del tenant activo: el superusuario gestiona todas las empresas.
    El DELETE es lógico; una empresa inactiva o eliminada deja de estar
    disponible en el login de sus usuarios.
    """

    serializer_class = EmpresaSerializer
    permission_classes = [permissions.IsAuthenticated, EsAdminSaaS]
    delete_identifier_fields = ['nombre_comercial', 'ruc']

    def get_queryset(self):
        qs = Empresa.objects.all().order_by('nombre_comercial', 'id')

        estado = self.request.query_params.get('estado', '').strip().lower()
        if estado == 'activo':
            qs = qs.filter(is_active=True)
        elif estado == 'inactivo':
            qs = qs.filter(is_active=False)

        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(nombre_comercial__icontains=search)
                | Q(razon_social__icontains=search)
                | Q(ruc__icontains=search)
                | Q(email_contacto__icontains=search)
            )
        return qs
