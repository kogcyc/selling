#!/usr/bin/env python3

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
from typing import Any, Optional


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


def run_command_bytes(
    command: list[str],
    require_success: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run a command and preserve its byte output."""

    try:
        return subprocess.run(
            command,
            capture_output=True,
            check=require_success,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Required command not found: {command[0]}"
        ) from error
    except subprocess.CalledProcessError as error:
        message = (
            error.stderr.decode(errors="replace").strip()
            or error.stdout.decode(errors="replace").strip()
        )
        raise RuntimeError(
            f"{' '.join(command)} failed: {message}"
        ) from error


def command_exists(name: str) -> bool:
    """Return True if a command is available in PATH."""

    return shutil.which(name) is not None


def clean(value: Any, fallback: str = "Not reported") -> str:
    """Return a cleaned display value."""

    if value is None:
        return fallback

    value = str(value).strip()

    if not value:
        return fallback

    return value


def integer_value(value: Any) -> Optional[int]:
    """Convert a value to int when possible."""

    if value is None:
        return None

    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def get_plist(command: list[str]) -> dict[str, Any]:
    """Run a command that emits an Apple property list."""

    result = run_command_bytes(command)

    try:
        value = plistlib.loads(result.stdout)
    except Exception as error:
        raise RuntimeError(
            f"{' '.join(command)} returned an invalid property list"
        ) from error

    if not isinstance(value, dict):
        raise RuntimeError(
            f"{' '.join(command)} returned an unexpected property list"
        )

    return value


def get_system_profiler(data_type: str) -> list[dict[str, Any]]:
    """Return one system_profiler data type as JSON records."""

    result = run_command([
        "system_profiler",
        data_type,
        "-json",
        "-detailLevel",
        "full",
    ])

    data = json.loads(result.stdout)
    records = data.get(data_type, [])

    if isinstance(records, list):
        return records

    return []


def sysctl_value(name: str) -> str:
    """Return a sysctl value, or an empty string when unavailable."""

    result = run_command(
        ["sysctl", "-n", name],
        require_success=False,
    )

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


# ------------------------------------------------------------
# System information
# ------------------------------------------------------------

def collect_system() -> dict[str, str]:
    """Collect Mac model, serial, firmware, and macOS details."""

    records = get_system_profiler("SPHardwareDataType")
    hardware = records[0] if records else {}

    product_name = hardware.get("machine_name", "Mac")
    model_identifier = hardware.get("machine_model", "")

    return {
        "manufacturer": "Apple Inc.",
        "model": str(product_name),
        "version": str(model_identifier),
        "serial": str(hardware.get("serial_number", "")),
        "uuid": str(hardware.get("platform_UUID", "")),
        "bios_vendor": "Apple Inc.",
        "bios_version": str(
            hardware.get("boot_rom_version")
            or hardware.get("system_firmware_version")
            or ""
        ),
        "bios_date": "",
        "smc_version": str(hardware.get("smc_version_system", "")),
        "os_version": macos_version(),
    }


def macos_version() -> str:
    """Return the installed macOS product version and build."""

    version = run_command(
        ["sw_vers", "-productVersion"],
        require_success=False,
    ).stdout.strip()

    build = run_command(
        ["sw_vers", "-buildVersion"],
        require_success=False,
    ).stdout.strip()

    if version and build:
        return f"macOS {version} ({build})"

    if version:
        return f"macOS {version}"

    return ""


# ------------------------------------------------------------
# CPU information
# ------------------------------------------------------------

def collect_cpu() -> dict[str, Any]:
    """Collect Intel CPU information from sysctl and system_profiler."""

    records = get_system_profiler("SPHardwareDataType")
    hardware = records[0] if records else {}

    feature_text = " ".join([
        sysctl_value("machdep.cpu.features"),
        sysctl_value("machdep.cpu.leaf7_features"),
        sysctl_value("machdep.cpu.extfeatures"),
    ]).upper()

    physical_cores = integer_value(
        sysctl_value("hw.physicalcpu_max")
        or hardware.get("number_processors")
    )
    logical_cpus = integer_value(
        sysctl_value("hw.logicalcpu_max")
        or hardware.get("number_logical_processors")
    )

    max_hz = integer_value(sysctl_value("hw.cpufrequency_max"))
    min_hz = integer_value(sysctl_value("hw.cpufrequency_min"))

    threads_per_core = None
    if physical_cores and logical_cpus:
        threads_per_core = max(1, logical_cpus // physical_cores)

    return {
        "model": (
            sysctl_value("machdep.cpu.brand_string")
            or str(hardware.get("cpu_type", ""))
        ),
        "architecture": sysctl_value("hw.machine"),
        "vendor": sysctl_value("machdep.cpu.vendor"),
        "sockets": 1,
        "physical_cores": physical_cores,
        "logical_cpus": logical_cpus,
        "threads_per_core": threads_per_core,
        "max_hz": max_hz,
        "min_hz": min_hz,
        "cache_l1d": format_bytes(sysctl_value("hw.l1dcachesize")),
        "cache_l1i": format_bytes(sysctl_value("hw.l1icachesize")),
        "cache_l2": format_bytes(sysctl_value("hw.l2cachesize")),
        "cache_l3": format_bytes(sysctl_value("hw.l3cachesize")),
        "virtualization": "Intel VT-x" if "VMX" in feature_text else "",
        "supports_aes": "AES" in feature_text,
        "supports_avx": "AVX1.0" in feature_text or " AVX " in f" {feature_text} ",
        "supports_avx2": "AVX2" in feature_text,
        "supports_avx512": "AVX512" in feature_text,
    }


# ------------------------------------------------------------
# Memory information
# ------------------------------------------------------------

def normalize_memory_slot(item: dict[str, Any]) -> dict[str, str]:
    """Convert a system_profiler memory record to our common format."""

    size = str(item.get("dimm_size", ""))
    status = str(item.get("dimm_status", "")).lower()

    if status in {"empty", "not installed"}:
        size = "No Module Installed"

    speed = str(
        item.get("dimm_speed")
        or item.get("dimm_config_speed")
        or ""
    )

    return {
        "locator": str(
            item.get("_name")
            or item.get("dimm_slot")
            or ""
        ),
        "bank_locator": str(item.get("dimm_bank", "")),
        "size": size,
        "type": str(item.get("dimm_type", "")),
        "speed": speed,
        "configured_speed": str(item.get("dimm_config_speed", "")),
        "manufacturer": str(item.get("dimm_manufacturer", "")),
        "part": str(item.get("dimm_part_number", "")),
        "serial": str(item.get("dimm_serial_number", "")),
        "rank": str(item.get("dimm_rank", "")),
    }


def collect_memory() -> dict[str, Any]:
    """Collect memory-slot information from system_profiler."""

    records = get_system_profiler("SPMemoryDataType")
    slots: list[dict[str, str]] = []

    def walk(items: Any) -> None:
        if isinstance(items, list):
            for item in items:
                walk(item)
            return

        if not isinstance(items, dict):
            return

        if any(key.startswith("dimm_") for key in items):
            slots.append(normalize_memory_slot(items))

        for key in ("_items", "items"):
            if key in items:
                walk(items[key])

    walk(records)

    installed = [slot for slot in slots if is_memory_installed(slot)]

    return {
        "slots": slots,
        "slot_count": len(slots),
        "populated_count": len(installed),
        "empty_count": len(slots) - len(installed),
        "channel_mode": guess_channel_mode(installed),
        "total": format_bytes(sysctl_value("hw.memsize")),
    }


def is_memory_installed(device: dict[str, str]) -> bool:
    """Return True if the memory slot contains a module."""

    size = device.get("size", "").strip().lower()

    return size not in {
        "",
        "no module installed",
        "not installed",
        "empty",
        "unknown",
        "none",
    }


def guess_channel_mode(
    installed_devices: list[dict[str, str]],
) -> str:
    """Infer a likely channel mode; macOS does not expose it directly."""

    count = len(installed_devices)

    if count == 0:
        return "Not determined by macOS"

    if count == 1:
        return "Single Channel (likely)"

    if count == 2:
        first, second = installed_devices

        if first["size"] == second["size"]:
            return "Dual Channel (likely)"

        return "Asymmetric / flex configuration (possible)"

    if count % 2 == 0:
        return f"{count} modules installed; multi-channel operation likely"

    return f"{count} modules installed; exact channel mode not determined"


# ------------------------------------------------------------
# Storage information
# ------------------------------------------------------------

def collect_storage() -> list[dict[str, Any]]:
    """Collect physical storage devices through diskutil."""

    data = get_plist(["diskutil", "list", "-plist", "physical"])
    entries = data.get("AllDisksAndPartitions", [])
    drives: list[dict[str, Any]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        identifier = str(entry.get("DeviceIdentifier", ""))
        if not identifier:
            continue

        info = get_plist(["diskutil", "info", "-plist", identifier])
        path = str(info.get("DeviceNode") or f"/dev/{identifier}")

        drive = {
            "name": identifier,
            "path": path,
            "size_bytes": info.get("TotalSize"),
            "size": format_bytes(info.get("TotalSize")),
            "model": clean_storage_text(
                info.get("DeviceModel")
                or info.get("MediaName")
            ),
            "vendor": clean_storage_text(info.get("DeviceVendor")),
            "serial": clean_storage_text(info.get("DiskUUID")),
            "transport": clean_storage_text(info.get("BusProtocol")),
            "rotational": not bool(info.get("SolidState", False)),
            "removable": bool(info.get("Removable", False)),
            "revision": "",
            "wwn": "",
            "internal": bool(info.get("Internal", False)),
            "partitions": collect_partitions(entry),
            "smart": collect_disk_smart(info, path),
        }

        drive["kind"] = determine_drive_kind(drive)
        drives.append(drive)

    return drives


def clean_storage_text(value: Any) -> str:
    """Clean a diskutil string value."""

    if value is None:
        return ""

    return str(value).strip()


def determine_drive_kind(drive: dict[str, Any]) -> str:
    """Describe the broad storage technology."""

    transport = drive.get("transport", "").lower()

    if "nvme" in transport or "pci" in transport:
        return "NVMe SSD"

    if drive.get("removable") and "usb" in transport:
        return "USB removable storage"

    if drive.get("rotational"):
        return "Hard disk drive"

    if "sata" in transport:
        return "SATA SSD"

    return "Solid-state drive"


def collect_partitions(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect partitions belonging to a physical disk."""

    partitions: list[dict[str, Any]] = []

    for child in entry.get("Partitions", []) or []:
        if not isinstance(child, dict):
            continue

        identifier = str(child.get("DeviceIdentifier", ""))
        mountpoint = child.get("MountPoint")

        partition = {
            "name": identifier,
            "path": f"/dev/{identifier}" if identifier else "",
            "size": format_bytes(child.get("Size")),
            "filesystem": clean_storage_text(
                child.get("Content")
                or child.get("VolumeName")
            ),
            "mountpoints": [str(mountpoint)] if mountpoint else [],
        }

        partitions.append(partition)

    return partitions


def collect_disk_smart(
    diskutil_info: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    """Collect SMART status from diskutil and optional smartctl."""

    status = clean_storage_text(diskutil_info.get("SMARTStatus"))
    base: dict[str, Any] = {
        "available": bool(status),
        "passed": status.lower() == "verified" if status else None,
        "status": status,
    }

    if not command_exists("smartctl"):
        return base

    detailed = collect_smart_data(path)

    if detailed.get("available"):
        if base.get("passed") is not None and detailed.get("passed") is None:
            detailed["passed"] = base["passed"]
        if status:
            detailed["status"] = status
        return detailed

    if status:
        return base

    return detailed


def collect_smart_data(path: str) -> dict[str, Any]:
    """Collect a useful subset of smartctl JSON information."""

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

    return {
        "available": True,
        "passed": data.get("smart_status", {}).get("passed"),
        "power_on_hours": nested_value(data, ["power_on_time", "hours"]),
        "temperature_c": nested_value(data, ["temperature", "current"]),
        "percentage_used": nested_value(
            data,
            ["nvme_smart_health_information_log", "percentage_used"],
        ),
        "data_units_written": nested_value(
            data,
            ["nvme_smart_health_information_log", "data_units_written"],
        ),
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
    print()
    print(title)
    print("=" * 68)


def print_system_report(system: dict[str, str]) -> None:
    print_heading("System")

    print(f"Manufacturer    : {clean(system['manufacturer'])}")
    print(f"Model           : {clean(system['model'])}")
    print(f"Model Identifier: {clean(system['version'])}")
    print(f"macOS           : {clean(system['os_version'])}")
    print(f"Firmware        : {clean(system['bios_version'])}")

    if system.get("smc_version"):
        print(f"SMC Version     : {clean(system['smc_version'])}")


def print_cpu_report(cpu: dict[str, Any]) -> None:
    print_heading("CPU")

    print(f"Model              : {clean(cpu['model'])}")
    print(f"Architecture       : {clean(cpu['architecture'])}")
    print(f"Sockets            : {clean(cpu['sockets'])}")
    print(f"Physical cores     : {clean(cpu['physical_cores'])}")
    print(f"Logical processors : {clean(cpu['logical_cpus'])}")
    print(f"Threads per core   : {clean(cpu['threads_per_core'])}")

    if cpu["max_hz"]:
        print(f"Maximum clock      : {format_hz(cpu['max_hz'])}")

    if cpu["min_hz"]:
        print(f"Minimum clock      : {format_hz(cpu['min_hz'])}")

    print(f"L1 data cache      : {clean(cpu['cache_l1d'])}")
    print(f"L1 instruction     : {clean(cpu['cache_l1i'])}")
    print(f"L2 cache           : {clean(cpu['cache_l2'])}")
    print(f"L3 cache           : {clean(cpu['cache_l3'])}")
    print(f"Virtualization     : {clean(cpu['virtualization'], 'No')}")
    print(f"AES instructions   : {yes_no(cpu['supports_aes'])}")
    print(f"AVX                : {yes_no(cpu['supports_avx'])}")
    print(f"AVX2               : {yes_no(cpu['supports_avx2'])}")
    print(f"AVX-512            : {yes_no(cpu['supports_avx512'])}")


def format_hz(value: Any) -> str:
    try:
        hz = int(value)
    except (TypeError, ValueError):
        return clean(value)

    mhz = hz / 1_000_000
    return f"{mhz:.0f} MHz ({hz / 1_000_000_000:.2f} GHz)"


def yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def print_memory_report(memory: dict[str, Any]) -> None:
    print_heading("Memory")

    if not memory["slots"]:
        print(f"Total memory    : {clean(memory.get('total'))}")
        print("Slot details    : Not reported by this Mac")
        print("Channel mode    : Not determined by macOS")
        return

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
        print(f"    Manufacturer     : {clean(slot['manufacturer'])}")
        print(f"    Part Number      : {clean(slot['part'])}")

        if slot["rank"]:
            print(f"    Rank             : {clean(slot['rank'])}")

        print()

    print(f"Total memory    : {clean(memory.get('total'))}")
    print(f"Slots detected  : {memory['slot_count']}")
    print(f"Slots populated : {memory['populated_count']}")
    print(f"Slots empty     : {memory['empty_count']}")
    print(f"Channel mode    : {memory['channel_mode']}")


def print_storage_report(
    drives: list[dict[str, Any]],
    include_identifiers: bool,
) -> None:
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
        print(f"    Internal      : {yes_no(drive['internal'])}")
        print(f"    Removable     : {yes_no(drive['removable'])}")

        if include_identifiers:
            print(f"    Disk UUID     : {clean(drive['serial'])}")

        smart = drive.get("smart", {})

        if smart.get("available"):
            passed = smart.get("passed")

            if passed is True:
                health = "PASSED"
            elif passed is False:
                health = "FAILED"
            else:
                health = clean(smart.get("status"))

            print(f"    SMART Health  : {health}")

            if smart.get("power_on_hours") is not None:
                print(f"    Power-on Hours: {smart['power_on_hours']:,}")

            if smart.get("temperature_c") is not None:
                print(f"    Temperature   : {smart['temperature_c']} °C")

            if smart.get("percentage_used") is not None:
                print(f"    NVMe Used     : {smart['percentage_used']}%")
        elif command_exists("smartctl"):
            print("    SMART Details : Unavailable or permission denied")
        else:
            print("    SMART Details : Install smartmontools for more data")

        if drive["partitions"]:
            print("    Partitions:")

            for partition in drive["partitions"]:
                mounts = ", ".join(partition["mountpoints"])
                details = f"{partition['path']} — {partition['size']}"

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
    installed_memory = [
        slot for slot in memory["slots"]
        if is_memory_installed(slot)
    ]

    if installed_memory:
        memory_text = " + ".join(slot["size"] for slot in installed_memory)
    else:
        memory_text = clean(memory.get("total"), "Unknown memory")

    storage_text = ", ".join(
        f"{drive['size']} {drive['kind']}"
        for drive in drives
    )

    parts = [
        clean(system["model"], "Unknown Mac"),
        clean(cpu["model"], "Unknown CPU"),
        memory_text,
        storage_text or "Unknown storage",
    ]

    print(" | ".join(parts))


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detailed Intel Mac specification report"
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
        help="show serial numbers, UUIDs, and disk identifiers",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if sys.platform != "darwin":
        print("This version is intended for macOS on Intel Macs.")
        sys.exit(1)

    if sysctl_value("hw.machine") != "x86_64":
        print("This version is intended for Intel Macs (x86_64).")
        sys.exit(1)

    required_commands = [
        "system_profiler",
        "sysctl",
        "diskutil",
        "sw_vers",
    ]

    missing = [
        command for command in required_commands
        if not command_exists(command)
    ]

    if missing:
        print("Missing required commands: " + ", ".join(missing))
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
