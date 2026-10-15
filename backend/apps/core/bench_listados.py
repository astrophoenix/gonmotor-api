"""
Benchmark de los listados con volumen alto (NO forma parte de la suite: el
nombre no empieza por ``test``). Se ejecuta explícitamente sobre la BD de test,
sin tocar la base local:

    python manage.py test apps.core.bench_listados

Siembra dos empresas (la tabla de vehículos es global, así que la segunda
simula a otros tenants) y mide cada listado: tiempo de la petición completa y
el plan (``EXPLAIN ANALYZE``) de la consulta más lenta. Volumen ajustable con
``BENCH_ESCALA`` (1 = 100k órdenes por empresa).

Con ``--keepdb`` los datos sembrados se conservan entre corridas (la clase no
usa transacciones), útil para comparar antes/después. Al terminar, una corrida
sin ``--keepdb`` (o ``--noinput``) elimina la BD de test.
"""
import os
import time

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

ESCALA = float(os.environ.get('BENCH_ESCALA', '1'))
N_CLIENTES = int(20_000 * ESCALA)
N_VEHICULOS = int(20_000 * ESCALA)
N_DOCUMENTOS = int(100_000 * ESCALA)
LOTE = 5_000

NOMBRES = ['Pérez', 'García', 'Rodríguez', 'López', 'Martínez', 'Sánchez', 'Romero', 'Torres', 'Andrade', 'Vera']
MARCAS = ['Toyota', 'Chevrolet', 'Kia', 'Hyundai', 'Mazda', 'Nissan', 'Renault', 'Ford']

CASOS = [
    # (etiqueta, url, búsqueda)
    ('Órdenes', '/api/ordenes/ordenes-trabajo/', None),
    ('Órdenes', '/api/ordenes/ordenes-trabajo/', 'PBX-01234'),
    ('Órdenes', '/api/ordenes/ordenes-trabajo/', 'Andrade 77'),
    ('Recepciones', '/api/recepciones/', None),
    ('Recepciones', '/api/recepciones/', 'PBX-01234'),
    ('Recepciones', '/api/recepciones/', 'REC-0000099'),
    ('Inspecciones', '/api/ordenes/inspecciones/', None),
    ('Inspecciones', '/api/ordenes/inspecciones/', 'PBX-01234'),
    ('Cotizaciones', '/api/cotizaciones/', None),
    ('Cotizaciones', '/api/cotizaciones/', 'Andrade 77'),
    ('Clientes', '/api/clientes/', None),
    ('Clientes', '/api/clientes/', 'Andrade 77'),
    ('Clientes', '/api/clientes/', '0999'),
    ('Vehículos', '/api/vehiculos/', None),
    ('Vehículos', '/api/vehiculos/', 'PBX-01234'),
    ('Vehículos', '/api/vehiculos/', 'Andrade 77'),
]


def _bulk(modelo, objetos):
    for i in range(0, len(objetos), LOTE):
        modelo.objects.bulk_create(objetos[i:i + LOTE])


