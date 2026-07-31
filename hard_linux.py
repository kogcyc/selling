#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any


# ------------------------------------------------------------
# Command helpers
# ------------------------------------------------------------

def run_command(
    command: list[str],
    require_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command and return its completed-process object."""

    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=require_success,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Required command not found: {command[0]}"
        ) from error
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip()
        raise RuntimeError(
            f"{' '.join(command)} failed: {message}"
        ) from error


def command_exists(name: str) -> bool:
    """Return True if a command is available in PATH."""

    return shutil.which(name) is not None


def clean(value: str | None, fallback: str = "Not reported") -> str:
    """Return a cleaned display value."""

    if value is None:
        return fallback

    value = str(value).strip()

    if not value:
        return fallback

    return value


# ------------------------------------------------------------
# System information
# ------------------------------------------------------------

def get_dmidecode_type(type_name: str) -> str:
    """Return dmidecode output for a specific type."""

    result = run_command(["dmidecode", "-t", type_name])
    return result.stdout


def parse_key_value_lines(text: str) -> dict[str, str]:
    """Parse indented 'Key: Value' lines into a dictionary."""

    values: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()

    return values


def collect_system() -> dict[str, str]:
    """Collect manufacturer, product, serial, and firmware details."""

    system_text = get_dmidecode_type("system")
    bios_text = get_dmidecode_type("bios")

    system_values = parse_key_value_lines(system_text)
    bios_values = parse_key_value_lines(bios_text)

    return {
        "manufacturer": system_values.get("Manufacturer", ""),
        "model": system_values.get("Product Name", ""),
        "version": system_values.get("Version", ""),
        "serial": system_values.get("Serial Number", ""),
        "uuid": system_values.get("UUID", ""),
        "bios_vendor": bios_values.get("Vendor", ""),
        "bios_version": bios_values.get("Version", ""),
        "bios_date": bios_values.get("Release Date", ""),
    }


# ------------------------------------------------------------
# CPU information
# ------------------------------------------------------------

def collect_cpu() -> dict[str, Any]:
    """Collect detailed CPU information from lscpu JSON output."""

    result = run_command(["lscpu", "--json"])
    data = json.loads(result.stdout)

    fields: dict[str, str] = {}

    for item in data.get("lscpu", []):
        field = item.get("field", "").rstrip(":")
        value = item.get("data", "")
        fields[field] = value

    sockets = integer_value(fields.get("Socket(s)"))
    cores_per_socket = integer_value(fields.get("Core(s) per socket"))
    threads_per_core = integer_value(fields.get("Thread(s) per core"))
    logical_cpus = integer_value(fields.get("CPU(s)"))

    physical_cores = None

    if sockets is not None and cores_per_socket is not None:
        physical_cores = sockets * cores_per_socket

    flags = fields.get("Flags", "").split()

    return {
        "model": fields.get("Model name", ""),
        "architecture": fields.get("Architecture", ""),
        "vendor": fields.get("Vendor ID", ""),
        "sockets": sockets,
        "physical_cores": physical_cores,
        "logical_cpus": logical_cpus,
        "threads_per_core": threads_per_core,
        "max_mhz": fields.get("CPU max MHz", ""),
        "min_mhz": fields.get("CPU min MHz", ""),
        "current_mhz": fields.get("CPU MHz", ""),
        "cache_l1d": fields.get("L1d cache", ""),
        "cache_l1i": fields.get("L1i cache", ""),
        "cache_l2": fields.get("L2 cache", ""),
        "cache_l3": fields.get("L3 cache", ""),
        "virtualization": fields.get("Virtualization", ""),
        "numa_nodes": fields.get("NUMA node(s)", ""),
        "supports_aes": "aes" in flags,
        "supports_avx": "avx" in flags,
        "supports_avx2": "avx2" in flags,
        "supports_avx512": any(
            flag.startswith("avx512") for flag in flags
        ),
    }


def integer_value(value: str | None) -> int | None:
    """Convert a simple integer string to int."""

    if value is None:
        return None

    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


# ------------------------------------------------------------
# Memory information
# ------------------------------------------------------------

def parse_memory_devices(text: str) -> list[dict[str, str]]:
    """Parse all memory slots, including empty slots."""

    devices: list[dict[str, str]] = []

    sections = text.split("Memory Device")

    for section in sections:
        if "Size:" not in section:
            continue

        device = {
            "locator": "",
            "bank_locator": "",
            "size": "",
            "type": "",
            "speed": "",
            "configured_speed": "",
            "manufacturer": "",
            "part": "",
            "serial": "",
            "rank": "",
        }

        for raw_line in section.splitlines():
            line = raw_line.strip()

            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()

            field_map = {
                "Locator": "locator",
                "Bank Locator": "bank_locator",
                "Size": "size",
                "Type": "type",
                "Speed": "speed",
                "Configured Memory Speed": "configured_speed",
                "Manufacturer": "manufacturer",
                "Part Number": "part",
                "Serial Number": "serial",
                "Rank": "rank",
            }

            destination = field_map.get(key)

            if destination:
                device[destination] = value

        devices.append(device)

    return devices


def is_memory_installed(device: dict[str, str]) -> bool:
    """Return True if the memory slot contains a module."""

    size = device.get("size", "").strip().lower()

    return size not in {
        "",
        "no module installed",
        "not installed",
        "unknown",
        "none",
    }


def guess_channel_mode(
    installed_devices: list[dict[str, str]],
) -> str:
    """Infer a likely channel mode for ordinary two-channel systems."""

    count = len(installed_devices)

    if count == 0:
        return "No memory detected"

    if count == 1:
        return "Single Channel (likely)"

    if count == 2:
        first, second = installed_devices

        same_size = first["size"] == second["size"]
        same_speed = first["speed"] == second["speed"]
        same_rank = first["rank"] == second["rank"]

        if same_size and same_speed and same_rank:
            return "Dual Channel (likely)"

        if same_size:
            return "Dual Channel or Flex Mode (possible)"

        return "Flex Mode / asymmetric configuration (possible)"

    return (
        f"{count} modules installed; exact channel mode "
        "not determined"
    )


def collect_memory() -> dict[str, Any]:
    """Collect memory-slot information."""

    text = get_dmidecode_type("memory")
    slots = parse_memory_devices(text)

    installed = [
        slot for slot in slots if is_memory_installed(slot)
    ]

    return {
        "slots": slots,
        "slot_count": len(slots),
        "populated_count": len(installed),
        "empty_count": len(slots) - len(installed),
        "channel_mode": guess_channel_mode(installed),
    }


# ------------------------------------------------------------
# Storage information
# ------------------------------------------------------------

def collect_storage() -> list[dict[str, Any]]:
    """Collect physical storage-device information from lsblk."""

    columns = ",".join([
        "NAME",
        "KNAME",
        "PATH",
        "TYPE",
        "SIZE",
        "MODEL",
        "VENDOR",
        "SERIAL",
        "TRAN",
        "ROTA",
        "RM",
        "REV",
        "WWN",
        "MOUNTPOINTS",
        "FSTYPE",
    ])

    result = run_command([
        "lsblk",
        "--json",
        "--bytes",
        "--output",
        columns,
    ])

    data = json.loads(result.stdout)
    drives: list[dict[str, Any]] = []

    for device in data.get("blockdevices", []):
        if device.get("type") != "disk":
            continue

        path = device.get("path") or f"/dev/{device.get('name')}"

        drive = {
            "name": device.get("name", ""),
            "path": path,
            "size_bytes": device.get("size"),
            "size": format_bytes(device.get("size")),
            "model": clean_storage_text(device.get("model")),
            "vendor": clean_storage_text(device.get("vendor")),
            "serial": clean_storage_text(device.get("serial")),
            "transport": clean_storage_text(device.get("tran")),
            "rotational": bool(device.get("rota")),
            "removable": bool(device.get("rm")),
            "revision": clean_storage_text(device.get("rev")),
            "wwn": clean_storage_text(device.get("wwn")),
            "partitions": collect_partitions(device),
            "smart": {},
        }

        drive["kind"] = determine_drive_kind(drive)

        if command_exists("smartctl"):
            drive["smart"] = collect_smart_data(path)

        drives.append(drive)

    return drives


def clean_storage_text(value: Any) -> str:
    """Clean an lsblk string value."""

    if value is None:
        return ""

    return str(value).strip()


def determine_drive_kind(drive: dict[str, Any]) -> str:
    """Describe the broad storage technology."""

    transport = drive.get("transport", "").lower()

    if transport == "nvme":
        return "NVMe SSD"

    if drive.get("rotational"):
        return "Hard disk drive"

    if transport in {"sata", "ata"}:
        return "SATA SSD"

    if drive.get("removable"):
        return "Removable storage"

    return "Solid-state drive"


def collect_partitions(device: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect partitions belonging to a physical disk."""

    partitions: list[dict[str, Any]] = []

    for child in device.get("children", []) or []:
        partition = {
            "name": child.get("name", ""),
            "path": child.get("path", ""),
            "size": format_bytes(child.get("size")),
            "filesystem": child.get("fstype", ""),
            "mountpoints": [
                mountpoint
                for mountpoint in child.get("mountpoints", []) or []
                if mountpoint
            ],
        }

        partitions.append(partition)

    return partitions


def collect_smart_data(path: str) -> dict[str, Any]:
    """Collect a small, useful subset of smartctl information."""

    result = run_command(
        ["smartctl", "--json", "--all", path],
        require_success=False,
    )

    if not result.stdout.strip():
        return {
            "available": False,
            "error": result.stderr.strip(),
        }

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "available": False,
            "error": "smartctl returned invalid JSON",
        }

    smart_status = data.get("smart_status", {})

    power_on_hours = nested_value(
        data,
        ["power_on_time", "hours"],
    )

    temperature = nested_value(
        data,
        ["temperature", "current"],
    )

    percentage_used = nested_value(
        data,
        ["nvme_smart_health_information_log", "percentage_used"],
    )

    data_units_written = nested_value(
        data,
        [
            "nvme_smart_health_information_log",
            "data_units_written",
        ],
    )

    return {
        "available": True,
        "passed": smart_status.get("passed"),
        "power_on_hours": power_on_hours,
        "temperature_c": temperature,
        "percentage_used": percentage_used,
        "data_units_written": data_units_written,
    }


