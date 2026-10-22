"""HTTP público limitado e segredos protegidos pelo usuário do Windows."""
import base64,ctypes,http.client,ipaddress,json,re,socket,ssl
from ctypes import wintypes
from urllib.parse import urlsplit,urlunsplit,urljoin

class SourceUnavailable(ValueError):pass

def public_url(value):
    value=str(value or '').strip()
    if not value or len(value)>1600 or re.search(r'[\x00-\x20\x7f]',value):raise ValueError('Informe uma URL pública válida.')
    p=urlsplit(value)
    try:port=p.port
    except ValueError:raise ValueError('Porta inválida.') from None
    if p.scheme not in {'http','https'} or not p.hostname or p.username is not None or p.password is not None or port not in {None,80,443}:raise ValueError('Use uma URL pública HTTP ou HTTPS, sem login.')
    host=p.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    if host=='localhost' or host.endswith(('.localhost','.local','.internal')):raise ValueError('Endereços locais não podem ser consultados.')
    try:address=ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[a-z0-9.-]+',host):raise ValueError('Domínio inválido.') from None
    else:
        if not address.is_global:raise ValueError('Endereços privados não podem ser consultados.')
    netloc=('['+host+']' if ':' in host else host)+((':'+str(port)) if port else '')
    return urlunsplit((p.scheme,netloc,p.path or '/',p.query,''))

def public_ip(host,port):
    try:addresses={r[4][0] for r in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)}
    except OSError:raise SourceUnavailable('Não foi possível resolver o domínio.') from None
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):raise ValueError('O domínio aponta para uma rede privada ou reservada.')
    return sorted(addresses,key=lambda a:(':' in a,a))[0]

def request(url,*,method='GET',headers=None,body=None,timeout=9,limit=800000,redirects=0):
    url=public_url(url);p=urlsplit(url);port=p.port or (443 if p.scheme=='https' else 80)
    address=public_ip(p.hostname,port)
    con=http.client.HTTPConnection(p.hostname,port,timeout=timeout)
    try:
        con.sock=socket.create_connection((address,port),timeout)
        if p.scheme=='https':con.sock=ssl.create_default_context().wrap_socket(con.sock,server_hostname=p.hostname)
        con.request(method,(p.path or '/')+('?' + p.query if p.query else ''),body=body,headers={'User-Agent':'ProspectaX-Pesquisa/1.0','Accept-Encoding':'identity',**(headers or {})})
        response=con.getresponse();status=response.status;meta=dict(response.getheaders());raw=response.read(limit+1)
        if len(raw)>limit:raise SourceUnavailable('A página excede o limite desta consulta.')
    except (OSError,http.client.HTTPException):raise SourceUnavailable('A fonte não respondeu. Tente novamente mais tarde.') from None
    finally:con.close()
    if status in {301,302,303,307,308} and redirects:
        location=next((v for k,v in meta.items() if k.lower()=='location'),'')
        if not location:raise SourceUnavailable('Redirecionamento sem destino.')
        # Credentials are never forwarded to a redirect.
        if headers and any(k.lower() in {'authorization','x-subscription-token'} for k in headers):raise SourceUnavailable('Redirecionamento não permitido para APIs autenticadas.')
        return request(urljoin(url,location),timeout=timeout,limit=limit,redirects=redirects-1)
    return status,raw,meta,url

def json_api(url,headers=None,data=None,timeout=22):
    status,raw,_,_=request(url,method='POST' if data is not None else 'GET',headers={'Accept':'application/json',**({'Content-Type':'application/json'} if data is not None else {}),**(headers or {})},body=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None,timeout=timeout,limit=2000000)
    if status!=200:raise SourceUnavailable(f'O serviço respondeu HTTP {status}. Confira configuração, disponibilidade e limite da conta.')
    try:return json.loads(raw)
    except (ValueError,UnicodeError):raise SourceUnavailable('O serviço retornou uma resposta inválida.') from None

def _dpapi(raw,decrypt=False):
    if not hasattr(ctypes,'WinDLL'):raise ValueError('A proteção de chaves desta instalação exige o Windows.')
    class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));result=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];fn.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):raise ValueError('Não foi possível acessar a chave protegida. Cadastre-a novamente nesta conta do Windows.')
    try:return ctypes.string_at(result.data,result.size)
    finally:kernel.LocalFree(result.data)

def protect(value):return base64.b64encode(_dpapi(value.encode('utf-8'))).decode('ascii')
def unprotect(value):return _dpapi(base64.b64decode(value),True).decode('utf-8') if value else ''
