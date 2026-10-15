from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class BusquedaIndexadaFilterTests(TestCase):
    """`BusquedaIndexadaFilter`: mismos resultados que el SearchFilter de DRF,
    resolviendo los campos relacionados con subconsultas."""

    def setUp(self):
        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa
        from apps.ordenes.models import OrdenTrabajo
        from apps.vehiculos.models import Vehiculo, VehiculoPropietario

        self.empresa = Empresa.objects.create(nombre_comercial='Búsqueda', ruc='1790000000111')
        self.ana = Cliente.objects.create(empresa=self.empresa, nombre='Ana Andrade', identificacion='0101010101')
        self.luis = Cliente.objects.create(empresa=self.empresa, nombre='Luis Vera', identificacion='0202020202')
        self.toyota = Vehiculo.objects.create(placa='PBX1234', marca='Toyota', modelo='Hilux')
        self.kia = Vehiculo.objects.create(placa='GSU5812', marca='Kia', modelo='Rio')
        for vehiculo in (self.toyota, self.kia):
            vehiculo.empresas.add(self.empresa)
        # Dos propietarios actuales del mismo vehículo: con JOIN el vehículo saldría dos veces.
        VehiculoPropietario.objects.create(vehiculo=self.toyota, cliente=self.ana)
        VehiculoPropietario.objects.create(vehiculo=self.toyota, cliente=self.luis)
        VehiculoPropietario.objects.create(vehiculo=self.kia, cliente=self.luis)

        self.ot_ana = OrdenTrabajo.objects.create(
            empresa=self.empresa, cliente=self.ana, vehiculo=self.toyota, numero_orden='OT-0077'
        )
        self.ot_luis = OrdenTrabajo.objects.create(
            empresa=self.empresa, cliente=self.luis, vehiculo=self.kia, numero_orden='OT-0100'
        )

        # Otra empresa con un cliente homónimo: no debe colarse en los resultados.
        otra = Empresa.objects.create(nombre_comercial='Otra', ruc='1790000000222')
        homonimo = Cliente.objects.create(empresa=otra, nombre='Ana Andrade', identificacion='0101010101')
        OrdenTrabajo.objects.create(empresa=otra, cliente=homonimo, vehiculo=self.kia, numero_orden='OT-0077')

        user = get_user_model().objects.create_superuser(username='busqueda', password='x')
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def _ordenes(self, termino):
        respuesta = self.client.get('/api/ordenes/ordenes-trabajo/', {'search': termino})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return sorted(o['numero_orden'] for o in respuesta.json()['results'])

    def _vehiculos(self, termino):
        respuesta = self.client.get('/api/vehiculos/', {'search': termino})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return sorted(v['placa'] for v in respuesta.json()['results'])

    def test_busca_en_campo_propio_y_relacionados(self):
        self.assertEqual(self._ordenes('OT-0077'), ['OT-0077'])
        self.assertEqual(self._ordenes('andrade'), ['OT-0077'])        # cliente__nombre
        self.assertEqual(self._ordenes('0202020202'), ['OT-0100'])     # cliente__identificacion
        self.assertEqual(self._ordenes('GSU58'), ['OT-0100'])          # vehiculo__placa
        self.assertEqual(self._ordenes('hilux'), ['OT-0077'])          # vehiculo__modelo

    def test_varios_terminos_se_combinan_con_and(self):
        self.assertEqual(self._ordenes('Ana Toyota'), ['OT-0077'])
        self.assertEqual(self._ordenes('Ana Kia'), [])

    def test_termino_corto_acota_sobre_los_demas(self):
        # "77" (< 3 caracteres) se aplica como filtro sobre lo que acota "Andrade".
        self.assertEqual(self._ordenes('Andrade 77'), ['OT-0077'])
        self.assertEqual(self._ordenes('Andrade 99'), [])
        # Solo términos cortos: se resuelve igual (sin índice).
        self.assertEqual(self._ordenes('77'), ['OT-0077'])

    def test_encuentra_documentos_de_un_cliente_eliminado(self):
        self.ana.delete()  # borrado lógico: la OT conserva su cliente
        self.assertEqual(self._ordenes('andrade'), ['OT-0077'])

    def test_relacion_inversa_sin_filas_duplicadas(self):
        # propietarios__cliente__nombre: el Toyota tiene dos propietarios que coinciden.
        self.assertEqual(self._vehiculos('a'), ['GSU5812', 'PBX1234'])
        self.assertEqual(self._vehiculos('Vera'), ['GSU5812', 'PBX1234'])
        self.assertEqual(self._vehiculos('Andrade'), ['PBX1234'])

    def test_placa_con_o_sin_guion(self):
        self.assertEqual(self._vehiculos('GSU-5812'), ['GSU5812'])
        self.assertEqual(self._vehiculos('gsu 5812'), ['GSU5812'])

    def test_no_distingue_tildes(self):
        from apps.clientes.models import Cliente

        Cliente.objects.filter(pk=self.luis.pk).update(nombre='Luis Núñez Vera')
        self.assertEqual(self._ordenes('nunez'), ['OT-0100'])
        self.assertEqual(self._ordenes('NÚÑEZ'), ['OT-0100'])
        self.assertEqual(self._ordenes('andrádé'), ['OT-0077'])

    def test_comodines_de_like_se_buscan_literalmente(self):
        self.assertEqual(self._ordenes('%'), [])
        self.assertEqual(self._ordenes('OT_0'), [])
