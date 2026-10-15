"""Plantilla estándar de listados PDF (``apps.core.utils.pdf_export``)."""

import re
from io import BytesIO

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework.test import APIClient, APITestCase

from apps.clientes.models import Cliente
from apps.core.utils.pdf_export import (
    ESTADOS_FILTRO_ACTIVOS_POR_DEFECTO,
    FILTRO_BUSQUEDA,
    FILTRO_ESTADO,
    PAGINA,
    describir_filtros,
    exportar_clientes_pdf,
    generar_listado_pdf,
)
from apps.empresas.models import Empresa, Taller


def _paginas(pdf: bytes):
    """Tamaños (ancho, alto) de cada página del PDF."""
    return [
        (float(a), float(b))
        for a, b in re.findall(rb'/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]', pdf)
    ]


class DescribirFiltrosTest(SimpleTestCase):
    def test_traduce_y_omite_vacios(self):
        filtros = describir_filtros(
            {'search': 'carlos', 'estado': 'inactivo', 'tipo': '', 'anio': '2020'},
            [
                FILTRO_BUSQUEDA,
                FILTRO_ESTADO,
                ('tipo', 'Tipo', {'C': 'Cédula'}),
                ('anio', 'Año', None),
            ],
        )
        self.assertEqual(
            filtros,
            [('Búsqueda', '“carlos”'), ('Estado', 'Inactivos'), ('Año', '2020')],
        )

    def test_valor_por_defecto_cuando_no_llega_el_parametro(self):
        filtros = describir_filtros({}, [('estado', 'Estado', ESTADOS_FILTRO_ACTIVOS_POR_DEFECTO)])
        self.assertEqual(filtros, [('Estado', 'Activos')])

    def test_callable_vacio_omite_el_filtro(self):
        definicion = [('stock_bajo', 'Stock bajo', lambda v: 'Sí' if v == 'true' else '')]
        self.assertEqual(describir_filtros({'stock_bajo': 'false'}, definicion), [])


class GenerarListadoPdfTest(SimpleTestCase):
    def _generar(self, columnas, filas, **kwargs):
        buffer = BytesIO()
        generar_listado_pdf(buffer, titulo='Listado de Prueba', columnas=columnas, filas=filas, **kwargs)
        return buffer.getvalue()

    def test_listado_vacio_genera_una_pagina(self):
        pdf = self._generar([('Nombre', 2.0), ('Total', 1.0, 'R')], [])
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(len(_paginas(pdf)), 1)

    def test_listado_largo_continua_en_varias_paginas(self):
        filas = [[f'Registro {i}', f'{i}.00'] for i in range(150)]
        pdf = self._generar([('Nombre', 2.0), ('Total', 1.0, 'R')], filas, filtros=[('Estado', 'Activos')])
        self.assertGreater(len(_paginas(pdf)), 1)

    def test_orientacion_automatica(self):
        estrecho = self._generar([('A', 3.0), ('B', 3.0)], [['a', 'b']])
        ancho = self._generar([('A', 5.0), ('B', 5.0)], [['a', 'b']])
        self.assertLess(_paginas(estrecho)[0][0], _paginas(estrecho)[0][1])
        self.assertGreater(_paginas(ancho)[0][0], _paginas(ancho)[0][1])
        self.assertAlmostEqual(_paginas(ancho)[0][0], PAGINA[1], places=1)

    def test_clientes_con_datos_de_dict(self):
        buffer = BytesIO()
        exportar_clientes_pdf(
            buffer,
            [{
                'identificacion': '1710987654',
                'nombre': 'CARLOS JARAMILLO',
                'telefono': '0998877665',
                'email': 'carlos@mail.com',
                'vehiculos': [{'placa': 'PCH3310', 'detalle': 'BMW 320i'}],
                'saldo': '12.50',
            }],
            filtros=[('Búsqueda', '“carlos”')],
        )
        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))


class ListadosPdfEndpointsTest(APITestCase):
    """Cada listado responde un PDF con la plantilla estándar (con y sin filtros)."""

    ENDPOINTS = [
        ('/api/clientes/exportar-pdf/', {'search': 'demo', 'estado': 'activo', 'tipo_identificacion': 'C'}),
        ('/api/vehiculos/exportar-pdf/', {'estado': 'activo', 'anio': '2018'}),
        ('/api/proveedores/exportar-pdf/', {'estado': 'inactivo'}),
        ('/api/repuestos/exportar-pdf/', {'stock_bajo': 'true'}),
        ('/api/servicios/exportar-pdf/', {'search': 'freno'}),
        ('/api/auth/empleados/exportar-pdf/', {'rol': 'ASESOR', 'acceso': 'con'}),
        ('/api/configuracion/sucursales/exportar-pdf/', {'ciudad': 'Guayaquil'}),
        ('/api/recepciones/exportar-pdf/', {}),
    ]

    def setUp(self):
        self.user = User.objects.create_superuser(username='tester', password='x')
        self.empresa = Empresa.objects.create(
            nombre_comercial='GonMotor', razon_social='GON SA', ruc='0925033667001',
        )
        self.taller = Taller.objects.create(
            empresa=self.empresa, nombre='Matriz', ciudad='Guayaquil', direccion='Guayaquil Norte',
        )
        Cliente.objects.create(
            empresa=self.empresa, tipo_identificacion='C', identificacion='1723492458', nombre='Cliente Demo',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = self.empresa.pk

    def test_todos_los_listados_generan_pdf(self):
        for url, filtros in self.ENDPOINTS:
            for params in ({}, filtros):
                with self.subTest(url=url, params=params):
                    resp = self.client.get(url, params)
                    self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
                    self.assertEqual(resp['Content-Type'], 'application/pdf')
                    self.assertTrue(b''.join(resp.streaming_content).startswith(b'%PDF'))
