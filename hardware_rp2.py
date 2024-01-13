import machine
import re
import time
from datetime import datetime, timezone

class MyRTC:
    MON = {
        "Jan": 1,
        "Feb": 2,
        "Mar": 3,
        "Apr": 4,
        "May": 5,
        "Jun": 6,
        "Jul": 7,
        "Aug": 8,
        "Sep": 9,
        "Oct": 10,
        "Nov": 11,
        "Dec": 12
    }

    def __init__(self, http_date_re):
        self._rtc = machine.RTC()
        self._http_date_re = http_date_re

    def timestamp(self):
        t = self._rtc.datetime()
        return int(datetime(*t[0:7], timezone.utc).timestamp())

    def now_tuple_for_upy_strftime(self):
        t = self._rtc.datetime()

        # upy components can't agree on datetime tuple fields. Sheesh.
        return (t[0], t[1], t[2], t[4], t[5], t[6], 0, 0, 0, None)

    def now_iso(self):
        return time.strftime("%Y-%b-%dT%H:%M:%S", self.now_tuple_for_upy_strftime())

    def set_from_http(self, aiohttp_client_response):
        response_time = aiohttp_client_response.headers['Date']
        mo = re.search(self._http_date_re, response_time)
        if not mo:
            raise RuntimeError(f'HTTP Date header format unrecognized {aiohttp_client_response.headers}')
        g = mo.group
        try:
            # g(0) is entire match; g(1) is day of week; g(2) is day of month
            parsed_time = (int(g(4)), self.MON[g(3)], int(g(2)), 0, int(g(5)), int(g(6)), int(g(7)), 0)
        except KeyError:
            print(f'HTTP Date problem, maybe unknown month name? {aiohttp_client_response.headers}')
            raise
        n = self.now_tuple_for_upy_strftime()
        if n[0] < 2024 or abs(parsed_time[6] - n[6]) > 2:  # old, or 2s different; hits on minute rollover too
            self._rtc.datetime(parsed_time)


def blink_led(blinks, blink_on_ms, blink_off_ms):
    led = machine.Pin("LED", machine.Pin.OUT)
    led.off()
    for i in range(0, blinks):
        if i > 0:
            time.sleep(blink_off_ms / 1000)
        led.on()
        time.sleep(blink_on_ms / 1000)
        led.off()


def machine_soft_reset():
    machine.soft_reset()


def report_error(e):
    # incomplete traceback dependency implementation on rp2
    # incomplete machine.RTC implementation on rp2, sheesh
    print(repr(e))
    with open("hvac.log", "a") as logfile:
        logfile.write(f'\n\n=========== {MyRTC().now_iso()} ==============\n')
        logfile.write(repr(e))
