"""Log state changes at home HVAC"""

import json
import machine
import network
import sys
import time
import aiohttp
import uasyncio
from hardware_rp2 import MyRTC, machine_soft_reset, PicoLED
import onewire
import ds18x20
from secret.wifi import SECRET_WIFI_SSID, SECRET_WIFI_PASSWORD
from secret.couchdb import (
    SECRET_COUCHDB_HOSTNAME,
    SECRET_COUCHDB_SERVICE_PORT,
    SECRET_COUCHDB_PROTOCOL,
    SECRET_COUCHDB_DBNAME,
)


# Hardware configuration (RP2040, Raspberry Pi Pico W)
_ADC_PANEL_TEMP: int = None  # GPIO to configure as ADC

_GPIO_1W: int = 22  # GPIO to configure for 1W protocol
_1W_TEMP_SENSORS = {
    "outdoor": bytes(b"(\x87\x8bX\x12\x19\x01\x0b"),
    "discharge": None,
    "return": None,
    "ambient": None,
}

_GPIO_HEAT: int = None  # GPIO to read for HEAT digital input
_GPIO_COOL: int = None
_GPIO_FAN: int = None
_GPIO_PURGE: int = None
_GPIO_EMERGENCY: int = None
_GPIO_ZONE1: int = None
_GPIO_ZONE2: int = None
_GPIO_ZONE3: int = None
_GPIO_ZONE4: int = None

# "Fri, 12 Jan 2024 12:51:40 GMT" without benefit of re.X or ?P<name>
_COUCHDB_DATE_RE = r"\s*(\w+),\s+(\d+)\s+(\w+)\s+(\d+)\s+(\d+)\:(\d+)\:(\d+)\s+(\w+)"


