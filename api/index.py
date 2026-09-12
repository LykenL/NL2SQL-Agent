# Pure standard library WSGI app to satisfy Vercel AST validation
def app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b'Dummy Vercel Python Handler']
