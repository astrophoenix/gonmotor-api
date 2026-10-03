from decimal import Decimal
from unittest.mock import MagicMock

from django.db import IntegrityError
from django.test import SimpleTestCase

from rest_framework import serializers
from rest_framework.test import APIClient, APITestCase

from apps.vehiculos.models import Vehiculo

from .models import Cotizacion
from .pdf import _consolidar
from .serializers import transicion_estado_valida
from .views import _traducir_integridad, _validar_origen_cotizacion, _validar_origen_inspeccion


class ConsolidarConceptosTests(SimpleTestCase):
    """El PDF agrupa ítems idénticos y suma sus cantidades (ahorra alto de página)."""

    def _servicio(self, descripcion, horas, precio='25.00', descuento='0.00',
                  iva='0.1500', es_opcional=False, codigo='SRV-01'):
        item = MagicMock()
        item.codigo = codigo
        item.descripcion = descripcion
        item.horas_estimadas = Decimal(horas)
        item.precio_unitario = Decimal(precio)
        item.descuento = Decimal(descuento)
        item.iva_porcentaje = Decimal(iva)
        item.es_opcional = es_opcional
        item.subtotal = Decimal(horas) * Decimal(precio) - Decimal(descuento)
        return item

    def _consolidar_servicios(self, items):
        return _consolidar(
            items,
            campo_cantidad='horas_estimadas',
            campo_codigo='codigo',
            campo_precio='precio_unitario',
        )

    def test_suma_cantidad_de_items_identicos(self):
        grupos = self._consolidar_servicios([
            self._servicio('Cambio de aceite', '2.00'),
            self._servicio('Cambio de aceite', '1.50'),
            self._servicio('Cambio de aceite', '0.50'),
        ])

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['cantidad'], Decimal('4.00'))
        self.assertEqual(grupos[0]['subtotal'], Decimal('100.00'))

    def test_no_confunde_items_con_datos_distintos(self):
        grupos = self._consolidar_servicios([
            self._servicio('Cambio de aceite', '2.00'),
            self._servicio('Cambio de aceite', '2.00', precio='30.00'),      # otro precio
            self._servicio('Cambio de aceite', '2.00', iva='0.0000'),       # otro IVA
            self._servicio('Cambio de aceite', '2.00', es_opcional=True),   # otro status
            self._servicio('Cambio de aceite', '2.00', codigo='SRV-02'),    # otro código
        ])

        self.assertEqual(len(grupos), 5)

    def test_conservar_el_importe_total_del_grupo(self):
        items = [
            self._servicio('Alineación', '1.50', precio='40.00', descuento='5.00'),
            self._servicio('Alineación', '1.50', precio='40.00', descuento='5.00'),
        ]
        grupos = self._consolidar_servicios(items)

        self.assertEqual(grupos[0]['descuento'], Decimal('10.00'))
        self.assertEqual(
            grupos[0]['subtotal'],
            sum(item.subtotal for item in items),
        )

    def test_consolida_repuestos_por_cantidad(self):
        repuestos = []
        for cantidad in (2, 3):
            item = MagicMock()
            item.codigo_repuesto = 'RP-77'
            item.descripcion = 'Filtro de aceite'
            item.cantidad = cantidad
            item.precio_unitario_referencial = Decimal('12.50')
            item.descuento = Decimal('0.00')
            item.iva_porcentaje = Decimal('0.1500')
            item.es_opcional = False
            item.subtotal = Decimal(cantidad) * Decimal('12.50')
            repuestos.append(item)

        grupos = _consolidar(
            repuestos,
            campo_cantidad='cantidad',
            campo_codigo='codigo_repuesto',
            campo_precio='precio_unitario_referencial',
        )

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['cantidad'], Decimal('5'))
        self.assertEqual(grupos[0]['subtotal'], Decimal('62.50'))

    def test_lista_vacia_o_sin_conceptos(self):
        self.assertEqual(self._consolidar_servicios([]), [])
        self.assertEqual(self._consolidar_servicios(None), [])


