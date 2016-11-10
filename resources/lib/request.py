# =============================================================================
# lib/request.py
#
# Copyright (c) 2009 Frank Smit (FSX)
# License: GPL v3, see the COPYING file for details
# =============================================================================
  
import urllib
import httplib
import base64
#import xbmc #For Logging
  
class Request():
  
    def __init__(self, config):
        self.username, self.password, self.host, self.user_agent = config
  
    def set_auth():
        pass
  
    def retrieve(self, url):
  
        try:
            filename, headers = urllib.urlretrieve(url)
        except urllib.ContentTooShortError:
            return False
        else:
            return (filename, headers)
  
    def execute(self, path, params=None, method='GET', authenticate=False, ssl=False):
  
        headers = {'User-Agent': self.user_agent}
  
        if method == 'POST' or method == 'PUT':
            headers['Content-type'] = 'application/x-www-form-urlencoded'
  
        if params is not None:
            params = urllib.urlencode(params)
  
        if authenticate:
            encoded = base64.encodestring('%s:%s' % (self.username, self.password))[:-1]
            headers['Authorization'] = 'Basic %s' % encoded
  
        if ssl:
            connection = httplib.HTTPSConnection(self.host)
        else:
            connection = httplib.HTTPConnection(self.host)
  
        try:
            request = connection.request(method.upper(), '/' + path, params, headers)
            response = connection.getresponse()
            response_content = response.read()
  
            # Raise an exception if the status code is something else then 200
            # and print the status code and response.
            if response.status != httplib.OK and response.status != httplib.CREATED:
  
                #print response.status
                #print response_content

#		xbmc.log("### [%s] - %s" % ("XBMAL",str(response.status) + ": " + str(response_content)), level=xbmc.LOGERROR)
  
                connection.close()
                raise HttpStatusError()
  
            connection.close()
  
            return response_content
        except:
            print "Request Error."
            raise HttpRequestError()
  
# Request Exceptions
  
class HttpRequestError(Exception):
    pass
  
class HttpStatusError(Exception):
    pass
