"""Local image snapshots, restricted to references in an imported message."""
import base64
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import ipaddress
import socket
import urllib.request

ET.register_namespace('', 'http://www.w3.org/2000/svg')

MAX_IMAGE = 10 * 1024 * 1024


def remote_image(url, timeout=5):
    """Only export explicitly referenced public HTTP images; never reach local services."""
    def validate(value):
        parsed=urlsplit(value)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('图片网址无效')
        try:
            addresses=socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('远程图片地址不能指向本机或内网')
        except OSError: raise ValueError('远程图片地址暂时无法连接') from None
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,request,fp,code,msg,headers,newurl):
            validate(newurl)
            return super().redirect_request(request,fp,code,msg,headers,newurl)
    validate(url)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'ConversationNotes/0.6','Accept':'image/*'})
        with opener.open(request,timeout=timeout) as response:
            data=response.read(MAX_IMAGE+1)
        return image_bytes(data)[0]
    except ValueError as error:
        raise ValueError('远程图片未能嵌入：'+str(error)) from None
    except OSError:
        raise ValueError('远程图片暂时无法下载，请保存到本机后再插入') from None


def references(body):
    result = []
    # Parse destinations without treating balanced path parentheses as the end.
    for match in re.finditer(r'!\[(?:\\.|[^\]\\])*\]\(', body):
        start = match.end()
        if start < len(body) and body[start] == '<':
            end = body.find('>', start + 1)
            if end >= 0:
                result.append(unescape(body[start + 1:end]))
            continue
        end, depth = start, 0
        while end < len(body):
            c = body[end]
            if c == '\\' and end + 1 < len(body):
                end += 2; continue
            if c == ')' and depth == 0:
                break
            if c == '(': depth += 1
            if c == ')': depth -= 1
            if c == '\n': break
            end += 1
        value = re.split(r'\s+["\']', body[start:end], maxsplit=1)[0].strip()
        if value:
            result.append(unescape(re.sub(r'\\([()\\])', r'\1', value)))
    definitions = {}
    normalize = lambda value: re.sub(r'\s+', ' ', value).strip().casefold()
    for label, angle, plain in re.findall(r'^ {0,3}\[([^\]\n]+)\]:\s*(?:<([^>\n]+)>|(\S+))',body,re.M):
        definitions[normalize(label)] = unescape(angle or plain)
    for label, target in re.findall(r'!\[([^\]\n]+)\](?:\[([^\]\n]*)\])?(?!\()',body):
        value = definitions.get(normalize(target or label))
        if value: result.append(value)
    result += re.findall(r':codex-file-citation\{path="([^"]+)"[^}]*\}', body)
    return list(dict.fromkeys(result))


def image_bytes(data):
    if len(data) > MAX_IMAGE:
        raise ValueError('图片超过 10 MB，请使用较小的图片')
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return data, 'image/png'
    if data.startswith(b'\xff\xd8\xff'): return data, 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'): return data, 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return data, 'image/webp'
    if data.lstrip().startswith(b'<'):
        if len(data) > 2 * 1024 * 1024 or re.search(br'<!DOCTYPE|<!ENTITY', data, re.I):
            raise ValueError('不支持这份 SVG 图片')
        try: root = ET.fromstring(data)
        except ET.ParseError: raise ValueError('SVG 图片无效') from None
        allowed = {'svg','g','defs','path','rect','circle','ellipse','line','polyline','polygon','text','tspan','title','desc','clipPath','mask','linearGradient','radialGradient','stop','use','symbol','marker'}
        attrs = {'id','class','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','width','height','viewBox','preserveAspectRatio','d','points','transform','fill','fill-opacity','fill-rule','stroke','stroke-width','stroke-opacity','stroke-linecap','stroke-linejoin','stroke-dasharray','opacity','font-family','font-size','font-weight','text-anchor','dominant-baseline','dx','dy','offset','stop-color','stop-opacity','clip-path','mask','gradientUnits','gradientTransform','markerWidth','markerHeight','refX','refY','orient','marker-end','marker-start','href'}
        if root.tag.rsplit('}',1)[-1] != 'svg': raise ValueError('不是 SVG 图片')
        def sanitize(node):
            for key, value in list(node.attrib.items()):
                name = key.rsplit('}',1)[-1]
                if name not in attrs or (name == 'href' and not value.startswith('#')) or re.search(r'url\((?!\s*#[\w-]+\s*\))|javascript:|data:',value,re.I):
                    del node.attrib[key]
            for child in list(node):
                if child.tag.rsplit('}',1)[-1] not in allowed: node.remove(child)
                else: sanitize(child)
        sanitize(root)
        return ET.tostring(root, encoding='utf-8'), 'image/svg+xml'
    raise ValueError('图片格式不支持；可显示 PNG、JPEG、GIF、WebP 和 SVG')


def save_image(directory, value, filename='图片'):
    if isinstance(value, str):
        if value.startswith('data:image/'):
            try: data = base64.b64decode(value.split(',', 1)[1], validate=True)
            except (ValueError, IndexError): raise ValueError('图片数据无效') from None
        else:
            path = Path(unquote(value.removeprefix('file://'))).expanduser()
            try:
                if not path.is_absolute() or not path.is_file(): raise ValueError('找不到这张本机图片')
                if path.stat().st_size > MAX_IMAGE: raise ValueError('图片超过 10 MB')
                data = path.read_bytes(); filename = path.name
            except OSError: raise ValueError('这张本机图片暂时无法读取') from None
    else: data = value
    data, mime = image_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    folder = Path(directory) / 'images'; folder.mkdir(mode=0o700, exist_ok=True)
    target = folder / digest
    if not target.exists():
        target.write_bytes(data); target.chmod(0o600)
    return {'asset': digest, 'mime': mime, 'filename': filename}


def load_image(directory, item):
    if not re.fullmatch(r'[a-f0-9]{64}', item.get('asset', '')): raise ValueError('图片编号无效')
    path = Path(directory) / 'images' / item['asset']
    if not path.is_file(): raise ValueError('找不到保存的图片，请重新同步原对话')
    data, mime = image_bytes(path.read_bytes())
    return {'filename': item.get('filename', '图片'), 'mime': mime, 'blob': base64.b64encode(data).decode()}


def content_images(content):
    images = []
    for c in content:
        if c.get('type') not in ('input_image', 'image_url', 'image', 'local_image'): continue
        value = c.get('image_url') or c.get('url') or c.get('path')
        if isinstance(value, dict): value = value.get('url')
        if isinstance(value, str): images.append(value)
    return images