class TransicionEstadoTests(SimpleTestCase):
    def test_transiciones_validas_del_flujo(self):
        casos = [
            (Cotizacion.EstadoCotizacion.PENDIENTE, Cotizacion.EstadoCotizacion.ENVIADA, True),
            (Cotizacion.EstadoCotizacion.ENVIADA, Cotizacion.EstadoCotizacion.ACEPTADA, True),
            (Cotizacion.EstadoCotizacion.ENVIADA, Cotizacion.EstadoCotizacion.RECHAZADA, True),
            (Cotizacion.EstadoCotizacion.ACEPTADA, Cotizacion.EstadoCotizacion.ENVIADA, True),
            (Cotizacion.EstadoCotizacion.ACEPTADA, Cotizacion.EstadoCotizacion.CONVERTIDA, False),
            (Cotizacion.EstadoCotizacion.RECHAZADA, Cotizacion.EstadoCotizacion.ENVIADA, True),
            (Cotizacion.EstadoCotizacion.CONVERTIDA, Cotizacion.EstadoCotizacion.ENVIADA, False),
        ]
        for actual, nuevo, esperado in casos:
            with self.subTest(actual=actual, nuevo=nuevo):
                self.assertEqual(transicion_estado_valida(actual, nuevo), esperado)

    def test_mismo_estado_siempre_valido(self):
        for estado in Cotizacion.EstadoCotizacion:
            if estado.value != Cotizacion.EstadoCotizacion.ACEPTADA:
                self.assertTrue(transicion_estado_valida(estado, estado))

    def test_no_hay_salto_de_pendiente_a_aceptada(self):
        self.assertFalse(
            transicion_estado_valida(Cotizacion.EstadoCotizacion.PENDIENTE, Cotizacion.EstadoCotizacion.ACEPTADA)
        )


class ValidarOrigenCotizacionTests(SimpleTestCase):
    def _origen(self, **atributos):
        origen = MagicMock()
        origen.orden_trabajo_id = None
        origen.numero_inspeccion = 'INS-00001'
        origen.numero_recepcion = 'REC-00001'
        cotizacion_vigente = atributos.pop('vigente', None)
        origen.cotizaciones_generadas.filter.return_value.first.return_value = cotizacion_vigente
        for clave, valor in atributos.items():
            setattr(origen, clave, valor)
        return origen

    def _vigente(self, numero='COT-00001'):
        cotizacion = MagicMock()
        cotizacion.numero_cotizacion = numero
        return cotizacion

    def test_sin_origenes_no_valida(self):
        self.assertIsNone(_validar_origen_cotizacion())

    def test_cotizacion_independiente_sin_inspeccion_aprueba(self):
        # Cliente que llama por teléfono: no hay recepción ni inspección.
        self.assertIsNone(_validar_origen_cotizacion())

    def test_inspeccion_con_orden_trabajo_rechazada(self):
        inspeccion = self._origen(orden_trabajo_id=10)
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(inspeccion=inspeccion)

    def test_inspeccion_con_cotizacion_aceptada_rechazada(self):
        inspeccion = self._origen(vigente=self._vigente('COT-00004'))
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(inspeccion=inspeccion)

    def test_inspeccion_con_cotizacion_borrador_rechazada(self):
        inspeccion = self._origen(vigente=self._vigente())
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(inspeccion=inspeccion)

    def test_inspeccion_libre_aprueba(self):
        self.assertIsNone(_validar_origen_cotizacion(inspeccion=self._origen()))

    def test_recepcion_con_cotizacion_vigente_rechazada(self):
        recepcion = self._origen(vigente=self._vigente('COT-00007'))
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_cotizacion(recepcion=recepcion)

    def test_recepcion_libre_aprueba(self):
        self.assertIsNone(_validar_origen_cotizacion(recepcion=self._origen()))

    def test_mensaje_incluye_numero_de_cotizacion_vigente(self):
        recepcion = self._origen(vigente=self._vigente('COT-00007'))
        with self.assertRaises(serializers.ValidationError) as contexto:
            _validar_origen_cotizacion(recepcion=recepcion)
        self.assertIn('COT-00007', str(contexto.exception))

    def test_validar_origen_inspeccion_mantiene_compatibilidad(self):
        with self.assertRaises(serializers.ValidationError):
            _validar_origen_inspeccion(self._origen(vigente=self._vigente()))
        self.assertIsNone(_validar_origen_inspeccion(None))


