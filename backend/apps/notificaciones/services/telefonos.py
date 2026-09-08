"""Utilidades de normalización de números de teléfono (WhatsApp).

WhatsApp exige el número en formato internacional E.164 (ej. +593991234567).
Aquí normalizamos los formatos locales más comunes de Ecuador:

- 0991234567   -> 593991234567
- 9 912 345 67 -> 593991234567
- +593 99 123 4567 -> 593991234567
"""

import re


def normalizar_celular(telefono):
    """Normaliza un teléfono local a formato E.164 sin el signo '+'."""
    if not telefono:
        return ''

    digitos = re.sub(r'\D', '', str(telefono))

    # 10 dígitos que empiezan con 09 (celular ecuatoriano) -> 5939XXXXXXXX
    if len(digitos) == 10 and digitos.startswith('09'):
        return '593' + digitos[1:]

    # 9 dígitos que empiezan con 9 (celular sin el 0 inicial) -> +5939XXXXXXXX
    if len(digitos) == 9 and digitos.startswith('9'):
        return '593' + digitos

    # Ya está en formato internacional 593XXXXXXXXXX
    if len(digitos) == 12 and digitos.startswith('593'):
        return digitos

    # Con signo '+' ya incluido pero con espacios/guiones
    if digitos.startswith('00'):
        return digitos[2:]

    return digitos


def celular_para_wa_link(telefono):
    """Devuelve el número listo para enlaces wa.me (con '+')."""
    normalizado = normalizar_celular(telefono)
    return f'+{normalizado}' if normalizado else ''