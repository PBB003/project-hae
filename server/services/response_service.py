"""Validadores sin caché global y límite conservador de tokens, sin descargar vocabularios."""
import base64
import copy
import hashlib
import json

from server.policy import current_identity


def serialized(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def upper_bound(payload):
    # Para tokenizadores BPE de bytes, cada token contiene al menos un byte.
    return len(serialized(payload).encode('utf-8'))


def respond(payload, scope, max_tokens=4096, if_none_match=None):
    if not 512 <= max_tokens <= 65536:
        raise ValueError('max_tokens debe estar entre 512 y 65536')
    identity = current_identity.get()
    digest = hashlib.sha256(serialized([scope, identity.user_id, identity.role, max_tokens, payload]).encode()).digest()
    etag = base64.urlsafe_b64encode(digest).decode().rstrip('=')
    if if_none_match and if_none_match == etag:
        return {'not_modified': True, 'etag': etag}
    result = copy.deepcopy(payload)
    result['etag'] = etag
    omitted_results = omitted_types = omitted_contracts = 0
    while upper_bound(result) > max_tokens:
        rows = result.get('results', [])
        if not rows:
            # Contextos/reglas se omiten como bloques, nunca se presentan como íntegros.
            if result.get('context'):
                result.pop('context')
                omitted_contracts += 1
            elif result.get('rules'):
                result['rules'].pop()
                omitted_results += 1
            else:
                raise ValueError('Presupuesto insuficiente para los metadatos')
        elif rows[-1].get('types'):
            rows[-1]['types'].pop()
            omitted_types += 1
        elif rows[-1].get('contract') and result.get('level') == 'contract':
            rows[-1].pop('contract')
            rows[-1]['contract_omitted'] = True
            omitted_contracts += 1
        else:
            rows.pop()
            omitted_results += 1
        result['budget'] = {'max_tokens': max_tokens, 'counting': 'utf8_bytes_upper_bound',
                            'omitted_results': omitted_results, 'omitted_types': omitted_types,
                            'omitted_contracts': omitted_contracts}
        result['partial'] = True
        result['hint'] = 'Aumenta max_tokens o recupera el símbolo/tipo exacto por archivo.'
    return result
