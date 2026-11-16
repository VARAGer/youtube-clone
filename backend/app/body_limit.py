"""Bound request bytes before Starlette's multipart spooling, including chunked bodies."""
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
class BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413,'Request body exceeds size limit')
class BodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app=app
        self.max_bytes=max_bytes
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] in ('GET','HEAD','OPTIONS'):
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        maximum=self.max_bytes if scope['path']=='/api/videos' else 128*1024
        try:
            if int(headers.get(b'content-length',b'0'))>maximum:
                raise BodyTooLarge()
        except ValueError:
            return await JSONResponse({'detail':'Invalid Content-Length'},400)(scope,receive,send)
        except BodyTooLarge:
            return await JSONResponse({'detail':'Request body exceeds size limit'},413)(scope,receive,send)
        received=0
        async def limited_receive():
            nonlocal received
            message=await receive()
            if message['type']=='http.request':
                received+=len(message.get('body',b''))
                if received>maximum:
                    raise BodyTooLarge()
            return message
        try:
            await self.app(scope,limited_receive,send)
        except BodyTooLarge:
            await JSONResponse({'detail':'Request body exceeds size limit'},413)(scope,receive,send)