def nested_value(data: dict[str, Any], keys: list[str]) -> Any:
    """Retrieve a nested dictionary value safely."""

    value: Any = data

    for key in keys:
        if not isinstance(value, dict):
            return None

        value = value.get(key)

        if value is None:
            return None

    return value


def format_bytes(value: Any) -> str:
    """Convert a byte count to a readable binary capacity."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return "Unknown"

    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(number)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"

            if size >= 100:
                return f"{size:.0f} {unit}"

            return f"{size:.1f} {unit}"

        size /= 1024

    return f"{number} B"


# ------------------------------------------------------------
# Report rendering
# ------------------------------------------------------------

def print_heading(title: str) -> None:
    """Print a report section heading."""

    print()
    print(title)
    print("=" * 68)


def print_system_report(system: dict[str, str]) -> None:
    """Print system identity information."""

    print_heading("System")

    print(f"Manufacturer : {clean(system['manufacturer'])}")
    print(f"Model        : {clean(system['model'])}")
    print(f"Version      : {clean(system['version'])}")
    print(f"BIOS Vendor  : {clean(system['bios_vendor'])}")
    print(f"BIOS Version : {clean(system['bios_version'])}")
    print(f"BIOS Date    : {clean(system['bios_date'])}")


def print_cpu_report(cpu: dict[str, Any]) -> None:
    """Print the CPU report."""

    print_heading("CPU")

    print(f"Model              : {clean(cpu['model'])}")
    print(f"Architecture       : {clean(cpu['architecture'])}")
    print(f"Sockets            : {clean(cpu['sockets'])}")
    print(f"Physical cores     : {clean(cpu['physical_cores'])}")
    print(f"Logical processors : {clean(cpu['logical_cpus'])}")
    print(f"Threads per core   : {clean(cpu['threads_per_core'])}")

    if cpu["max_mhz"]:
        print(f"Maximum clock      : {format_mhz(cpu['max_mhz'])}")

    if cpu["min_mhz"]:
        print(f"Minimum clock      : {format_mhz(cpu['min_mhz'])}")

    print(f"L1 data cache      : {clean(cpu['cache_l1d'])}")
    print(f"L1 instruction     : {clean(cpu['cache_l1i'])}")
    print(f"L2 cache           : {clean(cpu['cache_l2'])}")
    print(f"L3 cache           : {clean(cpu['cache_l3'])}")
    print(f"Virtualization     : {clean(cpu['virtualization'], 'No')}")
    print(f"AES instructions   : {yes_no(cpu['supports_aes'])}")
    print(f"AVX                : {yes_no(cpu['supports_avx'])}")
    print(f"AVX2               : {yes_no(cpu['supports_avx2'])}")
    print(f"AVX-512            : {yes_no(cpu['supports_avx512'])}")


def format_mhz(value: str) -> str:
    """Format an MHz value as MHz and GHz."""

    try:
        mhz = float(value)
    except (TypeError, ValueError):
        return clean(value)

    return f"{mhz:.0f} MHz ({mhz / 1000:.2f} GHz)"


def yes_no(value: bool) -> str:
    """Return Yes or No."""

    return "Yes" if value else "No"


def print_memory_report(memory: dict[str, Any]) -> None:
    """Print detailed memory-slot information."""

    print_heading("Memory")

    for index, slot in enumerate(memory["slots"]):
        locator = slot["locator"] or f"Slot {index}"

        print(f"Slot {index}: {locator}")

        if not is_memory_installed(slot):
            print("    Status           : Empty")
            print()
            continue

        print("    Status           : Populated")
        print(f"    Size             : {clean(slot['size'])}")
        print(f"    Type             : {clean(slot['type'])}")
        print(f"    Speed            : {clean(slot['speed'])}")

        if slot["configured_speed"]:
            print(
                f"    Configured Speed : "
                f"{clean(slot['configured_speed'])}"
            )

        print(
            f"    Manufacturer     : "
            f"{clean(slot['manufacturer'])}"
        )
        print(f"    Part Number      : {clean(slot['part'])}")
        print(f"    Rank             : {clean(slot['rank'])}")
        print()

    print(f"Slots detected  : {memory['slot_count']}")
    print(f"Slots populated : {memory['populated_count']}")
    print(f"Slots empty     : {memory['empty_count']}")
    print(f"Channel mode    : {memory['channel_mode']}")


def print_storage_report(
    drives: list[dict[str, Any]],
    include_identifiers: bool,
) -> None:
    """Print storage-device information."""

    print_heading("Storage")

    if not drives:
        print("No physical storage devices detected.")
        return

    for index, drive in enumerate(drives):
        print(f"Drive {index}: {drive['path']}")
        print(f"    Type          : {drive['kind']}")
        print(f"    Capacity      : {drive['size']}")
        print(f"    Vendor        : {clean(drive['vendor'])}")
        print(f"    Model         : {clean(drive['model'])}")
        print(f"    Interface     : {clean(drive['transport'])}")
        print(
            f"    Removable     : "
            f"{yes_no(drive['removable'])}"
        )

        if include_identifiers:
            print(f"    Serial Number : {clean(drive['serial'])}")
            print(f"    WWN           : {clean(drive['wwn'])}")

        smart = drive.get("smart", {})

        if smart.get("available"):
            passed = smart.get("passed")

            if passed is True:
                health = "PASSED"
            elif passed is False:
                health = "FAILED"
            else:
                health = "Not reported"

            print(f"    SMART Health  : {health}")

            if smart.get("power_on_hours") is not None:
                print(
                    f"    Power-on Hours: "
                    f"{smart['power_on_hours']:,}"
                )

            if smart.get("temperature_c") is not None:
                print(
                    f"    Temperature   : "
                    f"{smart['temperature_c']} °C"
                )

            if smart.get("percentage_used") is not None:
                print(
                    f"    NVMe Used     : "
                    f"{smart['percentage_used']}%"
                )
        elif command_exists("smartctl"):
            print("    SMART Details : Unavailable")
        else:
            print(
                "    SMART Details : Install smartmontools "
                "for health data"
            )

        if drive["partitions"]:
            print("    Partitions:")

            for partition in drive["partitions"]:
                mounts = ", ".join(partition["mountpoints"])

                details = (
                    f"{partition['path']} — {partition['size']}"
                )

                if partition["filesystem"]:
                    details += f" — {partition['filesystem']}"

                if mounts:
                    details += f" — mounted at {mounts}"

                print(f"        {details}")

        print()


def print_summary(
    system: dict[str, str],
    cpu: dict[str, Any],
    memory: dict[str, Any],
    drives: list[dict[str, Any]],
) -> None:
    """Print a compact one-line summary."""

    installed_memory = [
        slot for slot in memory["slots"]
        if is_memory_installed(slot)
    ]

    memory_text = " + ".join(
        slot["size"] for slot in installed_memory
    )

    storage_text = ", ".join(
        f"{drive['size']} {drive['kind']}"
        for drive in drives
    )

    parts = [
        clean(system["model"], "Unknown computer"),
        clean(cpu["model"], "Unknown CPU"),
        memory_text or "Unknown memory",
        storage_text or "Unknown storage",
    ]

    print(" | ".join(parts))


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Detailed Ubuntu computer specification report"
    )

    parser.add_argument(
        "--summary",
        action="store_true",
        help="print a compact one-line summary",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="print collected information as JSON",
    )

    parser.add_argument(
        "--include-identifiers",
        action="store_true",
        help="show serial numbers, UUIDs, and WWNs",
    )

    return parser.parse_args()


def main() -> None:
    """Collect and print the computer specification."""

    args = parse_arguments()

    if os.geteuid() != 0:
        print("This utility must be run as root.")
        print(f"Usage: sudo python3 {sys.argv[0]}")
        sys.exit(1)

    required_commands = [
        "dmidecode",
        "lscpu",
        "lsblk",
    ]

    missing = [
        command
        for command in required_commands
        if not command_exists(command)
    ]

    if missing:
        print(
            "Missing required commands: "
            + ", ".join(missing)
        )
        sys.exit(1)

    try:
        system = collect_system()
        cpu = collect_cpu()
        memory = collect_memory()
        storage = collect_storage()
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"Unable to collect computer information: {error}")
        sys.exit(1)

    report = {
        "system": system,
        "cpu": cpu,
        "memory": memory,
        "storage": storage,
    }

    if not args.include_identifiers:
        report["system"]["serial"] = ""
        report["system"]["uuid"] = ""

        for drive in report["storage"]:
            drive["serial"] = ""
            drive["wwn"] = ""

    if args.json:
        print(json.dumps(report, indent=2))
        return

    if args.summary:
        print_summary(system, cpu, memory, storage)
        return

    print_system_report(system)
    print_cpu_report(cpu)
    print_memory_report(memory)
    print_storage_report(
        storage,
        include_identifiers=args.include_identifiers,
    )


if __name__ == "__main__":
    main()