import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from clerk_auth import AuthenticationError, MembershipError
from clerk_dashboard_api import handler


class HTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.gateway=Mock(origins={'https://dashboard.example.com'})
        self.gateway.dispatch.return_value={'rows':[]}
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.gateway))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)

    def tearDown(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,method='POST',path='/api/dashboard',body='{}',headers=None):
        self.client.request(method,path,body,headers or {'Origin':'https://dashboard.example.com','Content-Type':'application/json','Authorization':'Bearer test'})
        response=self.client.getresponse();result=(response.status,dict(response.getheaders()),response.read());return result

    def test_cors_only_allows_explicit_origin(self):
        status,headers,_=self.request('OPTIONS',body='',headers={'Origin':'https://dashboard.example.com'})
        self.assertEqual(status,204);self.assertEqual(headers['Access-Control-Allow-Origin'],'https://dashboard.example.com')
        status,headers,_=self.request('OPTIONS',body='',headers={'Origin':'https://attacker.example'})
        self.assertEqual(status,403);self.assertNotIn('Access-Control-Allow-Origin',headers);self.gateway.dispatch.assert_not_called()

    def test_only_bounded_json_endpoint_is_exposed(self):
        for method,path,body,headers,expected in [
            ('GET','/api/dashboard','',None,405),('POST','/anything','{}',None,404),
            ('POST','/api/dashboard','broken',None,400),
            ('POST','/api/dashboard','{}',{'Content-Type':'text/plain'},415),
            ('POST','/api/dashboard','',{'Content-Type':'application/json','Content-Length':'2100001'},413),
        ]:
            with self.subTest(expected=expected):self.assertEqual(self.request(method,path,body,headers)[0],expected)
        self.gateway.dispatch.assert_not_called()

    def test_authentication_and_membership_failures_are_distinct_and_private(self):
        for error,status in [(AuthenticationError('secret-token'),401),(MembershipError('private-id'),403)]:
            self.gateway.dispatch.side_effect=error
            actual,headers,body=self.request();self.assertEqual(actual,status)
            self.assertNotIn(b'secret-token',body);self.assertNotIn(b'private-id',body)
            self.assertEqual(headers['Cache-Control'],'no-store')

    def test_request_passes_identity_header_and_payload_without_cookies(self):
        status,_,body=self.request(body='{"operation":"/api/logs"}')
        self.assertEqual(status,200);self.assertEqual(json.loads(body),{'rows':[]})
        self.gateway.dispatch.assert_called_once_with('Bearer test','https://dashboard.example.com',{'operation':'/api/logs'})


if __name__=='__main__':unittest.main()