class HvacState:
    """State of HVAC system at a moment."""

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
        """Class setup, allowing for testing."""
        self._digitals: int | None = fakeDigitals
        self._panelTempC: float | None = fakeTemps
        self._outdoorTempC: float | None = fakeTemps
        self._dischargeTempC: float | None = fakeTemps
        self._returnTempC: float | None = fakeTemps

    def as_dict(self) -> dict:
        """Return self as a dict for JSON conversion."""
        try:
            digs = self.digitals
            retval = {
                "digitals": digs,
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
        except:
            retval = {}
        try:
            retval["panelTempC"] = self.panelTempC
        except:
            pass
        try:
            retval["outdoorTempC"] = self.outdoorTempC
        except:
            pass
        try:
            retval["dischargeTempC"] = self.dischargeTempC
        except:
            pass
        try:
            retval["returnTempC"] = self.returnTempC
        except:
            pass
        return retval

    def difference_is_reportable(self, other):
        """Heuristics for whether two states are different enough to record."""

        if other is None:
            return True

        # Bypass @property accessors so uninitialized values can be compared
        if self._digitals != other._digitals:
            return True
        if other._panelTempC is not None and abs(self._panelTempC - other._panelTempC) > 2:
            return True
        if other._outdoorTempC is not None and abs(self._outdoorTempC - other._outdoorTempC) > 1:
            return True
        if other._dischargeTempC is not None and abs(self._dischargeTempC - other._dischargeTempC) > 1:
            return True
        if other._returnTempC is not None and abs(self._returnTempC - other._returnTempC) > 1:
            return True
        return False

    @property
    def digitals(self) -> int:
        if self._digitals is None:
            raise RuntimeError("uninitialized digitals value accessed")
        return self._digitals

    @digitals.setter
    def digitals(self, value: int) -> None:
        self._outdoorTempC = value

    @property
    def panelTempC(self) -> float:
        if self._panelTemp is None:
            raise RuntimeError("uninitialized temperature accessed")
        return self._panelTemp

    @panelTempC.setter
    def panelTempC(self, value: int) -> None:
        self._panelTempC = value

    @property
    def outdoorTempC(self) -> float:
        if self._outdoorTempC is None:
            raise RuntimeError("uninitialized temperature accessed")
        return self._outdoorTempC

    @outdoorTempC.setter
    def outdoorTempC(self, value: int) -> None:
        self._outdoorTempC = value

    @property
    def dischargeTempC(self) -> float:
        if self._dischargeTempC is None:
            raise RuntimeError("uninitialized temperature accessed")
        return self._dischargeTempC

    @dischargeTempC.setter
    def dischargeTempC(self, value: int) -> None:
        self._dischargeTempC = value

    @property
    def returnTempC(self) -> float:
        if self._returnTempC is None:
            raise RuntimeError("uninitialized temperature accessed")
        return self._returnTempC

    @returnTempC.setter
    def returnTempC(self, value: int) -> None:
        self._returnTempC = value

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
    """Read hardware IO into HvacState."""

    def __init__(self):
        """Do basic hardware setup."""
        self._1w = onewire.OneWire(machine.Pin(_GPIO_1W))
        self._ds18x20 = ds18x20.DS18X20(self._1w)
        self._temp_sensors = []

    def sync_scan_1w(self):
        """Do one-wire system setup."""
        lookup_address = {}
        for key, val in _1W_TEMP_SENSORS.items():
            if val:
                lookup_address[val] = key
        try:
            scan_result = self._ds18x20.scan()
            self._ds18x20.convert_temp()
        except onewire.OneWireError as e:
            print(e)
            raise
        time.sleep(0.750)  # worst case, default resolution
        print(f"1W scan for DS18x20 temp sensors got {len(scan_result)}:")
        for ds in scan_result:
            try:
                t = self._ds18x20.read_temp(ds)
            except onewire.OneWireError as e:
                print(e)
                raise
            found = lookup_address.get(bytes(ds), None)
            print(f'  {ds} ({len(ds)}): {t} ({found if found else "not configured"})')
            if found:
                self._temp_sensors.append((ds, found if found else ds.decode("utf-8")))

    def read_io_state(self, state) -> None:
        """Commit current state to HvacState object. Note (async) delay for temp conversions."""
        self._ds18x20.convert_temp()
        state = HvacState()
        uasyncio.sleep(
            0.750
        )  # worst case conversion time, revisit (don't need 1/16C resolution)
        for ds, found in self._temp_sensors:
            if found == "outdoor":
                state.outdoorTempC = self._ds18x20.read_temp(ds)
            elif found == "discharge":
                state.dischargeTempC = self._ds18x20.read_temp(ds)
            elif found == "return":
                state.outdoorTempC = self._ds18x20.read_temp(ds)
            elif found == "ambient":
                state.outdoorTempC = self._ds18x20.read_temp(ds)
            else:
                pass  # other temps on same network for other uses?


async def async_run(from_setup):
    led, put_target_url, hvac_reader = from_setup

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    rtc = MyRTC(_COUCHDB_DATE_RE)

    # Once per run: set clock
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.request("GET", put_target_url) as response:
            jbody = await response.json()
            if response.status == 200:
                http_date = response.headers.get("Date")
                rtc.set_from_http_date(http_date)
            else:
                raise RuntimeError("clock retrieve failed: {response.status} {response.headers} {jbody}")

        last_sent_state = None
        last_post_time = None
        last_post_time_matches = 0  # for >1 post per second, just in case
        while True:
            state = HvacState()  # empty
            hvac_reader.read_io_state(state)  # fill the empty HvacState
            pydt_now = rtc.now()
            if state.difference_is_reportable(last_sent_state) or rtc.was_seconds_ago(
                pydt_now, last_post_time, 3600
            ):
                last_sent_state = state
                body = state.as_dict()
                post_time = pydt_now
                if post_time == last_post_time:
                    last_post_time_matches += 1
                    post_time += f".{last_post_time_matches}"
                else:
                    last_post_time = post_time
                    last_post_time_matches = 0
                    body["_id"] = rtc.pydt_tuple_as_iso(post_time)
                async with session.request(
                    "POST", put_target_url, data=json.dumps(body)
                ) as response:
                    if response.status != 200 and response.status != 201:
                        rb = await response.json()
                        raise RuntimeError(
                            f"Failed to post state, {response.status}: {rb}"
                        )
                    rtc.set_from_http_date(response.headers.get("Date"))
                    await led.async_blink(1, 100, 100)
                    await uasyncio.sleep(0.1)  # blink skips last off-wait
            await led.async_blink(1, 100, 100)
            await uasyncio.sleep(5)


def sync_setup(led):
    SECRET_COUCHDB_HOSTNAME = "192.168.2.246"  # until we can do mdns resolves
    url = f"{SECRET_COUCHDB_PROTOCOL}://{SECRET_COUCHDB_HOSTNAME}:{SECRET_COUCHDB_SERVICE_PORT}/{SECRET_COUCHDB_DBNAME}"

    network.hostname(open("/etc/hostname", "r").read())
    wlan = network.WLAN(network.STA_IF)
    print(f"wlan:{wlan}")
    if not wlan.isconnected():
        wlan.active(True)
        nets = wlan.scan()
        print(f"wlan.scan found {len(nets)} networks:")
        for net in nets:
            print(f"  net:{net}")
        wlan.connect(SECRET_WIFI_SSID, SECRET_WIFI_PASSWORD)
    print("wlan connected")

    hvac_reader = HvacReader()
    hvac_reader.sync_scan_1w()
    return (led, url, hvac_reader)


if __name__ == "__main__":
    # Setup, and setup errors
    led = PicoLED()
    try:
        with open("/hvac.log", "a") as f:
            f.write(f"\n\n============== Starting: {time.gmtime()} ==============\n")
        runtime_cfg = sync_setup(led)
    except Exception as e:
        led.sync_blink(3, 400, 400)
        sys.print_exception(e)
        with open("/hvac.log", "a") as f:
            sys.print_exception(e, f)
        machine_soft_reset()

    # Runtime, and runtime errors
    try:
        with open("/hvac.log", "a") as f:
            f.write(f"============== Running: {time.gmtime()} ==============\n")
        uasyncio.run(async_run(runtime_cfg))
    except Exception as e:
        sys.print_exception(e)
        with open("/hvac.log", "a") as f:
            f.write(f"----------- {time.gmtime()} -----------\n")
            sys.print_exception(e, f)
        led.sync_blink(3, 100, 100)
        machine_soft_reset()
