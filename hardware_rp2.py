import asyncio
import machine
import re
import time
from datetime import datetime, timezone

struct_time = tuple  # not available as time.struct_time


class MyRTC:
    """Abstract away the RP2040 machine.RTC implementation.
    Please don't expose a tuple representing date/time in Python that's
    different from the Python time.struct_time. It's quite confusing
    and burns up RAM tracking the differences.
    """

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
        "Dec": 12,
    }

    def __init__(self, http_date_re):
        self._rtc = machine.RTC()
        self._http_date_re = http_date_re

    @classmethod
    def pydttuple_from_upyrp2040tuple(cls, upyrp2040tuple: tuple) -> struct_time:
        t = struct_time(
            (
                upyrp2040tuple[0],  # year
                upyrp2040tuple[1],  # mon
                upyrp2040tuple[2],  # day
                upyrp2040tuple[4],  # hour
                upyrp2040tuple[5],  # min
                upyrp2040tuple[6],  # sec
                0,  # isdst
                "GMT",  # zone
                0,  # offset
            )
        )
        return t

    @classmethod
    def upyrp2040tuple_from_pydttuple(cls, p: struct_time) -> tuple:
        return (
            p[0],  # year
            p[1],  # mon
            p[2],  # day
            p[7],  # dow
            p[3],  # hour
            p[4],  # min
            p[5],  # sec
            0      # subsec
        )

    def timestamp(self) -> int:
        t = self.pydttuple_from_upyrp2040tuple(self._rtc.datetime())
        return self.timestamp_from_pydttuple(t)

    @classmethod
    def was_seconds_ago(
        cls, newer: struct_time, older: struct_time, seconds: int
    ) -> bool:
        return time.mktime(newer) - time.mktime(older) >= seconds

    @classmethod
    def pydt_tuple_as_iso(cls, t: struct_time) -> str:
        return time.strftime("%Y-%b-%dT%H:%M:%S", t)

    def now(self) -> struct_time:
        return self.pydttuple_from_upyrp2040tuple(self._rtc.datetime())

    def now_iso(self) -> str:
        pydt_tuple = self.now()
        return self.pydt_tuple_as_iso(pydt_tuple)

    def set_from_http_date(self, http_date: str) -> None:
        """Set machine.RTC.datetime from an HTTP response header.
        RP2040 RTC uses a different tuple layout than Python.
        We try to store only the Python tuple format.
        """
        mo = re.search(self._http_date_re, http_date)
        if not mo:
            raise RuntimeError(f"HTTP Date header format unrecognized {http_date}")
        g = mo.group
        try:
            # g(0) is entire match; g(1) is day of week; g(2) is day of month
            pydt_tuple_parsed = struct_time(
                (
                    int(g(4)),  # year
                    self.MON[g(3)],  # mon
                    int(g(2)),  # day
                    int(g(5)),  # hour
                    int(g(6)),  # min
                    int(g(7)),  # sec
                    0,  # isdst
                    g(8),  # zone, acting on expectation "GMT"
                    0,  # offset
                )
            )
        except KeyError:
            print(f"HTTP Date problem, maybe unknown month name? {http_date}")
            raise
        pydt_tuple_now = self.pydttuple_from_upyrp2040tuple(self._rtc.datetime())
        if (
            pydt_tuple_now[0] < 2024
            or abs(pydt_tuple_parsed[5] - pydt_tuple_now[5]) > 2
        ):  # old, or 2s different; hits on minute rollover too
            try:
                self._rtc.datetime(
                    self.upyrp2040tuple_from_pydttuple(pydt_tuple_parsed)
                )
            except:
                print(f"trouble setting RTC from converted pydt {pydt_tuple_parsed}")
                print(f" ... which gave upy {self.upyrp2040tuple_from_pydttuple(pydt_tuple_parsed)}")
                raise


class PicoLED:
    def __init__(self, pin="LED"):
        self._led = machine.Pin(pin, machine.Pin.OUT)

    def sync_blink(self, blinks, blink_on_ms, blink_off_ms):
        self._led.off()
        for i in range(0, blinks):
            if i > 0:
                time.sleep(blink_off_ms / 1000)
            self._led.on()
            time.sleep(blink_on_ms / 1000)
            self._led.off()

    async def async_blink(self, blinks, blink_on_ms, blink_off_ms):
        self._led.off()
        for i in range(0, blinks):
            if i > 0:
                await asyncio.sleep(blink_off_ms / 1000)
            self._led.on()
            await asyncio.sleep(blink_on_ms / 1000)
            self._led.off()


def machine_soft_reset():
    machine.soft_reset()
