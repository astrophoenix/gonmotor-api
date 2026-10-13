from django.urls import path

from .views import rentabilidad_taller, rentabilidad_empresa

urlpatterns = [
    path('rentabilidad-taller/', rentabilidad_taller, name='rentabilidad-taller'),
    path('rentabilidad-empresa/', rentabilidad_empresa, name='rentabilidad-empresa'),
]
