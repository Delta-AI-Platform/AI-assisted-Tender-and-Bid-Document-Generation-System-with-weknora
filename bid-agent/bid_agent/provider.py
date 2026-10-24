"""Small OpenAI-compatible transport; credentials never enter model context."""
import json
import copy
import time
import os
import stat
import urllib.request
import urllib.error
from pathlib import Path


class ProviderError(RuntimeError):
    pass


class Provider:
    def __init__(self, config=None, model=None):
        path = Path(config or Path.home() / '.config/deta-bid-agent/provider.json')
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ProviderError('Provider config must be private (0600)')
        self.config = json.loads(path.read_text())
        if model:
            self.config['model'] = model
        self.on_progress = None
        if not self.config['base_url'].startswith('https://'):
            raise ProviderError('Provider requires HTTPS')

    def decide(self, messages, tools):
        messages = copy.deepcopy(messages)
        # Some compatible endpoints emit malformed JSON arguments, then reject
        # that same history on the next request. Preserve raw audit events; send
        # a valid placeholder with the rejection explained in the tool result.
        rejected = {}
        for message in messages:
            for call in message.get('tool_calls', []):
                call.pop('index', None)
                try:
                    json.loads(call['function']['arguments'])
                except (ValueError, TypeError):
                    rejected[call['id']] = call['function']['arguments']
                    call['function']['arguments'] = '{}'
            if message.get('role') == 'tool' and message.get('tool_call_id') in rejected:
                message['content'] += '\nOriginal arguments rejected as invalid JSON (not executed): ' + str(rejected[message['tool_call_id']])
        payload = {'model': self.config['model'], 'messages': messages,
                   'tools': tools, 'stream': self.config.get('stream', True), 'max_tokens': self.config.get('max_tokens', 12288)}
        if self.config.get('reasoning_effort'):
            payload['reasoning_effort'] = self.config['reasoning_effort']
        if not tools:
            payload.pop('tools', None)
        request = urllib.request.Request(
            self.config['base_url'].rstrip('/') + '/chat/completions',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + self.config['api_key']})
        # Do not follow redirects carrying credentials to another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=180) as response:
                data = self.read_stream(response) if payload['stream'] else json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode('utf-8', errors='replace').replace(self.config['api_key'], '[REDACTED]')
            raise ProviderError(f'Model request failed: HTTP {exc.code}: {detail[:1000]}') from None
        except (OSError, ValueError) as exc:
            raise ProviderError(f'Model request failed: {type(exc).__name__}') from None
        choice = data['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ProviderError('Model response truncated; no tools executed')
        return choice['message']

    def read_stream(self, response):
        message = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
        calls = {}; finish = None; started = time.monotonic(); last = started
        count = 0
        for line in response:
            if time.monotonic() - started > 600:
                raise ProviderError('Model stream exceeded 600 seconds; partial tools not executed')
            if not line.startswith(b'data:'):
                continue
            raw = line[5:].strip()
            if raw == b'[DONE]': break
            chunk = json.loads(raw)
            if chunk.get('error'): raise ProviderError('Model stream returned an error')
            for choice in chunk.get('choices', []):
                if choice.get('index', 0) != 0: continue
                delta = choice.get('delta', {})
                for key in ('content', 'reasoning_content'):
                    value = delta.get(key) or ''
                    message[key] += value; count += len(value)
                for call in delta.get('tool_calls', []):
                    index = call.get('index', 0)
                    current = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    if call.get('id'): current['id'] = call['id']
                    for key in ('name', 'arguments'):
                        value = call.get('function', {}).get(key) or ''
                        current['function'][key] += value; count += len(value)
                if choice.get('finish_reason'): finish = choice['finish_reason']
            if self.on_progress and time.monotonic() - last >= 10:
                self.on_progress({'elapsed_seconds': round(time.monotonic()-started), 'received_characters': count})
                last = time.monotonic()
        if finish is None: raise ProviderError('Model stream ended without completion; partial tools not executed')
        if calls: message['tool_calls'] = [calls[k] for k in sorted(calls)]
        return {'choices': [{'finish_reason': finish, 'message': message}]}
