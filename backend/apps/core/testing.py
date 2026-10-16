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
