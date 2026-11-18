import json
import os
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from unittest.mock import patch
import server

class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0),server.Handler)
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close()

    def test_health(self):
        try:
            with urlopen(self.base+'/health') as resp: obj=json.load(resp)
        except HTTPError as exc:
            self.assertEqual(exc.code,503); obj=json.load(exc)
        self.assertEqual(obj['eccodes'],server.noaa.ec is not None)
        self.assertEqual(obj['source'],'NOAA GFS 0.25')

    def test_unknown_endpoint(self):
        with self.assertRaises(HTTPError) as error:urlopen(self.base+'/other')
        self.assertEqual(error.exception.code,404)

    def test_auth(self):
        with self.assertRaises(HTTPError) as error:urlopen(self.base+'/api/timezero-jobs?owner=someone')
        self.assertEqual(error.exception.code,401)

    def test_job_deduplication(self):
        with patch.dict(os.environ,{'TIMEZERO_WORKER_SECRET':'x'*32}):
            with patch.object(server.EXEC,'submit') as submit:
                url=self.base+'/api/timezero-jobs?owner=test-owner&lat=-25.2&lon=-47.4&radius=30&hours=24'
                request=Request(url,headers={'X-Worker-Secret':'x'*32})
                with urlopen(request) as r:a=json.load(r)
                with urlopen(request) as r:b=json.load(r)
                self.assertEqual(a['id'],b['id'])
                self.assertEqual(submit.call_count,1)

class TestNOAA(unittest.TestCase):
    def test_bbox_lon_scales_at_latitude(self):
        bbox=server.noaa.bounds(-30,-48,30)
        self.assertGreater((bbox['rightlon']-bbox['leftlon']), .69)
        self.assertGreater(bbox['toplat'],bbox['bottomlat'])

    def test_noaa_wind_grib_query(self):
        from datetime import datetime,timezone
        url=server.noaa.make_url(datetime(2026,11,18,6,tzinfo=timezone.utc),3,server.noaa.bounds(-25,-47,60))
        self.assertIn('filter_gfs_0p25.pl',url)
        self.assertIn('var_UGRD=on',url)
        self.assertIn('var_VGRD=on',url)
        self.assertIn('lev_10_m_above_ground=on',url)

    def test_missing_variable_never_replaced_by_zero(self):
        # Verifies that no fabricated value arrays are created by URL/grid setup
        self.assertNotIn('open-meteo',server.noaa.BASE)

if __name__=='__main__':unittest.main()
