"""Private ingress verifies Gateway context and rechecks current platform authorization."""

import asyncio
from contextvars import ContextVar
import time

from zf.integrations.controller.binding import ManagedDenied
from zf.integrations.controller.installation import Installation
from zf.integrations.controller.workspace_transport import WorkspaceTransport


# None is standalone, False is managed-but-unproven, True is a verified request only.
request_authority = ContextVar('zf_controller_request_authority', default=None)


class AccessVerifier:
    def __init__(self, config):
        self.config = config

    def verify(self, token, method, path):
        from joserfc import jwt
        from joserfc.jwk import KeySet
        import httpx2

        if not token or len(token) > 12000:
            raise ManagedDenied('Gateway access context required')
        # The issuer/JWKS URL is installation-pinned, never taken from an untrusted JWT.
        with httpx2.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = client.get(self.config.gateway_origin + '/__zf/jwks.json')
            response.raise_for_status()
            if len(response.content) > 16384:
                raise ManagedDenied('invalid Gateway key response')
            keys = response.json()
        if not isinstance(keys.get('keys'), list) or not 1 <= len(keys['keys']) <= 4:
            raise ManagedDenied('invalid Gateway keys')
        if any(key.get('kty') != 'OKP' or key.get('crv') != 'Ed25519'
               or key.get('alg') != 'EdDSA' for key in keys['keys']):
            raise ManagedDenied('unsupported Gateway signing key')
        claims = jwt.decode(token, KeySet.import_key_set(keys), algorithms=['EdDSA']).claims
        now = time.time()
        credentials = Installation(self.config).credentials()
        if (type(claims.get('iat')) is not int or type(claims.get('exp')) is not int
                or not now - 25 <= claims['iat'] <= now + 3
                or not now < claims['exp'] <= claims['iat'] + 25
                or claims.get('iss') != self.config.gateway_origin
                or claims.get('origin') != self.config.gateway_origin
                or claims.get('aud') != 'zf-workspace:' + self.config.workspace_id
                or claims.get('workspace_id') != self.config.workspace_id
                or claims.get('deployment_id') != credentials['deployment_id']
                or claims.get('generation') != credentials['generation']
                or claims.get('method') != method or claims.get('path') != path):
            raise ManagedDenied('Gateway request binding mismatch')
        self.current(claims)
        return claims

    def current(self, claims):
        credentials = Installation(self.config).credentials()
        with WorkspaceTransport(self.config, credentials) as transport:
            result = transport.request('POST', '/internal/v1/me/access/check', {
                'protocol': 'zfc.control.v1', **{key: claims[key] for key in
                    ('sub', 'sid', 'auth_version', 'policy_version', 'origin')}})
        expected = {key: claims[key] for key in ('sub', 'sid', 'auth_version', 'policy_version',
                                                  'origin', 'workspace_id', 'deployment_id', 'generation')}
        if (result.get('protocol') != 'zfc.control.v1' or result.get('authorized') is not True
                or any(result.get(key) != value for key, value in expected.items())):
            raise ManagedDenied('platform authorization is no longer current')


class ManagedAccessMiddleware:
    def __init__(self, app, config, verifier=None):
        self.app, self.config = app, config
        self.verifier = verifier or AccessVerifier(config)

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        context = request_authority.set(False)
        task = None
        started = False

        async def tracked_send(message):
            nonlocal started
            if message['type'] in {'http.response.start', 'websocket.accept'}:
                started = True
            await send(message)

        async def denied():
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 4403})
            elif not started:
                await send({'type': 'http.response.start', 'status': 403,
                            'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store')]})
                await send({'type': 'http.response.body', 'body': b'{"error":"managed_access_denied"}'})
            else:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})

        application_error = False
        try:
            headers = scope.get('headers', [])
            tokens = [v.decode('ascii') for k, v in headers if k.lower() == b'x-zf-access-context']
            if len(tokens) != 1:
                raise ManagedDenied('one Gateway context required')
            method = scope.get('method', 'WEBSOCKET')
            claims = await asyncio.to_thread(self.verifier.verify, tokens[0], method, scope['path'])
            scope['zf.access'] = claims
            # Gateway enforces browser Origin too; enforce it again before local side effects.
            origins = [v.decode('ascii') for k, v in headers if k.lower() == b'origin']
            if ((method not in {'GET', 'HEAD', 'OPTIONS'} or scope['type'] == 'websocket')
                    and origins != [self.config.gateway_origin]):
                raise ManagedDenied('managed request origin mismatch')
            request_authority.set(True)
            task = asyncio.create_task(self.app(scope, receive, tracked_send))
            while True:
                done, _ = await asyncio.wait({task}, timeout=8)
                if done:
                    application_error = True
                    return await task
                await asyncio.to_thread(self.verifier.current, claims)
        except asyncio.CancelledError:
            raise
        except Exception:
            if application_error:
                raise
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await denied()
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            request_authority.reset(context)


def install_access(app):
    from zf.integrations.controller.binding import managed_config

    config = managed_config()
    if config is not None:
        # Eager credential check: misconfigured managed Web must never bind a public socket.
        Installation(config).credentials()
        app.add_middleware(ManagedAccessMiddleware, config=config)
        from zf.integrations.controller.exports import install_export_route
        install_export_route(app, config)
