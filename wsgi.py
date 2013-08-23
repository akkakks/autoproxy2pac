#!/usr/bin/env python
# coding:utf-8

import sys
import string
import logging
import urllib2
import base64
import time

AUTOPROXY_URL = 'https://autoproxy-gfwlist.googlecode.com/svn/trunk/gfwlist.txt'
URLFILTER_URL = 'https://simpleu.googlecode.com/svn/trunk/Opera/urlfilter.ini'


def autoproxy2pac(content, func_name='FindProxyForURLByAutoProxy', proxy='127.0.0.1:8087', default='DIRECT', indent=4):
    """Autoproxy to Pac, based on https://github.com/iamamac/autoproxy2pac"""
    jsLines = []
    for line in content.splitlines()[1:]:
        if line and not line.startswith("!"):
            use_proxy = True
            if line.startswith("@@"):
                line = line[2:]
                use_proxy = False
            return_proxy = 'PROXY %s' % proxy if use_proxy else default
            if line.startswith('/') and line.endswith('/'):
                jsLine = 'if (/%s/i.test(url)) return "%s";' % (line[1:-1], return_proxy)
            elif line.startswith('||'):
                domain = line[2:]
                if 'host.indexOf(".%s") >= 0' % domain in jsLines[-1] or 'host.indexOf("%s") >= 0' % domain in jsLines[-1]:
                    jsLines.pop()
                jsLine = 'if (host == "%s" || dnsDomainIs(host, ".%s")) return "%s";' % (domain, domain, return_proxy)
            elif line.startswith('|'):
                jsLine = 'if (url.indexOf("%s") == 0) return "%s";' % (line[1:], return_proxy)
            elif '*' in line:
                jsLine = 'if (shExpMatch(url, "*%s*")) return "%s";' % (line.strip('*'), return_proxy)
            elif '/' not in line:
                jsLine = 'if (host.indexOf("%s") >= 0) return "%s";' % (line, return_proxy)
            else:
                jsLine = 'if (url.indexOf("%s") >= 0) return "%s";' % (line, return_proxy)
            jsLine = ' ' * indent + jsLine
            if use_proxy:
                jsLines.append(jsLine)
            else:
                jsLines.insert(0, jsLine)
    function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsLines), ' '*indent, default)
    return function


def urlfilter2pac(content, func_name='FindProxyForURLByUrlfiter', proxy='127.0.0.1:8086', default='DIRECT', indent=4):
    """urlfilter.ini to Pac, based on https://github.com/iamamac/autoproxy2pac"""
    jsCode = []
    for line in content[content.index('[exclude]'):].splitlines()[1:]:
        if line and not line.startswith(';'):
            use_proxy = True
            if line.startswith("@@"):
                line = line[2:]
                use_proxy = False
            return_proxy = 'PROXY %s' % proxy if use_proxy else default
            jsLine = 'if(shExpMatch(url, "%s")) return "%s";' % (line, return_proxy)
            jsLine = ' ' * indent + jsLine
            if use_proxy:
                jsCode.append(jsLine)
            else:
                jsCode.insert(0, jsLine)
    function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsCode), ' '*indent, default)
    return function


def make_cacheable(func):
    from bae.api.memcache import BaeMemcache
    cache = BaeMemcache()
    def wrap(*args, **kwargs):
        key = '%s %s' % (','.join(str(x) for x in args), ','.join('%s:%s' % (k, v) for k, v in kwargs.items()))
        value = cache.get(key)
        if value is None:
            value = func(*args, **kwargs)
            cache.set(key, value, 86400)
        return value
    return wrap


#@make_cacheable
def generate_pac(urlfilter_url, autoproxy_url, urlfilter_proxy, autoproxy_proxy, default_proxy='DIRECT'):
    TEMPLATE = '''
function FindProxyForURL(url, host) {
    var proxy = FindProxyForURLByUrlfiter(url, host);
    if (proxy != 'DIRECT')
    {
        return proxy;
    } else {
        return FindProxyForURLByAutoProxy(url, host);
    }
}

$urlfilter_func

$autoproxy_func
'''
    urlfilter_func = ''
    autoproxy_func = ''
    opener = urllib2.build_opener()
    try:
        logging.info('try download %r to generate_pac', urlfilter_url)
        urlfilter_content = opener.open(urlfilter_url).read()
        logging.info('%r downloaded, try convert it with urlfilter2pac', urlfilter_url)
        if 'gevent' in sys.modules and time.sleep is getattr(sys.modules['gevent'], 'sleep', None) and hasattr(gevent.get_hub(), 'threadpool'):
            urlfilter_func = gevent.get_hub().threadpool.apply(urlfilter2pac, (urlfilter_content, 'FindProxyForURLByUrlfiter', urlfilter_proxy))
        else:
            urlfilter_func = urlfilter2pac(urlfilter_content, 'FindProxyForURLByUrlfiter', urlfilter_proxy)
        logging.info('%r downloaded and parsed', urlfilter_url)
    except Exception as e:
        logging.exception('generate_pac failed: %r', e)
    try:
        logging.info('try download %r to generate_pac', autoproxy_url)
        autoproxy_content = base64.b64decode(opener.open(autoproxy_url).read())
        logging.info('%r downloaded, try convert it with autoproxy2pac', autoproxy_url)
        if 'gevent' in sys.modules and time.sleep is getattr(sys.modules['gevent'], 'sleep', None) and hasattr(gevent.get_hub(), 'threadpool'):
            autoproxy_func = gevent.get_hub().threadpool.apply(autoproxy2pac, (autoproxy_content, 'FindProxyForURLByAutoProxy', autoproxy_proxy))
        else:
            autoproxy_func = autoproxy2pac(autoproxy_content, 'FindProxyForURLByAutoProxy', autoproxy_proxy)
        logging.info('%r downloaded and parsed', autoproxy_url)
    except Exception as e:
        logging.exception('generate_pac failed: %r', e)
    return string.Template(TEMPLATE).substitute(urlfilter_func=urlfilter_func, autoproxy_func=autoproxy_func)


def app(environ, start_response):
    try:
        path_info = environ['PATH_INFO']
        urlfilter_proxy, autoproxy_proxy, filename = path_info.strip('/').split('/')
        pac = generate_pac(URLFILTER_URL, AUTOPROXY_URL, urlfilter_proxy, autoproxy_proxy)
        start_response('200 OK', [])
        return [pac]
    except Exception as e:
        start_response('500 Internal Server Error', [])
        return [repr(e)]

application = app


if __name__ == '__main__':
    import gevent.wsgi
    server = gevent.wsgi.WSGIServer(('', 80), app)
    server.serve_forever()
