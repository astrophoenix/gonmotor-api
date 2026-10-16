"""Utilidades para tests de la API."""


def fijar_empresa(client, empresa):
    """Ata la sesión del `APIClient` a `empresa`, como lo hace el JWT real.

    La empresa sale solo de la claim `empresa_id` del token: en los tests se
    simula con `force_authenticate(user, token={...})`. Llamarla después de
    `force_authenticate(user)`: sin usuario falla (un cliente anónimo no tiene
    sesión que atar, y fallar evita que el test pase sin la empresa fijada).

    `empresa`: instancia de `Empresa` o su ID público.
    """
    user = client.handler._force_user
    if user is None:
        raise AssertionError('fijar_empresa() requiere force_authenticate(user) antes.')
    empresa_id = str(getattr(empresa, 'uuid', empresa))
    client.force_authenticate(user=user, token={'empresa_id': empresa_id})


def imagen_de_prueba(ancho=80, alto=60, formato='PNG', color='red', modo='RGB', **guardar):
    """Bytes de una imagen real (Pillow), para subir con `SimpleUploadedFile`."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new(modo, (ancho, alto), color=color).save(buffer, format=formato, **guardar)
    return buffer.getvalue()


def firma_data_url():
    """Firma como la envía el canvas de la web: `data:image/png;base64,...`."""
    import base64

    return 'data:image/png;base64,' + base64.b64encode(
        imagen_de_prueba(400, 100, modo='RGBA', color=(0, 0, 0, 0))
    ).decode()
