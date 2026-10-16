"""Servicio único de imágenes: toda imagen subida pasa por `procesar_imagen`.

El servidor no confía en lo que envía el cliente (web o app): cada imagen se
verifica, se endereza según su EXIF, pierde los metadatos (GPS, modelo del
teléfono), se limita por su lado mayor y se guarda con un nombre UUID. Las
fotos se guardan en WebP; el logo conserva su transparencia y la firma es PNG.

Los límites de bytes y píxeles viven en `settings` (configurables por `.env`)
y se publican en `GET /api/core/limites-imagenes/` para la web y la app.
"""
import base64
import binascii
import uuid
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # HEIC/HEIF de iPhone y de algunos Android (Chrome no lo convierte).
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_DISPONIBLE = True
except ImportError:  # pragma: no cover - la dependencia está en requirements
    HEIC_DISPONIBLE = False


# MPO es el JPEG multi-imagen de algunas cámaras (iPhone, Samsung).
FORMATOS_PIL_ACEPTADOS = {'JPEG', 'MPO', 'PNG', 'WEBP'} | ({'HEIF'} if HEIC_DISPONIBLE else set())
TIPOS_MIME_ACEPTADOS = ['image/jpeg', 'image/png', 'image/webp'] + (
    ['image/heic', 'image/heif'] if HEIC_DISPONIBLE else []
)
NOMBRE_FORMATOS = 'JPG, PNG, WebP' + (' o HEIC' if HEIC_DISPONIBLE else '')


@dataclass(frozen=True)
class Perfil:
    lado_mayor: int
    miniatura: int | None = None
    # 'webp' (con pérdida, sin transparencia), 'webp_sin_perdida' o 'png'.
    formato: str = 'webp'
    calidad: int = 80
    calidad_miniatura: int = 70
    # None: usa IMAGEN_MAX_MB de settings.
    max_bytes: int | None = None


PERFILES = {
    'evidencia': Perfil(lado_mayor=1920, miniatura=400),
    'vehiculo': Perfil(lado_mayor=1600, miniatura=400),
    'repuesto': Perfil(lado_mayor=1200, miniatura=300),
    'avatar': Perfil(lado_mayor=512),
    'logo': Perfil(lado_mayor=1024, formato='webp_sin_perdida'),
    'firma': Perfil(lado_mayor=1200, formato='png', max_bytes=300 * 1024),
}


class ImagenInvalida(Exception):
    """Mensaje en español listo para devolver al usuario."""


@dataclass
class ImagenProcesada:
    archivo: ContentFile
    miniatura: ContentFile | None
    ancho: int
    alto: int


def max_bytes(perfil):
    return perfil.max_bytes or settings.IMAGEN_MAX_MB * 1024 * 1024


def max_pixeles():
    return settings.IMAGEN_MAX_MEGAPIXELES * 1_000_000


def _megas(n):
    return f'{n / (1024 * 1024):.1f}'.replace('.0', '').replace('.', ',')


def _abrir_verificada(archivo, perfil):
    """Abre la imagen comprobando que lo sea de verdad y que quepa en memoria."""
    try:
        archivo.seek(0)
        with Image.open(archivo) as prueba:
            prueba.verify()
        archivo.seek(0)
        img = Image.open(archivo)
    except Image.DecompressionBombError:
        raise ImagenInvalida('La imagen tiene demasiados píxeles.')
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ImagenInvalida(f'El archivo no es una imagen válida. Usa {NOMBRE_FORMATOS}.')

    if img.format not in FORMATOS_PIL_ACEPTADOS:
        raise ImagenInvalida(f'Formato no permitido. Usa {NOMBRE_FORMATOS}.')
    ancho, alto = img.size
    if ancho * alto > max_pixeles():
        raise ImagenInvalida(
            f'La imagen mide {ancho}×{alto} píxeles; el máximo es '
            f'{settings.IMAGEN_MAX_MEGAPIXELES} megapíxeles.'
        )
    # En JPEG decodifica directamente a una escala menor: menos memoria y CPU
    # con fotos de 12–50 MP. Pide el tamaño final respetando la proporción
    # (draft nunca baja de lo pedido).
    escala = perfil.lado_mayor / max(ancho, alto)
    if escala < 1:
        img.draft('RGB', (max(1, int(ancho * escala)), max(1, int(alto * escala))))
    return img


