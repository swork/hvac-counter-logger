import main
import sys
import time
import unittest


class FakeResponse:
    def __init__(self):
        self.headers = {"Date": "Fri, 12 Jan 2024 20:51:40 GMT"}


class TestClock(unittest.TestCase):
    def test_sync(self):
        rtc = main.MyRTC(main._COUCHDB_DATE_RE)
        rtc.sync_rtc_to_http(FakeResponse())
        t = rtc.now_tuple()
        self.assertEqual(t[0], 2024)
        self.assertEqual(t[1], 1)
        self.assertEqual(t[2], 12)
        self.assertEqual(t[3], 4)
        self.assertEqual(t[4], 20)
        self.assertEqual(t[5], 51)
        self.assertIn(t[6], [40, 41])
        time.sleep(2)
        t = rtc.now_tuple()
        self.assertTrue(t[6] > 41)


unittest.main(module=sys.modules[__name__])
