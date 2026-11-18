import json
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import server

class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_health_status_reports_dependency(self):
        try:
            with urlopen(self.base + '/health') as response:
                content = json.load(response)
        except HTTPError as exc:
            self.assertEqual(exc.code, 503)
            content = json.load(exc)
        self.assertEqual(content['eccodes'], server.ec is not None)

    def test_unknown_endpoint_is_not_exposed(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.base + '/other')
        self.assertEqual(raised.exception.code, 404)

    def test_generate_requires_shared_secret(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.base + '/api/timezero-grib?lat=-27&lon=-48&radius=30&hours=24')
        self.assertEqual(raised.exception.code, 401)

if __name__ == '__main__':
    unittest.main()