def _tiene_transparencia(img):
    return img.mode in ('RGBA', 'LA', 'PA') or (img.mode == 'P' and 'transparency' in img.info)


def _normalizar_modo(img, conservar_alfa):
    if _tiene_transparencia(img):
        img = img.convert('RGBA')
        if conservar_alfa:
            return img
        fondo = Image.new('RGBA', img.size, (255, 255, 255, 255))
        return Image.alpha_composite(fondo, img).convert('RGB')
    return img if img.mode == 'RGB' else img.convert('RGB')


def _guardar(img, formato, calidad, icc):
    buffer = BytesIO()
    # Solo se conserva el perfil de color (evita colores apagados en fotos
    # Display P3); EXIF, GPS y demás metadatos no se copian.
    extra = {'icc_profile': icc} if icc else {}
    if formato == 'png':
        img.save(buffer, format='PNG', optimize=True, **extra)
    elif formato == 'webp_sin_perdida':
        img.save(buffer, format='WEBP', lossless=True, quality=100, method=4, **extra)
    else:
        img.save(buffer, format='WEBP', quality=calidad, method=4, **extra)
    return buffer.getvalue()


def procesar_imagen(archivo, perfil):
    """Valida y normaliza `archivo` según el perfil (clave de `PERFILES`).

    Lanza `ImagenInvalida` con un mensaje para el usuario.
    """
    perfil = PERFILES[perfil]
    tamano = getattr(archivo, 'size', None)
    if tamano is not None and tamano > max_bytes(perfil):
        limite = max_bytes(perfil)
        if limite < 1024 * 1024:
            raise ImagenInvalida(f'La imagen supera el tamaño máximo de {limite // 1024} KB.')
        raise ImagenInvalida(
            f'La imagen pesa {_megas(tamano)} MB y el máximo es {_megas(limite)} MB.'
        )

    img = _abrir_verificada(archivo, perfil)
    try:
        icc = img.info.get('icc_profile')
        img = ImageOps.exif_transpose(img)
        img.load()
    except (OSError, SyntaxError, ValueError):
        raise ImagenInvalida('La imagen está dañada o incompleta.')

    img = _normalizar_modo(img, conservar_alfa=perfil.formato != 'webp')
    img.thumbnail((perfil.lado_mayor, perfil.lado_mayor), Image.Resampling.LANCZOS)

    extension = 'png' if perfil.formato == 'png' else 'webp'
    base = uuid.uuid4().hex
    principal = ContentFile(
        _guardar(img, perfil.formato, perfil.calidad, icc), name=f'{base}.{extension}'
    )

    miniatura = None
    if perfil.miniatura:
        chica = img.copy()
        chica.thumbnail((perfil.miniatura, perfil.miniatura), Image.Resampling.LANCZOS)
        miniatura = ContentFile(
            _guardar(chica, perfil.formato, perfil.calidad_miniatura, icc), name=f'{base}_min.{extension}'
        )
    return ImagenProcesada(archivo=principal, miniatura=miniatura, ancho=img.width, alto=img.height)


def archivo_desde_data_url(valor, nombre='imagen'):
    """Convierte un `data:image/...;base64,...` (canvas) en archivo.

    Devuelve None si `valor` no es un data URL de imagen.
    """
    if not isinstance(valor, str) or not valor.startswith('data:image/'):
        return None
    try:
        _, contenido = valor.split(',', 1)
        datos = base64.b64decode(contenido, validate=True)
    except (ValueError, binascii.Error):
        raise ImagenInvalida('La imagen enviada no es válida.')
    return ContentFile(datos, name=nombre)


def limites_publicos():
    """Lo que la web y la app necesitan para comprimir antes de subir."""
    return {
        'formatos_aceptados': TIPOS_MIME_ACEPTADOS,
        'max_megapixeles': settings.IMAGEN_MAX_MEGAPIXELES,
        'perfiles': {
            nombre: {
                'lado_mayor': perfil.lado_mayor,
                'formato': 'image/png' if perfil.formato == 'png' else 'image/webp',
                'sin_perdida': perfil.formato != 'webp',
                'calidad': perfil.calidad / 100,
                'miniatura': perfil.miniatura,
                'max_bytes': max_bytes(perfil),
            }
            for nombre, perfil in PERFILES.items()
        },
    }
