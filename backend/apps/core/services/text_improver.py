"""Mejora de textos técnicos mediante LLM (API compatible con OpenAI).

Servicio genérico y reutilizable: cualquier entidad puede mejorar sus campos
de texto libre (motivo, observaciones, etc.) apuntando a /api/core/mejorar-texto/.

Características:
- Sin modelo hardcodeado: aunque AI_TEXT_MODEL quede vacío, descubre y usa el
  mejor modelo al que la API key tenga acceso.
- Fallback automático y transparente: si el modelo preferido deja de estar
  disponible, se busca otro en la lista de modelos del proveedor y se reintenta.
- Caché de la lista de modelos para no consultar /models en cada request.
- No envía datos personales (identificaciones, placas, teléfonos); solo el texto.
"""

import threading
import time

import requests
from decouple import config

MAX_TEXT_LENGTH = 500
MAX_CONTEXTO_LENGTH = 120

MODEL_EXCLUDE_PATTERNS = (
    'whisper',
    'prompt-guard',
    'safeguard',
    'orpheus',
    'embed',
    'rerank',
)

PREFER_SUBSTRINGS = (
    'qwen3.8-27b',
    'qwen3.6-27b',
    'qwen3',
    'gpt-oss-120b',
    'gpt-oss-20b',
    'gpt-oss',
    'compound',
    'allam',
    'llama',
    'mistral',
)

_MODELS_CACHE_TTL = 3600
_models_cache = {
    'fetched_at': None,
    'model_ids': [],
}
_models_lock = threading.Lock()


def _config():
    return {
        'api_key': config('AI_TEXT_API_KEY', default=''),
        'base_url': config('AI_TEXT_BASE_URL', default='https://api.groq.com/openai/v1').rstrip('/'),
        'model': config('AI_TEXT_MODEL', default='').strip(),
        'timeout': config('AI_TEXT_TIMEOUT', cast=int, default=30),
    }


def construir_prompt(contexto=''):
    prompt = (
        'Eres un asistente experto en redacción técnica para talleres automotrices. '
        'Reescribe el texto escrito por el mecánico de forma profesional, clara y '
        'concisa en español.'
    )
    if contexto:
        prompt += f' El texto corresponde a «{contexto}».'
    prompt += (
        ' Corrige la ortografía y la gramática, ordena las ideas y mejora la redacción, '
        'pero conserva exactamente toda la información técnica y el significado original. '
        'No inventes ni agregues datos que no estén en el texto. Devuelve únicamente el '
        'texto mejorado, sin comillas, sin aclaraciones y sin saludos.'
    )
    return prompt


class TextImproverError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def _es_modelo_chat(model_id):
    model_id = (model_id or '').lower()
    if not model_id:
        return False
    return not any(pattern in model_id for pattern in MODEL_EXCLUDE_PATTERNS)


def _rank_model(model_id):
    model_id = model_id.lower()
    for index, substr in enumerate(PREFER_SUBSTRINGS):
        if substr in model_id:
            return (index, model_id)
    return (len(PREFER_SUBSTRINGS), model_id)


def _get_models(force=False):
    """Devuelve los modelos de chat disponibles, ordenados por preferencia pedagógica.

    Usa caché en memoria con expiración; si force=True ignora la caché.
    En caso de error de red retorna lo último que se tenía en caché.
    """
    now = time.time()
    with _models_lock:
        cached = (
            _models_cache['fetched_at'] is not None
            and now - _models_cache['fetched_at'] < _MODELS_CACHE_TTL
        )
        if cached and not force and _models_cache['model_ids']:
            return list(_models_cache['model_ids'])

        cfg = _config()
        if not cfg['api_key']:
            return list(_models_cache['model_ids'])

        try:
            response = requests.get(
                f"{cfg['base_url']}/models",
                headers={'Authorization': f'Bearer {cfg["api_key"]}'},
                timeout=15,
            )
        except requests.RequestException:
            return list(_models_cache['model_ids'])

        if response.status_code >= 400:
            return list(_models_cache['model_ids'])

        try:
            data = response.json()
            raw_ids = [m.get('id') for m in data.get('data', []) if isinstance(m, dict)]
        except (ValueError, AttributeError):
            return list(_models_cache['model_ids'])

        chat_ids = [mid for mid in raw_ids if isinstance(mid, str) and _es_modelo_chat(mid)]
        chat_ids.sort(key=_rank_model)
        _models_cache['fetched_at'] = now
        _models_cache['model_ids'] = chat_ids
        return list(chat_ids)


