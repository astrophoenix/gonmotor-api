from pathlib import Path
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image


MAX_WIDTH = 1200
JPEG_QUALITY = 80
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}


def validate_image_extension(file):
    ext = Path(file.name).suffix.lower().lstrip('.')
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Formato no permitido: .{ext}. Solo se aceptan JPG, JPEG, PNG o WebP."
        )
    return ext


def optimize_image(file, max_width=MAX_WIDTH, quality=JPEG_QUALITY):
    img = Image.open(file)
    original_ext = Path(file.name).suffix.lower().lstrip('.')
    has_transparency = img.mode in ('RGBA', 'P') and 'transparency' in img.info

    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()

    if has_transparency and original_ext in {'png', 'webp'}:
        if original_ext == 'webp':
            img.save(buffer, format='WEBP', quality=quality, optimize=True)
            content_type = 'image/webp'
            new_ext = 'webp'
        else:
            img.save(buffer, format='PNG', optimize=True)
            content_type = 'image/png'
            new_ext = 'png'
    else:
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        content_type = 'image/jpeg'
        new_ext = 'jpg'

    buffer.seek(0)
    original_name = Path(file.name).stem
    new_name = f"{original_name}.{new_ext}"

    return InMemoryUploadedFile(
        buffer, 'ImageField', new_name, content_type, buffer.tell(), None
    )
