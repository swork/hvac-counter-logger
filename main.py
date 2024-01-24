"""Log state changes at home HVAC"""

import aiohttp
import gc
import json
import machine
import network
import sys
import time
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
_GPIO_1W: int = 22  # Configure GPIO22 for 1W protocol
_1W_TEMP_SENSORS = {
    "outdoor": bytes(b"(\x87\x8bX\x12\x19\x01\x0b"),
    "discharge": None,
    "return": None,
}

_GPIO_HEAT: int = 1  # Read GPIO1 for HEAT digital input
_GPIO_COOL: int = 2
_GPIO_FAN: int = 3
_GPIO_PURGE: int = 4
_GPIO_EMERGENCY: int = 5
_GPIO_ZONE1: int = 6
_GPIO_ZONE2: int = 7
_GPIO_ZONE3: int = 8
_GPIO_ZONE4: int = 9

# "Fri, 12 Jan 2024 12:51:40 GMT" without benefit of re.X or ?P<name>
_COUCHDB_DATE_RE = r"\s*(\w+),\s+(\d+)\s+(\w+)\s+(\d+)\s+(\d+)\:(\d+)\:(\d+)\s+(\w+)"


class HvacState:
    """State of HVAC system at a moment."""

    DIG_HEAT: int = 0  # Bit of .digitals for HEAT call value
    DIG_COOL: int = 1
    DIG_FAN: int = 2
    DIG_PURGE: int = 3
    DIG_EMERGENCY: int = 4
    DIG_ZONE1: int = 5
    DIG_ZONE2: int = 6
    DIG_ZONE3: int = 7
    DIG_ZONE4: int = 8

    def __init__(self, fakeDigitals=None, fakeTemps=None) -> None:
        """Class setup, allowing for testing."""
        self._digitals: int | None = fakeDigitals
        self._ambientTempC: float | None = fakeTemps
        self._outdoorTempC: float | None = fakeTemps
        self._dischargeTempC: float | None = fakeTemps
        self._returnTempC: float | None = fakeTemps

    def __repr__(self):
        return str(self.as_dict())

    def as_dict(self) -> dict:
        """Return self as a dict for JSON conversion."""
        try:
            retval = {
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
        except Exception as e:
            print(e)
            retval = {}
        try:
            retval["ambientTempC"] = self.ambientTempC
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
        if other._ambientTempC is not None and abs(self._ambientTempC - other._ambientTempC) > 2:
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
        self._digitals = value

    @property
    def ambientTempC(self) -> float:
        if self._ambientTemp is None:
            raise RuntimeError("uninitialized temperature accessed")
        return self._ambientTemp

    @ambientTempC.setter
    def ambientTempC(self, value: int) -> None:
        self._ambientTempC = value

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

    def _get_digitals_bit_truth(self, bit: int) -> bool:
        return self._digitals & (1 << bit) != 0

    @property
    def heat(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_HEAT)

    @property
    def cool(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_COOL)

    @property
    def fan(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_FAN)

    @property
    def purge(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_PURGE)

    @property
    def emergency(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_EMERGENCY)

    @property
    def zone1(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_ZONE1)

    @property
    def zone2(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_ZONE2)

    @property
    def zone3(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_ZONE3)

    @property
    def zone4(self) -> bool:
        return self._get_digitals_bit_truth(self.DIG_ZONE4)


class HvacReader:
    """Read hardware IO into HvacState."""

    def __init__(self):
        """Do basic hardware setup."""
        self._1w = onewire.OneWire(machine.Pin(_GPIO_1W))
        self._ds18x20 = ds18x20.DS18X20(self._1w)
        self._temp_sensors = []

        self._gpio_heat      = machine.Pin(_GPIO_HEAT, machine.Pin.IN, None)
        self._gpio_cool      = machine.Pin(_GPIO_COOL, machine.Pin.IN, None)
        self._gpio_fan       = machine.Pin(_GPIO_FAN, machine.Pin.IN, None)
        self._gpio_purge     = machine.Pin(_GPIO_PURGE, machine.Pin.IN, None)
        self._gpio_emergency = machine.Pin(_GPIO_EMERGENCY, machine.Pin.IN, None)
        self._gpio_zone1      = machine.Pin(_GPIO_ZONE1, machine.Pin.IN, None)
        self._gpio_zone2      = machine.Pin(_GPIO_ZONE2, machine.Pin.IN, None)
        self._gpio_zone3      = machine.Pin(_GPIO_ZONE3, machine.Pin.IN, None)
        self._gpio_zone4      = machine.Pin(_GPIO_ZONE4, machine.Pin.IN, None)

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

    async def read_io_state(self) -> state:
        """Commit current state to HvacState object.
        Note (async) delay for temp conversions.
        Returns input object for convenience.
        """
        self._ds18x20.convert_temp()
        state = HvacState()
        state.digitals = (
            0
            | self._gpio_heat.value() << state.DIG_HEAT
            | self._gpio_cool.value() << state.DIG_COOL
            | self._gpio_fan.value() << state.DIG_FAN
            | self._gpio_purge.value() << state.DIG_PURGE
            | self._gpio_emergency.value() << state.DIG_EMERGENCY
            | self._gpio_zone1.value() << state.DIG_ZONE1
            | self._gpio_zone2.value() << state.DIG_ZONE2
            | self._gpio_zone3.value() << state.DIG_ZONE3
            | self._gpio_zone4.value() << state.DIG_ZONE4
        )
        await uasyncio.sleep(
            0.750
        )  # worst case conversion time, revisit (don't need 1/16C resolution)
        for ds, found in self._temp_sensors:
            if found == "outdoor":
                state.outdoorTempC = self._ds18x20.read_temp(ds)
            elif found == "discharge":
                state.dischargeTempC = self._ds18x20.read_temp(ds)
            elif found == "return":
                state.returnTempC = self._ds18x20.read_temp(ds)
            elif found == "ambient":
                state.ambientTempC = self._ds18x20.read_temp(ds)
            else:
                pass  # other temps on same network for other uses?
        return state


async def async_run(from_setup):
    led, put_target_url, hvac_reader = from_setup
    print(f'async_run led:{led} url:{put_target_url} hvac_reader:{hvac_reader}')

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
            gc.collect()
            f = gc.mem_free()
            a = gc.mem_alloc()
            print(f'Top of loop, RAM total:{f+a} alloc:{a} free:{f}')
            state = await hvac_reader.read_io_state()
            print(f' state: {repr(state)}')
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
                print(f' --> Posting at {body["_id"]}')
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
    url = f"{SECRET_COUCHDB_PROTOCOL}://{SECRET_COUCHDB_HOSTNAME}:{SECRET_COUCHDB_SERVICE_PORT}/{SECRET_COUCHDB_DBNAME}"

    network.country('US')
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
        sys.print_exception(e)
        led.sync_blink(3, 400, 400)
        with open("/hvac.log", "a") as f:
            sys.print_exception(e, f)
        machine_soft_reset()

    f = gc.mem_free()
    a = gc.mem_alloc()
    print(f'RAM total:{f+a} alloc:{a} free:{f}')

    # Runtime, and runtime errors
    try:
        with open("/hvac.log", "a") as f:
            f.write(f"==============  Running: {time.gmtime()} ==============\n")
        uasyncio.run(async_run(runtime_cfg))
    except Exception as e:
        sys.print_exception(e)
        led.sync_blink(3, 100, 100)
        with open("/hvac.log", "a") as f:
            f.write(f"----------- {time.gmtime()} -----------\n")
            sys.print_exception(e, f)
        machine_soft_reset()
