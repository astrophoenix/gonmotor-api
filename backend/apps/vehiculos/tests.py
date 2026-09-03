from django.contrib.auth.models import User
from django.test import TestCase
from apps.empresas.models import Empresa
from apps.clientes.models import Cliente
from apps.vehiculos.serializers import VehiculoNestedSerializer


class ClientePropietarioSerializerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='x')
        self.empresa = Empresa.objects.create(
            nombre_comercial='Taller GON',
            razon_social='GON SA',
            ruc='1750000000001',
        )
        self.cliente1 = Cliente.objects.create(
            empresa=self.empresa,
            tipo_identificacion='R',
            identificacion='0001',
            nombre='Cliente Uno',
        )
        self.cliente2 = Cliente.objects.create(
            empresa=self.empresa,
            tipo_identificacion='R',
            identificacion='0002',
            nombre='Cliente Dos',
        )

    def _create(self, cliente_id=None):
        data = {'placa': 'ABC123', 'marca': 'Toyota', 'modelo': 'Corolla', 'cliente_id': cliente_id}
        s = VehiculoNestedSerializer(data=data)
        self.assertTrue(s.is_valid(), s.errors)
        return s.save()

    def test_create_with_cliente(self):
        v = self._create(cliente_id=self.cliente1.id)
        rel = v.propietarios.get(es_actual=True)
        self.assertEqual(rel.cliente_id, self.cliente1.id)
        self.assertTrue(rel.es_actual)
        self.assertIsNone(rel.fecha_fin)

    def test_create_without_cliente(self):
        v = self._create(cliente_id=None)
        self.assertFalse(v.propietarios.filter(es_actual=True).exists())

    def test_update_switch_cliente_marks_previous_inactive(self):
        v = self._create(cliente_id=self.cliente1.id)
        s = VehiculoNestedSerializer(
            instance=v,
            data={'cliente_id': self.cliente2.id},
            partial=True,
        )
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        self.assertFalse(v.propietarios.filter(es_actual=True, cliente_id=self.cliente1.id).exists())
        self.assertTrue(v.propietarios.filter(es_actual=True, cliente_id=self.cliente2.id).exists())

    def test_update_clear_cliente(self):
        v = self._create(cliente_id=self.cliente1.id)
        s = VehiculoNestedSerializer(
            instance=v,
            data={'cliente_id': ''},
            partial=True,
        )
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        self.assertFalse(v.propietarios.filter(es_actual=True).exists())
        self.assertTrue(v.propietarios.filter(es_actual=False).exists())