class TraducirIntegridadTests(SimpleTestCase):
    def _origen(self, numero_cotizacion='COT-00007', estado_display='Aceptada'):
        origen = MagicMock()
        cotizacion = MagicMock()
        cotizacion.numero_cotizacion = numero_cotizacion
        cotizacion.get_estado_display.return_value = estado_display
        filtrado = origen.cotizaciones_generadas.filter.return_value
        filtrado.exclude.return_value = filtrado
        filtrado.first.return_value = cotizacion
        return origen

    def test_constraint_de_recepcion_da_error_de_validacion(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_recepcion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError):
            _traducir_integridad(error)

    def test_constraint_de_inspeccion_da_error_de_validacion(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_inspeccion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError):
            _traducir_integridad(error)

    def test_mensaje_identifica_la_cotizacion_en_conflicto(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_recepcion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError) as contexto:
            _traducir_integridad(error, recepcion=self._origen())
        mensaje = str(contexto.exception)
        self.assertIn('COT-00007', mensaje)
        self.assertIn('Aceptada', mensaje)

    def test_mensaje_sin_origen_no_rompe(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_inspeccion_vigente_unica"')
        with self.assertRaises(serializers.ValidationError) as contexto:
            _traducir_integridad(error)
        self.assertIn('inspección', str(contexto.exception))

    def test_otros_errores_se_dejan_pasar(self):
        error = IntegrityError('duplicate key value violates unique constraint "cotizacion_empresa_numero_unico"')
        self.assertIs(_traducir_integridad(error), error)


class ActualizacionOrigenInmutableTests(SimpleTestCase):
    def test_reapertura_choca_con_la_maquina_de_estados(self):
        # Reabrir es una transición válida, pero el constraint de la BD impide
        # que dos cotizaciones del mismo origen queden vigentes a la vez.
        self.assertTrue(
            transicion_estado_valida(Cotizacion.EstadoCotizacion.RECHAZADA, Cotizacion.EstadoCotizacion.ENVIADA)
        )
        self.assertTrue(
            transicion_estado_valida(Cotizacion.EstadoCotizacion.ACEPTADA, Cotizacion.EstadoCotizacion.ENVIADA)
        )
        self.assertIn(
            Cotizacion.EstadoCotizacion.ACEPTADA,
            Cotizacion.ESTADOS_VIGENTES,
        )
        self.assertNotIn(
            Cotizacion.EstadoCotizacion.RECHAZADA,
            Cotizacion.ESTADOS_VIGENTES,
        )


class GenerarOrdenTests(SimpleTestCase):
    def _cotizacion(self, estado, orden_origen_id=None, inspeccion=None, recepcion=None, total='50.00', con_detalles=True):
        cotizacion = MagicMock()
        cotizacion.orden_trabajo_origen_id = orden_origen_id
        cotizacion.estado = estado
        cotizacion.total = Decimal(total)
        cotizacion.EstadoCotizacion = Cotizacion.EstadoCotizacion
        cotizacion.inspeccion_origen_id = inspeccion.id if inspeccion is not None else None
        cotizacion.inspeccion_origen = inspeccion
        cotizacion.recepcion_origen = recepcion
        if con_detalles:
            cotizacion.servicios.exists.return_value = True
        else:
            cotizacion.servicios.exists.return_value = False
            cotizacion.repuestos.exists.return_value = False
        cotizacion.generar_orden = Cotizacion.generar_orden.__get__(cotizacion, Cotizacion)
        return cotizacion

    def _origen(self, orden_trabajo_id=None):
        origen = MagicMock()
        origen.orden_trabajo_id = orden_trabajo_id
        origen.numero_inspeccion = 'INS-00001'
        origen.numero_recepcion = 'REC-00001'
        origen.recepcion_id = None
        return origen

    def test_con_orden_trabajo_origen_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ENVIADA, orden_origen_id=7)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()

    def test_ya_convertida_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.CONVERTIDA)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()

    def test_no_llama_creacion_cuando_hay_orden_origen(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ENVIADA, orden_origen_id=1)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_inspeccion_ya_convertida_en_otra_ot_rechazada(self):
        inspeccion = self._origen(orden_trabajo_id=5)
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, inspeccion=inspeccion)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_recepcion_ya_con_ot_rechazada(self):
        recepcion = self._origen(orden_trabajo_id=5)
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, recepcion=recepcion)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_sin_detalles_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, con_detalles=False)
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_total_cero_rechazada(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA, total='0.00')
        with self.assertRaises(ValueError):
            cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_not_called()

    def test_cotizacion_valida_llega_a_crear_la_orden(self):
        cotizacion = self._cotizacion(Cotizacion.EstadoCotizacion.ACEPTADA)
        cotizacion.generar_orden()
        cotizacion._crear_orden_trabajo.assert_called_once()


