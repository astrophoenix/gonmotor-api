"""Servicio único de imágenes (`apps/core/utils/images.py`) y su uso en la API."""
import os
import time
from io import BytesIO, StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.authentication.models import UsuarioEmpresa
from apps.clientes.models import Cliente
from apps.empresas.models import Empresa, Taller
from apps.ordenes.models import FotoInspeccion, InspeccionVehiculo
from apps.vehiculos.models import Vehiculo, vincular_empresa

from .testing import fijar_empresa, imagen_de_prueba
from .utils.images import HEIC_DISPONIBLE, ImagenInvalida, procesar_imagen


def _archivo(datos, nombre='foto.jpg', tipo='image/jpeg'):
    return SimpleUploadedFile(nombre, datos, content_type=tipo)


def _abrir(contenido):
    contenido.seek(0)
    return Image.open(BytesIO(contenido.read()))


def _jpeg_con_exif(ancho, alto, orientacion=1, gps=False):
    img = Image.new('RGB', (ancho, alto), 'blue')
    exif = Image.Exif()
    exif[0x0112] = orientacion  # Orientation
    exif[0x0110] = 'Telefono de prueba'  # Model
    if gps:
        exif[0x8825] = {1: 'S', 2: (0.0, 10.0, 0.0)}  # GPSInfo
    buffer = BytesIO()
    img.save(buffer, format='JPEG', exif=exif)
    return buffer.getvalue()


class ProcesarImagenTests(SimpleTestCase):

    def test_endereza_segun_el_exif(self):
        # Orientación 6: la cámara guardó la foto girada 90°.
        procesada = procesar_imagen(_archivo(_jpeg_con_exif(200, 100, orientacion=6)), 'evidencia')
        self.assertEqual(_abrir(procesada.archivo).size, (100, 200))

    def test_elimina_los_metadatos(self):
        procesada = procesar_imagen(_archivo(_jpeg_con_exif(200, 100, gps=True)), 'evidencia')
        self.assertEqual(dict(_abrir(procesada.archivo).getexif()), {})

    def test_reduce_por_el_lado_mayor_y_guarda_en_webp(self):
        procesada = procesar_imagen(_archivo(imagen_de_prueba(3000, 4000, 'JPEG')), 'evidencia')
        img = _abrir(procesada.archivo)
        self.assertEqual((img.format, img.size), ('WEBP', (1440, 1920)))
        self.assertTrue(procesada.archivo.name.endswith('.webp'))

    def test_genera_la_miniatura(self):
        procesada = procesar_imagen(_archivo(imagen_de_prueba(1600, 1200, 'JPEG')), 'evidencia')
        self.assertEqual(_abrir(procesada.miniatura).size, (400, 300))
        self.assertTrue(procesada.miniatura.name.endswith('_min.webp'))

    def test_no_agranda_imagenes_pequenas(self):
        procesada = procesar_imagen(_archivo(imagen_de_prueba(300, 200, 'JPEG')), 'evidencia')
        self.assertEqual(_abrir(procesada.archivo).size, (300, 200))

    def test_nombre_unico_sin_el_nombre_original(self):
        a = procesar_imagen(_archivo(imagen_de_prueba(), 'IMG_casa.jpg'), 'evidencia')
        b = procesar_imagen(_archivo(imagen_de_prueba(), 'IMG_casa.jpg'), 'evidencia')
        self.assertNotEqual(a.archivo.name, b.archivo.name)
        self.assertNotIn('casa', a.archivo.name)

    def test_acepta_png_y_rellena_la_transparencia_en_blanco(self):
        datos = imagen_de_prueba(50, 50, 'PNG', modo='RGBA', color=(255, 0, 0, 0))
        img = _abrir(procesar_imagen(_archivo(datos, 'x.png', 'image/png'), 'evidencia').archivo)
        self.assertEqual(img.mode, 'RGB')
        self.assertEqual(img.getpixel((10, 10)), (255, 255, 255))

    def test_el_logo_conserva_la_transparencia(self):
        datos = imagen_de_prueba(50, 50, 'PNG', modo='RGBA', color=(255, 0, 0, 0))
        img = _abrir(procesar_imagen(_archivo(datos, 'x.png', 'image/png'), 'logo').archivo)
        self.assertEqual(img.mode, 'RGBA')
        self.assertEqual(img.getpixel((10, 10))[3], 0)

    def test_la_firma_se_guarda_en_png(self):
        procesada = procesar_imagen(_archivo(imagen_de_prueba(400, 100), 'f.png', 'image/png'), 'firma')
        self.assertEqual(_abrir(procesada.archivo).format, 'PNG')
        self.assertIsNone(procesada.miniatura)

    def test_rechaza_un_archivo_que_no_es_imagen(self):
        with self.assertRaisesMessage(ImagenInvalida, 'no es una imagen válida'):
            procesar_imagen(_archivo(b'%PDF-1.4 esto no es una foto'), 'evidencia')

    def test_rechaza_formatos_no_permitidos(self):
        with self.assertRaisesMessage(ImagenInvalida, 'Formato no permitido'):
            procesar_imagen(_archivo(imagen_de_prueba(formato='GIF'), 'x.gif', 'image/gif'), 'evidencia')

    @override_settings(IMAGEN_MAX_MB=1)
    def test_rechaza_por_peso(self):
        with self.assertRaisesMessage(ImagenInvalida, 'el máximo es 1 MB'):
            procesar_imagen(_archivo(b'x' * (1024 * 1024 + 1)), 'evidencia')

    @override_settings(IMAGEN_MAX_MEGAPIXELES=1)
    def test_rechaza_por_pixeles(self):
        with self.assertRaisesMessage(ImagenInvalida, 'megapíxeles'):
            procesar_imagen(_archivo(imagen_de_prueba(1200, 1000, 'JPEG')), 'evidencia')

    def test_acepta_heic(self):
        if not HEIC_DISPONIBLE:
            self.skipTest('pillow-heif no instalado')
        datos = imagen_de_prueba(2400, 1800, 'HEIF')
        procesada = procesar_imagen(_archivo(datos, 'IMG.HEIC', 'image/heic'), 'evidencia')
        self.assertEqual(_abrir(procesada.archivo).size, (1920, 1440))

    def test_el_pdf_incrusta_webp(self):
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import Image as ImagenPdf, SimpleDocTemplate

        procesada = procesar_imagen(_archivo(imagen_de_prueba(800, 600, 'JPEG')), 'evidencia')
        procesada.archivo.seek(0)
        salida = BytesIO()
        SimpleDocTemplate(salida, pagesize=A4).build([ImagenPdf(BytesIO(procesada.archivo.read()), 200, 150)])
        self.assertTrue(salida.getvalue().startswith(b'%PDF'))


class ImagenesApiTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre_comercial='Fotos', ruc='1790000000777')
        cls.otra = Empresa.objects.create(nombre_comercial='Otra', ruc='1790000000778')
        cls.taller = Taller.objects.create(empresa=cls.empresa, nombre='Centro', codigo_sucursal='001', direccion='x')
        cls.admin = get_user_model().objects.create_superuser(username='fotos', password='x')
        UsuarioEmpresa.objects.create(user=cls.admin, empresa=cls.empresa, rol='ADMIN_EMPRESA')
        cliente = Cliente.objects.create(empresa=cls.empresa, nombre='Cliente', identificacion='1700000001')
        cls.vehiculo = Vehiculo.objects.create(placa='FOT0001', marca='Kia', modelo='Rio')
        vincular_empresa(cls.vehiculo, cls.empresa.pk)
        cls.inspeccion = InspeccionVehiculo.objects.create(
            empresa=cls.empresa, sucursal=cls.taller, cliente=cliente, vehiculo=cls.vehiculo, numero_inspeccion='INS-1',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        fijar_empresa(self.client, self.empresa)

    def _subir_foto(self, **extra):
        foto = _archivo(imagen_de_prueba(2400, 1800, 'JPEG'))
        return self.client.post(
            '/api/ordenes/inspeccion-fotos/',
            {'inspeccion': str(self.inspeccion.uuid), 'imagen': foto, **extra},
            format='multipart',
        )

    def test_la_foto_se_procesa_y_expone_imagen_y_miniatura(self):
        respuesta = self._subir_foto()

        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertTrue(respuesta.data['imagen'].startswith('http'))
        self.assertTrue(respuesta.data['imagen'].endswith('.webp'))
        self.assertTrue(respuesta.data['miniatura'].endswith('_min.webp'))
        foto = FotoInspeccion.objects.get(uuid=respuesta.data['id'])
        self.assertEqual((foto.imagen.width, foto.imagen.height), (1920, 1440))

    def test_sin_miniatura_se_usa_la_imagen(self):
        # Fotos subidas antes de existir las miniaturas.
        foto = FotoInspeccion.objects.create(inspeccion=self.inspeccion, imagen='ordenes/vieja.jpg')
        respuesta = self.client.get(f'/api/ordenes/inspeccion-fotos/{foto.uuid}/')
        self.assertEqual(respuesta.data['miniatura'], respuesta.data['imagen'])

    def test_rechaza_un_archivo_falso(self):
        respuesta = self.client.post(
            '/api/ordenes/inspeccion-fotos/',
            {'inspeccion': str(self.inspeccion.uuid), 'imagen': _archivo(b'no soy una foto')},
            format='multipart',
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('imagen', respuesta.data)

    def test_un_reintento_con_el_mismo_id_no_duplica_la_foto(self):
        id_cliente = '01920000-0000-7000-8000-000000000001'
        primera = self._subir_foto(id=id_cliente)
        reintento = self._subir_foto(id=id_cliente)

        self.assertEqual(primera.status_code, 201, primera.content)
        self.assertEqual(primera.data['id'], id_cliente)
        self.assertEqual(reintento.status_code, 200, reintento.content)
        self.assertEqual(reintento.data['id'], id_cliente)
        self.assertEqual(self.inspeccion.fotos.count(), 1)

    def test_un_id_de_otra_empresa_se_rechaza_sin_revelarla(self):
        cliente = Cliente.objects.create(empresa=self.otra, nombre='Ajeno', identificacion='1700000002')
        ajena = InspeccionVehiculo.objects.create(
            empresa=self.otra, cliente=cliente, vehiculo=self.vehiculo, numero_inspeccion='INS-X',
        )
        foto = FotoInspeccion.objects.create(inspeccion=ajena, imagen='ordenes/ajena.jpg')

        respuesta = self._subir_foto(id=str(foto.uuid))

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(str(respuesta.data['id']), 'El identificador ya está en uso.')

    def test_reemplazar_la_imagen_borra_el_archivo_anterior_al_confirmar(self):
        url = f'/api/vehiculos/{self.vehiculo.uuid}/imagen/'
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'imagen': _archivo(imagen_de_prueba(800, 600, 'JPEG'))}, format='multipart')
        self.vehiculo.refresh_from_db()
        anteriores = [self.vehiculo.imagen.path, self.vehiculo.miniatura.path]
        self.assertTrue(all(os.path.exists(r) for r in anteriores))

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(url, {'imagen': _archivo(imagen_de_prueba(800, 600, 'JPEG'))}, format='multipart')

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.vehiculo.refresh_from_db()
        self.assertTrue(os.path.exists(self.vehiculo.imagen.path))
        self.assertFalse(any(os.path.exists(r) for r in anteriores))

    def test_borrar_la_foto_borra_sus_archivos(self):
        respuesta = self._subir_foto()
        foto = FotoInspeccion.objects.get(uuid=respuesta.data['id'])
        rutas = [foto.imagen.path, foto.miniatura.path]

        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/api/ordenes/inspeccion-fotos/{foto.uuid}/')

        self.assertFalse(any(os.path.exists(r) for r in rutas))

    def test_los_limites_se_publican(self):
        respuesta = self.client.get('/api/core/limites-imagenes/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['perfiles']['evidencia']['lado_mayor'], 1920)
        self.assertEqual(respuesta.data['perfiles']['evidencia']['max_bytes'], settings.IMAGEN_MAX_MB * 1024 * 1024)
        self.assertEqual(respuesta.data['max_fotos']['inspeccion'], FotoInspeccion.MAX_FOTOS)
        self.assertIn('image/jpeg', respuesta.data['formatos_aceptados'])


class LimpiarMediaHuerfanaTests(TestCase):

    def _crear(self, relativo, antiguo=True):
        ruta = Path(settings.MEDIA_ROOT) / relativo
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(b'x')
        if antiguo:
            hace_dos_horas = time.time() - 7200
            os.utime(ruta, (hace_dos_horas, hace_dos_horas))
        return ruta

    def test_lista_y_borra_solo_los_huerfanos_antiguos(self):
        vehiculo = Vehiculo.objects.create(placa='HUE0001', marca='Kia', modelo='Rio', imagen='vehiculos/usada.webp')
        usada = self._crear(vehiculo.imagen.name)
        huerfana = self._crear('vehiculos/huerfana.webp')
        reciente = self._crear('vehiculos/subiendo.webp', antiguo=False)

        salida = StringIO()
        call_command('limpiar_media_huerfana', stdout=salida)
        self.assertIn('vehiculos/huerfana.webp', salida.getvalue())
        self.assertTrue(huerfana.exists())

        call_command('limpiar_media_huerfana', '--aplicar', stdout=StringIO())
        self.assertFalse(huerfana.exists())
        self.assertTrue(usada.exists())
        self.assertTrue(reciente.exists())
