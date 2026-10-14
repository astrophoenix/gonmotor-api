from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.empresas.models import Empresa, Taller

from .models import Gasto

User = get_user_model()
ENDPOINT = '/api/gastos/'


class GastosFiltroTallerTests(TestCase):
    """El listado se recorta al taller activo (`X-Taller-ID`), como los demás módulos."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='gastos_admin', password='x')
        cls.empresa = Empresa.objects.create(
            nombre_comercial='Taller Gastos',
            ruc=str(uuid4().int % 10_000_000_000_000).zfill(13),
            email_contacto='gastos@test.com',
        )
        cls.central = Taller.objects.create(
            empresa=cls.empresa, nombre='Central', codigo_sucursal='001', direccion='Av. 1',
        )
        cls.norte = Taller.objects.create(
            empresa=cls.empresa, nombre='Norte', codigo_sucursal='002', direccion='Av. 2',
        )
        cls.gasto_central = Gasto.objects.create(
            empresa=cls.empresa, taller=cls.central, fecha=date(2026, 1, 10), monto=10,
        )
        cls.gasto_norte = Gasto.objects.create(
            empresa=cls.empresa, taller=cls.norte, fecha=date(2026, 1, 11), monto=20,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def _ids(self, respuesta):
        self.assertEqual(respuesta.status_code, 200)
        return {fila['id'] for fila in respuesta.data['results']}

    def test_con_taller_activo_solo_lista_sus_gastos(self):
        respuesta = self.client.get(ENDPOINT, HTTP_X_TALLER_ID=str(self.central.pk))
        self.assertEqual(self._ids(respuesta), {self.gasto_central.pk})

    def test_todos_los_talleres_lista_todo(self):
        respuesta = self.client.get(ENDPOINT)
        self.assertEqual(self._ids(respuesta), {self.gasto_central.pk, self.gasto_norte.pk})

    def test_filtro_manual_por_taller_en_todos(self):
        respuesta = self.client.get(ENDPOINT, {'taller_id': self.norte.pk})
        self.assertEqual(self._ids(respuesta), {self.gasto_norte.pk})
