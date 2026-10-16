"""Los listados del flujo no hacen consultas por fila (N+1).

Cada documento se serializa con relaciones anidadas (la recepción dentro de la
orden, la inspección dentro de la recepción, líneas con su catálogo, el
responsable con su rol). Si una vista deja de precargarlas, el número de
consultas crece con las filas: estos tests lo detectan comparando 2 y 6
documentos.
"""
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.citas.models import Cita
from apps.clientes.models import Cliente
from apps.core.testing import fijar_empresa
from apps.cotizaciones.models import Cotizacion
from apps.empresas.models import Empresa, Taller
from apps.inventario.models import Repuesto, Servicio
from apps.vehiculos.models import Vehiculo, vincular_empresa

from .models import (
    DetalleRepuestoInspeccion,
    DetalleServicioInspeccion,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)


class ConsultasListadosTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.empresa = Empresa.objects.create(nombre_comercial='Consultas', ruc='1790000000555')
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Centro', codigo_sucursal='001', direccion='x')
        cls.admin = User.objects.create_superuser(username='consultas', password='x')
        cls.asesor = User.objects.create_user(username='consultas-asesor', password='x', first_name='Ana')
        UsuarioEmpresa.objects.create(user=cls.asesor, empresa=cls.empresa, rol='ASESOR')
        cls.servicio = Servicio.objects.create(empresa=cls.empresa, codigo='S-1', nombre='Alineación')
        cls.repuesto = Repuesto.objects.create(empresa=cls.empresa, codigo='R-1', nombre='Filtro')
        cls.creados = 0

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        fijar_empresa(self.client, self.empresa)

    def _documentos(self, cantidad):
        """`cantidad` órdenes, cada una con recepción, inspección y líneas."""
        for _ in range(cantidad):
            type(self).creados += 1
            n = self.creados
            cliente = Cliente.objects.create(empresa=self.empresa, nombre=f'Cliente {n}', identificacion=f'17{n:08d}')
            vehiculo = Vehiculo.objects.create(placa=f'CON{n:04d}', marca='Kia', modelo='Rio')
            vincular_empresa(vehiculo, self.empresa.pk)
            comun = {'empresa': self.empresa, 'sucursal': self.taller, 'cliente': cliente, 'vehiculo': vehiculo}
            orden = OrdenTrabajo.objects.create(**comun, numero_orden=f'OT-{n}', asesor=self.asesor)
            recepcion = RecepcionVehiculo.objects.create(
                **comun, numero_recepcion=f'REC-{n}', recibido_por=self.asesor, orden_trabajo=orden,
            )
            inspeccion = InspeccionVehiculo.objects.create(
                **comun, numero_inspeccion=f'INS-{n}', recepcion=recepcion, responsable=self.asesor,
                orden_trabajo=orden,
            )
            DetalleServicioInspeccion.objects.create(
                inspeccion=inspeccion, servicio=self.servicio, descripcion='Alinear', precio_referencial=Decimal('10'),
            )
            DetalleRepuestoInspeccion.objects.create(
                inspeccion=inspeccion, repuesto=self.repuesto, descripcion='Filtro', precio_referencial=Decimal('5'),
            )
            Cotizacion.objects.create(
                **comun, numero_cotizacion=f'COT-{n}', asesor=self.asesor,
                inspeccion_origen=inspeccion, recepcion_origen=recepcion,
            )
            Cita.objects.create(
                empresa=self.empresa, taller=self.taller, cliente=cliente, vehiculo=vehiculo, asesor=self.asesor,
                fecha_cita=date.today() + timedelta(days=n), hora_cita=time(9, 0), recepcion_generada=recepcion,
            )

    def _consultas(self, url):
        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return len(consultas.captured_queries), len(respuesta.json()['results'])

    def test_el_numero_de_consultas_no_crece_con_las_filas(self):
        urls = (
            '/api/recepciones/',
            '/api/ordenes/inspecciones/',
            '/api/ordenes/ordenes-trabajo/',
            '/api/cotizaciones/',
            '/api/citas/',
        )
        self._documentos(2)
        con_dos = {url: self._consultas(url) for url in urls}
        self._documentos(4)
        for url in urls:
            with self.subTest(url=url):
                consultas_6, filas_6 = self._consultas(url)
                consultas_2, filas_2 = con_dos[url]
                self.assertEqual((filas_2, filas_6), (2, 6))
                self.assertEqual(consultas_6, consultas_2, f'{url}: {consultas_2} → {consultas_6} consultas')