def _candidatos(force=False):
    """Modelos a intentar en orden: primero el preferido (si se configuró),
    luego los disponibles por ranking."""
    cfg = _config()
    preferido = cfg['model'] or ''
    disponibles = _get_models(force=force)
    candidatos = []
    if preferido:
        candidatos.append(preferido)
    for model_id in disponibles:
        if model_id != preferido and model_id not in candidatos:
            candidatos.append(model_id)
    return candidatos


def _llamar(cfg, model, contexto_limpio, texto):
    url = f"{cfg['base_url']}/chat/completions"
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': construir_prompt(contexto_limpio)},
            {'role': 'user', 'content': texto},
        ],
        'temperature': 0.3,
        'max_tokens': 512,
    }
    headers = {
        'Authorization': f'Bearer {cfg["api_key"]}',
        'Content-Type': 'application/json',
    }
    try:
        return requests.post(url, json=payload, headers=headers, timeout=cfg['timeout'])
    except requests.exceptions.Timeout as exc:
        raise TextImproverError(
            'El servicio de mejora tardó demasiado en responder. Intenta nuevamente.', 503
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise TextImproverError(
            'No fue posible conectar con el servicio de mejora de texto.', 503
        ) from exc


def _es_error_de_modelo(response):
    """True si el proveedor rechazó por modelo inexistente/sin acceso."""
    if response.status_code not in (400, 404):
        return False
    try:
        payload = response.json()
        error = payload.get('error', {}) or {}
        message = str(error.get('message', '')).lower()
        code = str(error.get('code', '')).lower()
    except ValueError:
        return False
    return 'model' in message or 'model' in code


def _extraer_resultado(response):
    try:
        data = response.json()
        contenido = data['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise TextImproverError('El servicio no devolvió una respuesta válida.', 502) from exc

    mejorado = _limpiar_respuesta(contenido)
    if not mejorado:
        raise TextImproverError('El servicio no pudo mejorar el texto.', 502)
    return mejorado


def _limpiar_respuesta(texto):
    texto = (texto or '').strip()
    if not texto:
        return ''
    if texto.startswith('```'):
        lines = texto.splitlines()
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        texto = '\n'.join(lines).strip()
    for estilo in ('"', "'", '\u201c', '\u201d'):
        if len(texto) >= 2 and texto[0] == estilo and texto[-1] == estilo:
            texto = texto[1:-1].strip()
    return texto


def mejorar_texto(texto, contexto=''):
    texto = (texto or '').strip()
    if not texto:
        raise TextImproverError('No hay texto para mejorar.', 400)
    if len(texto) > MAX_TEXT_LENGTH:
        raise TextImproverError(
            f'El texto no puede superar {MAX_TEXT_LENGTH} caracteres.', 400
        )

    contexto_limpio = (contexto or '').strip()[:MAX_CONTEXTO_LENGTH]

    cfg = _config()
    if not cfg['api_key']:
        raise TextImproverError(
            'La mejora de texto no está configurada en el servidor. '
            'Contacta al administrador para habilitarla.',
            503,
        )

    candidatos = _candidatos()
    if not candidatos:
        raise TextImproverError(
            'No hay modelos disponibles para el servicio de mejora de texto.', 503
        )

    ultimo_error_modelo = None
    for _ in range(2):  # máximo 2 pasadas: la segunda tras refrescar la lista
        for model in candidatos:
            response = _llamar(cfg, model, contexto_limpio, texto)
            if response.status_code < 400:
                return _extraer_resultado(response)
            if response.status_code == 429:
                raise TextImproverError(
                    'El servicio de mejora de texto está saturado. '
                    'Espera unos segundos e intenta nuevamente.',
                    503,
                )
            if _es_error_de_modelo(response):
                ultimo_error_modelo = response
                continue
            raise TextImproverError(
                'El servicio de mejora de texto respondió con un error. Intenta nuevamente.', 502
            )
        nuevos = _candidatos(force=True)
        if nuevos == candidatos:
            break
        candidatos = nuevos

    if ultimo_error_modelo is not None:
        raise TextImproverError(
            'El proveedor de mejora de texto no tiene modelos disponibles. '
            'Verifica la configuración e intenta nuevamente.',
            502,
        )
    raise TextImproverError(
        'El servicio de mejora de texto respondió con un error. Intenta nuevamente.', 502
    )