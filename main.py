import json
import network
import os
import sys
import time
from async_urequests import request
import uasyncio
from hardware_rp2 import MyRTC, blink_led, machine_soft_reset, report_error

# Hardware configuration (RP2040, Raspberry Pi Pico W)
_ADC_PANEL_TEMP: int = None  # GPIO to configure as ADC

_GPIO_1W: int = None  # GPIO to configure for 1W protocol
_1W_DISCHARGE_TEMP: int = None  # 1W address of discharge temp sensor
_1W_RETURN_TEMP: int = None  # 1W address of return temp sensor

_GPIO_HEAT: int = None  # GPIO to read for HEAT digital input
_GPIO_COOL: int = None
_GPIO_FAN: int = None
_GPIO_PURGE: int = None
_GPIO_EMERGENCY: int = None
_GPIO_ZONE1: int = None
_GPIO_ZONE2: int = None
_GPIO_ZONE3: int = None
_GPIO_ZONE4: int = None

# Environment configuration
_404HB_WIFI_SSID = 'FourOhFour'
_404HB_WIFI_PASSWORD = 'happiness'
_404HB_COUCHDB_HOSTNAME = '192.168.2.246'
_404HB_COUCHDB_SERVICE_PORT = '5984'
_404HB_COUCHDB_PROTOCOL = 'http'
_404HB_COUCHDB_DBNAME = 'hvac'

# "Fri, 12 Jan 2024 12:51:40 GMT"
_COUCHDB_DATE_RE = r'\s*(\w+),\s+(\d+)\s+(\w+)\s+(\d+)\s+(\d+)\:(\d+)\:(\d+)\s+(\w+)'


async def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(_404HB_WIFI_SSID, _404HB_WIFI_PASSWORD)
    for i in range(0, 10):
        if wlan.isconnected():
            break
        await uasyncio.sleep(1)
    if i == 10:
        print("Timed out connecting  to WIFI, we'll fail later.")
        return None
    else:
        cfg = wlan.ifconfig()
        print(cfg)
        return cfg[0]  # IP


class HvacState:
    """State of 404HB heat pump at a moment"""

    _DIG_HEAT: int = 1 << 0  # Bitmask against .digitals for HEAT call value
    _DIG_COOL: int = 1 << 1
    _DIG_FAN: int = 1 << 2
    _DIG_PURGE: int = 1 << 3
    _DIG_EMERGENCY: int = 1 << 4
    _DIG_ZONE1: int = 1 << 5
    _DIG_ZONE2: int = 1 << 6
    _DIG_ZONE3: int = 1 << 7
    _DIG_ZONE4: int = 1 << 8

    def __init__(self, fakeDigitals=None, fakeTemps=None) -> None:
        self._digitals: int | None = fakeDigitals
        self._panelTempC: float | None = fakeTemps
        self._iomTempC: float | None = fakeTemps
        self._dischargeTempC: float | None = fakeTemps
        self._returnTempC: float | None = fakeTemps

    @property
    def as_dict(self) -> dict:
        return {
            "digitals": self.digitals,
            "heat": self.heat,
            "cool": self.cool,
            "fan": self.fan,
            "purge": self.purge,
            "emergency": self.emergency,
            "zone1": self.zone1,
            "zone2": self.zone2,
            "zone3": self.zone3,
            "zone4": self.zone4,
        }

    @property
    def digitals(self) -> int:
        if self._digitals is None:
            raise RuntimeError
        return self._digitals

    @property
    def panelTempC(self) -> float:
        if self._panelTemp is None:
            raise RuntimeError
        return self._panelTemp

    @property
    def iomTempC(self) -> float:
        if self._iomTemp is None:
            raise RuntimeError
        return self._iomTemp

    @property
    def dischargeTempC(self) -> float:
        if self._dischargeTempC is None:
            raise RuntimeError
        return self._dischargeTempC

    @property
    def returnTempC(self) -> float:
        if self._returnTempC is None:
            raise RuntimeError
        return self._returnTempC

    @property
    def heat(self) -> bool:
        return self._digitals & self._DIG_HEAT != 0

    @property
    def cool(self) -> bool:
        return self._digitals & self._DIG_COOL != 0

    @property
    def fan(self) -> bool:
        return self._digitals & self._DIG_FAN != 0

    @property
    def purge(self) -> bool:
        return self._digitals & self._DIG_PURGE != 0

    @property
    def emergency(self) -> bool:
        return self._digitals & self._DIG_EMERGENCY != 0

    @property
    def zone1(self) -> bool:
        return self._digitals & self._DIG_ZONE1 != 0

    @property
    def zone2(self) -> bool:
        return self._digitals & self._DIG_ZONE2 != 0

    @property
    def zone3(self) -> bool:
        return self._digitals & self._DIG_ZONE3 != 0

    @property
    def zone4(self) -> bool:
        return self._digitals & self._DIG_ZONE4 != 0


class HvacReader:
    def __init__(self):
        pass

    def read_io_state(self) -> HvacState:
        return HvacState(fakeDigitals=0, fakeTemps=20.0)


def iso_date_time(couchdb_date_string):
    parsed_time = datetime.strptime(couchdb_date_string, _COUCHDB_STRPTIME_FORMAT)
    return parsed_time.strftime('%Y-%m-%dT%H:%M:%S')



async def main(put_target_url):
    ip = await wifi_connect()
    print(f'My ip:{ip}')

    headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    rtc = MyRTC(_COUCHDB_DATE_RE)

    # Once per run: set clock
    print(put_target_url)
    response = await request("GET", put_target_url, headers=headers)
    if response.status_code == 200:
        rtc.set_from_http(response)
        print(f'set clock: {rtc.now_iso()}')
    else:
        body = await response.json()
        raise RuntimeError('clock retrieve failed: {body}')

    hvac = HvacReader()
    old_digitals = None
    last_post_time = None
    last_post_time_matches = 0
    while True:
        print('loop top')
        state = hvac.read_io_state()
        if state.digitals != old_digitals:
            old_digitals = state.digitals
            body = state.as_dict
            post_time = rtc.now_iso()
            if post_time == last_post_time:
                last_post_time_matches += 1
                post_time += f'.{last_post_time_matches}'
            else:
                last_post_time = post_time
                last_post_time_matches = 0
            body["_id"] = post_time
            response = await request("POST",
                                     put_target_url,
                                     headers=headers,
                                     data=json.dumps(body))
            if response.status_code != 200 and response.status_code != 201:
                rb = await response.json()
                raise RuntimeError(f"Failed to post state, {response.status_code}: {rb}")
            print(f'response: {response} body:{await response.json()}')
            rtc.set_from_http(response)
            blink_led(1, 100, 100)
            await uasyncio.sleep(0.05)
        blink_led(1, 100, 100)
        print('sleep 5')
        await uasyncio.sleep(4.9)


if __name__ == '__main__':
    try:
        url = f"{_404HB_COUCHDB_PROTOCOL}://{_404HB_COUCHDB_HOSTNAME}:{_404HB_COUCHDB_SERVICE_PORT}/{_404HB_COUCHDB_DBNAME}"
        uasyncio.run(main(url))
    except Exception as e:
        sys.print_exception(e)
        with open("/hvac.log", "a") as f:
            f.write(f'\n\n=========== {time.gmtime()} ==============\n')
            sys.print_exception(e, f)
        blink_led(3, 100, 100)
        machine_soft_reset()