class BenchListados(SimpleTestCase):
    databases = {'default'}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from apps.empresas.models import Empresa

        cls.segundos_seed = 0.0
        cls.empresas = list(Empresa.objects.filter(nombre_comercial__startswith='Bench ').order_by('id'))
        if len(cls.empresas) != 2:
            cls._sembrar()
        cls.user, _ = get_user_model().objects.get_or_create(
            username='bench', defaults={'is_superuser': True, 'is_staff': True}
        )

    @classmethod
    def _sembrar(cls):
        from apps.clientes.models import Cliente
        from apps.cotizaciones.models import Cotizacion
        from apps.empresas.models import Empresa, Taller
        from apps.ordenes.models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo
        from apps.vehiculos.models import Vehiculo, VehiculoEmpresa, VehiculoPropietario

        inicio = time.perf_counter()
        cls.empresas = []
        for e, prefijo in enumerate(['PBX', 'QRZ']):
            empresa = Empresa.objects.create(nombre_comercial=f'Bench {prefijo}', ruc=f'17900000000{e}1')
            taller = Taller.objects.create(empresa=empresa, nombre=f'Matriz {prefijo}', direccion='Av. 1')
            cls.empresas.append(empresa)

            _bulk(Cliente, [
                Cliente(
                    empresa=empresa,
                    nombre=f'{NOMBRES[i % 10]} {i}',
                    identificacion=f'09{e}{i:07d}',
                    telefono=f'099{i:07d}',
                    email=f'cliente{i}@bench{e}.ec',
                )
                for i in range(N_CLIENTES)
            ])
            clientes = list(Cliente.objects.filter(empresa=empresa).order_by('id').values_list('id', flat=True))

            _bulk(Vehiculo, [
                Vehiculo(placa=f'{prefijo}-{i:05d}', marca=MARCAS[i % 8], modelo=f'M{i % 50}')
                for i in range(N_VEHICULOS)
            ])
            vehiculos = list(
                Vehiculo.objects.filter(placa__startswith=f'{prefijo}-').order_by('id').values_list('id', flat=True)
            )
            _bulk(VehiculoEmpresa, [VehiculoEmpresa(empresa=empresa, vehiculo_id=v) for v in vehiculos])
            _bulk(VehiculoPropietario, [
                VehiculoPropietario(vehiculo_id=v, cliente_id=clientes[i % len(clientes)])
                for i, v in enumerate(vehiculos)
            ])

            def par(i):
                return clientes[i % len(clientes)], vehiculos[i % len(vehiculos)]

            for modelo, campo, pref, extra in (
                (OrdenTrabajo, 'numero_orden', 'OT', {}),
                (RecepcionVehiculo, 'numero_recepcion', 'REC', {}),
                (InspeccionVehiculo, 'numero_inspeccion', 'INS', {'motivo_ingreso': 'Ruido'}),
                (Cotizacion, 'numero_cotizacion', 'COT', {}),
            ):
                n = N_DOCUMENTOS if modelo in (OrdenTrabajo, RecepcionVehiculo) else N_DOCUMENTOS // 2
                _bulk(modelo, [
                    modelo(
                        empresa=empresa,
                        sucursal=taller,
                        cliente_id=par(i)[0],
                        vehiculo_id=par(i)[1],
                        **{campo: f'{pref}-{i:07d}'},
                        **extra,
                    )
                    for i in range(n)
                ])

        with connection.cursor() as cursor:
            cursor.execute('ANALYZE')
        cls.segundos_seed = time.perf_counter() - inicio

    def _explain(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(f'EXPLAIN (ANALYZE, BUFFERS) {sql}')
            return [fila[0] for fila in cursor.fetchall()]

    def test_medir_listados(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresas[0].pk)

        print(f'\n\nSeed: {self.segundos_seed:.1f}s · escala {ESCALA} '
              f'({N_DOCUMENTOS} órdenes/recepciones por empresa, 2 empresas)\n')
        print(f'{"Listado":<13} {"Búsqueda":<13} {"ms":>7} {"queries":>8} {"ms SQL+lenta":>13}  plan')
        detalle = []
        for etiqueta, url, termino in CASOS:
            params = {'search': termino} if termino else {}
            client.get(url, params)  # calienta caché de Postgres
            with CaptureQueriesContext(connection) as ctx:
                inicio = time.perf_counter()
                respuesta = client.get(url, params)
                ms = (time.perf_counter() - inicio) * 1000
            self.assertEqual(respuesta.status_code, 200, respuesta.content[:300])

            lenta = max(ctx.captured_queries, key=lambda q: float(q['time']))
            plan = self._explain(lenta['sql'])
            nodos = [linea.strip() for linea in plan if 'Scan' in linea or 'Join' in linea]
            resumen = ' / '.join(n.split('  (')[0].replace('->', '').strip() for n in nodos[:3])
            print(f'{etiqueta:<13} {termino or "—":<13} {ms:>7.0f} {len(ctx.captured_queries):>8} '
                  f'{float(lenta["time"]) * 1000:>13.0f}  {resumen}')
            detalle.append((etiqueta, termino, lenta['sql'][:400], plan))

        if os.environ.get('BENCH_DETALLE'):
            for etiqueta, termino, sql, plan in detalle:
                print(f'\n===== {etiqueta} · {termino}\n{sql}\n' + '\n'.join(plan))