class CotizacionAsesorApiTests(APITestCase):
    """El asesor de la cotización se persiste desde el API y se expone en la
    representación (campo asesor + asesor_nombre/identificacion/telefono/email)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa, Taller
        from apps.vehiculos.models import Vehiculo

        User = get_user_model()
        self.user = User.objects.create_superuser(username='admin-cot', password='x')
        self.asesor = User.objects.create_user(
            username='ana',
            password='x',
            first_name='Ana',
            last_name='Paredes',
            email='ana@gonmotor.test',
        )
        self.empresa = Empresa.objects.create(
            nombre_comercial='Taller Asesor', razon_social='ASESOR SA', ruc='444444444444'
        )
        self.taller = Taller.objects.create(
            empresa=self.empresa, nombre='Taller Central', direccion='Av. Central 200', prefijo_cotizacion='COT-'
        )
        self.cliente = Cliente.objects.create(
            empresa=self.empresa, nombre='Carlos Ruiz', identificacion='0987654321'
        )
        self.vehiculo = Vehiculo.objects.create(placa='ASE-0001', marca='Kia', modelo='Rio')
        self.vehiculo.empresas.add(self.empresa)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)

    def test_crear_con_asesor(self):
        resp = self.client.post(
            '/api/cotizaciones/',
            {
                'cliente': self.cliente.id,
                'vehiculo': self.vehiculo.id,
                'asesor': self.asesor.id,
                'validez_dias': 15,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        self.assertEqual(data['asesor'], self.asesor.id)
        self.assertEqual(data['asesor_nombre'], 'Ana Paredes')
        self.assertEqual(data['asesor_email'], 'ana@gonmotor.test')

    def test_crear_sin_asesor_y_luego_asignarlo(self):
        resp = self.client.post(
            '/api/cotizaciones/',
            {'cliente': self.cliente.id, 'vehiculo': self.vehiculo.id, 'validez_dias': 15},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIsNone(resp.json()['asesor'])
        self.assertNotIn('asesor_nombre', resp.json())

        patch = self.client.patch(
            f"/api/cotizaciones/{resp.json()['id']}/", {'asesor': self.asesor.id}, format='json'
        )
        self.assertEqual(patch.status_code, 200, patch.content)
        self.assertEqual(patch.json()['asesor'], self.asesor.id)
        self.assertEqual(patch.json()['asesor_nombre'], 'Ana Paredes')

    def test_quitar_asesor(self):
        cot = Cotizacion.objects.create(
            empresa=self.empresa,
            sucursal=self.taller,
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            asesor=self.asesor,
        )
        resp = self.client.patch(f'/api/cotizaciones/{cot.id}/', {'asesor': None}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        cot.refresh_from_db()
        self.assertIsNone(cot.asesor_id)


class CotizacionesMultiplesPorVehiculoTests(CotizacionAsesorApiTests):
    """Un vehículo puede tener varias cotizaciones vigentes independientes."""

    def _crear(self, **extra):
        datos = {'cliente': self.cliente.id, 'vehiculo': self.vehiculo.id}
        datos.update(extra)
        return self.client.post('/api/cotizaciones/', datos, format='json')

    def _cerrar(self, cotizacion, estado):
        cotizacion.estado = estado
        cotizacion.save(update_fields=['estado', 'updated_at'])

    def test_permite_varias_cotizaciones_vigentes_para_el_mismo_vehiculo(self):
        primera = self._crear()
        self.assertEqual(primera.status_code, 201, primera.content)

        segunda = self._crear()
        self.assertEqual(segunda.status_code, 201, segunda.content)
        self.assertNotEqual(primera.json()['id'], segunda.json()['id'])
        self.assertEqual(
            Cotizacion.objects.filter(
                empresa=self.empresa,
                vehiculo=self.vehiculo,
                estado__in=Cotizacion.ESTADOS_VIGENTES,
            ).count(),
            2,
        )

    def test_vehiculo_distinto_puede_tener_su_cotizacion_vigente(self):
        otro = Vehiculo.objects.create(placa='ASE-0002', marca='Kia', modelo='Rio')
        otro.empresas.add(self.empresa)
        self.assertEqual(self._crear().status_code, 201)
        respuesta = self._crear(vehiculo=otro.id)
        self.assertEqual(respuesta.status_code, 201, respuesta.content)

    def test_cotizaciones_sin_vehiculo_no_se_bloquean(self):
        primera = self._crear(vehiculo=None)
        segunda = self._crear(vehiculo=None)
        self.assertEqual(primera.status_code, 201, primera.content)
        self.assertEqual(segunda.status_code, 201, segunda.content)

    def test_tras_convertir_el_vehiculo_queda_libre(self):
        primera = self._crear()
        numero = primera.json()['numero_cotizacion']
        self._cerrar(Cotizacion.objects.get(numero_cotizacion=numero), 'CONVERTIDA')
        segunda = self._crear()
        self.assertEqual(segunda.status_code, 201, segunda.content)

    def test_tras_rechazar_el_vehiculo_queda_libre(self):
        primera = self._crear()
        numero = primera.json()['numero_cotizacion']
        self._cerrar(Cotizacion.objects.get(numero_cotizacion=numero), 'RECHAZADA')
        segunda = self._crear()
        self.assertEqual(segunda.status_code, 201, segunda.content)

    def test_permite_asignar_un_vehiculo_con_otra_cotizacion_vigente(self):
        self._crear()
        otro = Vehiculo.objects.create(placa='ASE-0003', marca='Toyota', modelo='Hilux')
        otro.empresas.add(self.empresa)
        editable = self._crear(vehiculo=otro.id)
        self.assertEqual(editable.status_code, 201, editable.content)

        respuesta = self.client.patch(
            f"/api/cotizaciones/{editable.json()['id']}/",
            {'vehiculo': self.vehiculo.id},
            format='json',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_el_vehiculo_no_se_toca_al_crear_una_cotizacion_sin_el(self):
        # Crear la segunda cotización sin vehículo no debe desasignar nada.
        primera = self._crear()
        segunda = self._crear(vehiculo=None)
        self.assertEqual(segunda.status_code, 201, segunda.content)
        primera_cot = Cotizacion.objects.get(pk=primera.json()['id'])
        self.assertEqual(primera_cot.vehiculo_id, self.vehiculo.id)

    def test_filtro_por_vehiculo(self):
        creada = self._crear()
        otro = Vehiculo.objects.create(placa='ASE-0004', marca='Mazda', modelo='3')
        otro.empresas.add(self.empresa)
        self.assertEqual(self._crear(vehiculo=otro.id).status_code, 201)

        respuesta = self.client.get(f'/api/cotizaciones/?vehiculo={self.vehiculo.id}')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        ids = [item['id'] for item in respuesta.json()['results']]
        self.assertIn(creada.json()['id'], ids)
        self.assertEqual(len(ids), 1)


class ExportarPdfCotizacionTests(CotizacionAsesorApiTests):
    """``GET /api/cotizaciones/<id>/exportar-pdf/`` entrega el documento.

    El botón del frontend aparece desde que existe el ID, así que el PDF debe
    estar disponible en cualquier estado (incluido un borrador PENDIENTE).
    """

    def _cotizacion_con_detalles(self):
        creada = self.client.post(
            '/api/cotizaciones/',
            {'cliente': self.cliente.id, 'vehiculo': self.vehiculo.id, 'validez_dias': 15},
            format='json',
        )
        self.assertEqual(creada.status_code, 201, creada.content)
        cotizacion_id = creada.json()['id']

        servicios = self.client.post(
            '/api/cotizaciones/servicios/',
            {
                'cotizacion': cotizacion_id,
                'descripcion': 'Cambio de aceite y filtros',
                'horas_estimadas': '2.00',
                'precio_unitario': '25.00',
            },
            format='json',
        )
        self.assertEqual(servicios.status_code, 201, servicios.content)

        repuesto = self.client.post(
            '/api/cotizaciones/repuestos/',
            {
                'cotizacion': cotizacion_id,
                'descripcion': 'Filtro de aceite',
                'cantidad': 2,
                'precio_unitario_referencial': '12.50',
                'es_opcional': True,
            },
            format='json',
        )
        self.assertEqual(repuesto.status_code, 201, repuesto.content)
        return cotizacion_id

    def test_descarga_pdf_de_una_cotizacion_con_detalles(self):
        cotizacion_id = self._cotizacion_con_detalles()
        respuesta = self.client.get(f'/api/cotizaciones/{cotizacion_id}/exportar-pdf/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'%PDF'))

        cotizacion = Cotizacion.objects.get(pk=cotizacion_id)
        self.assertIn(
            cotizacion.numero_cotizacion,
            respuesta['Content-Disposition'],
        )

    def test_pdf_disponible_en_cualquier_estado(self):
        cotizacion_id = self._cotizacion_con_detalles()
        for estado in (
            Cotizacion.EstadoCotizacion.PENDIENTE,
            Cotizacion.EstadoCotizacion.ENVIADA,
            Cotizacion.EstadoCotizacion.ACEPTADA,
            Cotizacion.EstadoCotizacion.RECHAZADA,
            Cotizacion.EstadoCotizacion.CONVERTIDA,
        ):
            with self.subTest(estado=estado):
                Cotizacion.objects.filter(pk=cotizacion_id).update(estado=estado)
                respuesta = self.client.get(f'/api/cotizaciones/{cotizacion_id}/exportar-pdf/')
                self.assertEqual(respuesta.status_code, 200)

    def test_pdf_sin_detalles_no_falla(self):
        creada = self.client.post(
            '/api/cotizaciones/',
            {'cliente': self.cliente.id, 'vehiculo': None, 'validez_dias': 10},
            format='json',
        )
        respuesta = self.client.get(f"/api/cotizaciones/{creada.json()['id']}/exportar-pdf/")

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'%PDF'))

    def test_requiere_autenticacion(self):
        cotizacion_id = self._cotizacion_con_detalles()
        anonimo = APIClient()
        anonimo.defaults['HTTP_X_EMPRESA_ID'] = str(self.empresa.pk)
        respuesta = anonimo.get(f'/api/cotizaciones/{cotizacion_id}/exportar-pdf/')
        self.assertEqual(respuesta.status_code, 401)

    def test_no_filtra_cotizaciones_de_otra_empresa(self):
        from apps.clientes.models import Cliente
        from apps.empresas.models import Empresa

        otra_empresa = Empresa.objects.create(
            nombre_comercial='Otra Empresa', razon_social='OTRA SA', ruc='999999999999'
        )
        otro_cliente = Cliente.objects.create(
            empresa=otra_empresa, nombre='Cliente Ajeno', identificacion='1111111111'
        )
        ajena = Cotizacion.objects.create(
            empresa=otra_empresa,
            cliente=otro_cliente,
            numero_cotizacion='COT-AJENA-1',
        )

        respuesta = self.client.get(f'/api/cotizaciones/{ajena.pk}/exportar-pdf/')
        self.assertEqual(respuesta.status_code, 404)
