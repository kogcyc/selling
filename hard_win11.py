#!/usr/bin/env python3

"""
Comp Spec for Windows 11
Buyer-friendly used-computer inspection report.

Requirements:
    Windows 10 or Windows 11
    Python 3.10 or newer
    Windows PowerShell 5.1 or PowerShell 7

Run:

    python comp_spec_windows.py

Compact summary:

    python comp_spec_windows.py --summary

Machine-readable JSON:

    python comp_spec_windows.py --json
"""

import argparse
import ctypes
import json
import platform
import re
import shutil
import subprocess
import sys
from typing import Any


REPORT_WIDTH = 64


# ------------------------------------------------------------
# General helpers
# ------------------------------------------------------------

def clean(value: Any, fallback: str = "Not reported") -> str:
    """Return a clean display value."""

    if value is None:
        return fallback

    text = str(value).strip()

    return text if text else fallback


def integer_value(value: Any) -> int | None:
    """Convert a value to an integer when possible."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ensure_list(value: Any) -> list[Any]:
    """
    PowerShell ConvertTo-Json returns an object for one result
    and an array for multiple results. Normalize either to a list.
    """

    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def command_exists(name: str) -> bool:
    """Return True when an executable exists in PATH."""

    return shutil.which(name) is not None


def find_powershell() -> str:
    """
    Prefer Windows PowerShell because its Windows management
    modules are present on ordinary Windows 11 installations.
    """

    for executable in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        path = shutil.which(executable)

        if path:
            return path

    raise RuntimeError(
        "PowerShell was not found. Windows PowerShell should "
        "normally be included with Windows 11."
    )


POWERSHELL = ""


def run_powershell(script: str) -> str:
    """Run a PowerShell command and return its standard output."""

    command = [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="replace",
        )

    except FileNotFoundError as error:
        raise RuntimeError(
            "PowerShell could not be started."
        ) from error

    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip()

        raise RuntimeError(
            f"PowerShell command failed: {message}"
        ) from error

    return result.stdout.strip()


def run_powershell_json(script: str) -> Any:
    """Run PowerShell and decode its JSON output."""

    wrapped_script = f"""
$ErrorActionPreference = "Stop"

$result = & {{
{script}
}}

if ($null -eq $result) {{
    "null"
}}
else {{
    $result | ConvertTo-Json -Depth 8 -Compress
}}
"""

    output = run_powershell(wrapped_script)

    if not output:
        return None

    try:
        return json.loads(output)

    except json.JSONDecodeError as error:
        raise RuntimeError(
            "PowerShell returned data that was not valid JSON."
        ) from error


def is_administrator() -> bool:
    """Return True when the script has administrator privileges."""

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


# ------------------------------------------------------------
# System
# ------------------------------------------------------------

def collect_system() -> dict[str, Any]:
    """Collect computer model, firmware, and Windows information."""

    data = run_powershell_json(
        r"""
$computer = Get-CimInstance -ClassName Win32_ComputerSystem
$product  = Get-CimInstance -ClassName Win32_ComputerSystemProduct
$bios     = Get-CimInstance -ClassName Win32_BIOS
$os       = Get-CimInstance -ClassName Win32_OperatingSystem

[PSCustomObject]@{
    Manufacturer = $computer.Manufacturer
    Model = $computer.Model
    SystemType = $computer.SystemType
    TotalMemoryBytes = [UInt64]$computer.TotalPhysicalMemory
    BIOSVendor = $bios.Manufacturer
    BIOSVersion = ($bios.SMBIOSBIOSVersion | Select-Object -First 1)
    BIOSDate = $bios.ReleaseDate
    WindowsName = $os.Caption
    WindowsVersion = $os.Version
    WindowsBuild = $os.BuildNumber
    Architecture = $os.OSArchitecture
}
"""
    ) or {}

    return {
        "manufacturer": clean(data.get("Manufacturer"), ""),
        "model": clean(data.get("Model"), ""),
        "system_type": clean(data.get("SystemType"), ""),
        "total_memory_bytes": integer_value(
            data.get("TotalMemoryBytes")
        ),
        "bios_vendor": clean(data.get("BIOSVendor"), ""),
        "bios_version": clean(data.get("BIOSVersion"), ""),
        "bios_date": format_windows_date(data.get("BIOSDate")),
        "windows_name": clean(data.get("WindowsName"), ""),
        "windows_version": clean(data.get("WindowsVersion"), ""),
        "windows_build": clean(data.get("WindowsBuild"), ""),
        "architecture": clean(data.get("Architecture"), ""),
    }


def format_windows_date(value: Any) -> str:
    """Format a PowerShell/WMI date for display."""

    if value is None:
        return ""

    text = str(value).strip()

    # PowerShell may serialize a DateTime as an ISO string.
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)

    if match:
        year, month, day = match.groups()
        return f"{month}/{day}/{year}"

    # Older WMI date representation: YYYYMMDD...
    match = re.match(r"^(\d{4})(\d{2})(\d{2})", text)

    if match:
        year, month, day = match.groups()
        return f"{month}/{day}/{year}"

    return text


# ------------------------------------------------------------
# CPU
# ------------------------------------------------------------

def collect_cpu() -> dict[str, Any]:
    """Collect buyer-relevant processor information."""

    cpus = ensure_list(
        run_powershell_json(
            r"""
Get-CimInstance -ClassName Win32_Processor |
    Select-Object `
        Name,
        Manufacturer,
        SocketDesignation,
        NumberOfCores,
        NumberOfLogicalProcessors,
        MaxClockSpeed,
        VirtualizationFirmwareEnabled,
        SecondLevelAddressTranslationExtensions
"""
        )
    )

    if not cpus:
        return {}

    first = cpus[0]

    physical_cores = sum(
        integer_value(cpu.get("NumberOfCores")) or 0
        for cpu in cpus
    )

    logical_processors = sum(
        integer_value(cpu.get("NumberOfLogicalProcessors")) or 0
        for cpu in cpus
    )

    maximum_clock = max(
        (
            integer_value(cpu.get("MaxClockSpeed")) or 0
            for cpu in cpus
        ),
        default=0,
    )

    return {
        "model": clean(first.get("Name"), ""),
        "manufacturer": clean(first.get("Manufacturer"), ""),
        "sockets": len(cpus),
        "physical_cores": physical_cores or None,
        "logical_processors": logical_processors or None,
        "max_mhz": maximum_clock or None,
        "virtualization_enabled": first.get(
            "VirtualizationFirmwareEnabled"
        ),
        "second_level_translation": first.get(
            "SecondLevelAddressTranslationExtensions"
        ),
    }


def format_cpu_clock(mhz: Any) -> str:
    """Format an MHz clock value as GHz."""

    try:
        value = float(mhz)
    except (TypeError, ValueError):
        return ""

    return f"{value / 1000:.2f} GHz maximum"


# ------------------------------------------------------------
# Memory
# ------------------------------------------------------------

MEMORY_TYPE_NAMES = {
    0: "Unknown",
    1: "Other",
    20: "DDR",
    21: "DDR2",
    22: "DDR2 FB-DIMM",
    24: "DDR3",
    26: "DDR4",
    27: "LPDDR",
    28: "LPDDR2",
    29: "LPDDR3",
    30: "LPDDR4",
    34: "DDR5",
    35: "LPDDR5",
}


def memory_type_name(value: Any) -> str:
    """Translate the SMBIOS memory-type number."""

    number = integer_value(value)

    if number is None:
        return "RAM"

    return MEMORY_TYPE_NAMES.get(
        number,
        f"Memory type {number}",
    )


def collect_memory() -> dict[str, Any]:
    """
    Collect physical memory modules and total slot count.

    Windows reports installed modules through Win32_PhysicalMemory.
    The total socket count normally comes from
    Win32_PhysicalMemoryArray.MemoryDevices.
    """

    modules = ensure_list(
        run_powershell_json(
            r"""
Get-CimInstance -ClassName Win32_PhysicalMemory |
    Select-Object `
        BankLabel,
        DeviceLocator,
        Capacity,
        Speed,
        ConfiguredClockSpeed,
        Manufacturer,
        PartNumber,
        FormFactor,
        SMBIOSMemoryType
"""
        )
    )

    arrays = ensure_list(
        run_powershell_json(
            r"""
Get-CimInstance -ClassName Win32_PhysicalMemoryArray |
    Select-Object MemoryDevices, MaxCapacity, MaxCapacityEx
"""
        )
    )

    installed: list[dict[str, Any]] = []

    for module in modules:
        installed.append({
            "locator": (
                clean(module.get("DeviceLocator"), "")
                or clean(module.get("BankLabel"), "")
            ),
            "bank": clean(module.get("BankLabel"), ""),
            "capacity_bytes": integer_value(
                module.get("Capacity")
            ),
            "size": format_memory_size(
                module.get("Capacity")
            ),
            "speed": format_memory_speed(
                module.get("Speed")
            ),
            "configured_speed": format_memory_speed(
                module.get("ConfiguredClockSpeed")
            ),
            "manufacturer": clean(
                module.get("Manufacturer"),
                "",
            ),
            "part_number": clean(
                module.get("PartNumber"),
                "",
            ),
            "type": memory_type_name(
                module.get("SMBIOSMemoryType")
            ),
        })

    reported_slots = sum(
        integer_value(array.get("MemoryDevices")) or 0
        for array in arrays
    )

    populated_count = len(installed)

    # Some firmware reports zero or omits MemoryDevices.
    slot_count = max(reported_slots, populated_count)
    empty_count = max(slot_count - populated_count, 0)

    total_bytes = sum(
        module.get("capacity_bytes") or 0
        for module in installed
    )

    return {
        "installed": installed,
        "total_bytes": total_bytes,
        "total": format_memory_size(total_bytes),
        "slot_count": slot_count,
        "populated_count": populated_count,
        "empty_count": empty_count,
        "channel_mode": guess_channel_mode(installed),
    }


def format_memory_size(value: Any) -> str:
    """Format memory capacity using ordinary seller-friendly units."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return "Unknown size"

    gib = number / (1024 ** 3)

    if gib >= 1024:
        return f"{gib / 1024:.1f} TB"

    if gib >= 10:
        return f"{gib:.0f} GB"

    return f"{gib:.1f} GB"


def format_memory_speed(value: Any) -> str:
    """Format memory speed in MT/s."""

    speed = integer_value(value)

    if not speed:
        return ""

    return f"{speed} MT/s"


def describe_memory_modules(
    installed: list[dict[str, Any]],
) -> str:
    """Return a description such as '2 × 8 GB Samsung'."""

    if not installed:
        return "No installed modules reported"

    sizes = [
        module["size"]
        for module in installed
    ]

    manufacturers = {
        module["manufacturer"]
        for module in installed
        if module.get("manufacturer")
        and module["manufacturer"].lower()
        not in {"unknown", "not reported"}
    }

    if len(set(sizes)) == 1:
        description = f"{len(sizes)} × {sizes[0]}"
    else:
        description = " + ".join(sizes)

    if len(manufacturers) == 1:
        description += f" {next(iter(manufacturers))}"

    return description


def common_memory_type(
    installed: list[dict[str, Any]],
) -> str:
    """Return the common memory type."""

    types = {
        module["type"]
        for module in installed
        if module.get("type")
        and module["type"] not in {"Unknown", "RAM"}
    }

    if len(types) == 1:
        return next(iter(types))

    if len(types) > 1:
        return "/".join(sorted(types))

    return "RAM"


def common_memory_speed(
    installed: list[dict[str, Any]],
) -> str:
    """Return the common configured module speed."""

    speeds = [
        module.get("configured_speed")
        or module.get("speed")
        for module in installed
    ]

    speeds = [
        speed for speed in speeds if speed
    ]

    if not speeds:
        return ""

    if len(set(speeds)) == 1:
        return speeds[0]

    return "Mixed speeds: " + ", ".join(sorted(set(speeds)))


def guess_channel_mode(
    installed: list[dict[str, Any]],
) -> str:
    """
    Provide a conservative estimate.

    Windows CIM usually does not report the active memory-controller
    channel mode directly.
    """

    count = len(installed)

    if count == 0:
        return "No memory detected"

    if count == 1:
        return "Single channel likely"

    sizes = [
        module.get("capacity_bytes")
        for module in installed
    ]

    speeds = [
        module.get("configured_speed")
        or module.get("speed")
        for module in installed
    ]

    if count == 2:
        if sizes[0] == sizes[1] and speeds[0] == speeds[1]:
            return "Dual channel likely"

        if sizes[0] == sizes[1]:
            return "Dual channel possible"

        return "Flex or asymmetric mode likely"

    if count == 4 and len(set(sizes)) == 1:
        return "Multi-module configuration; dual channel likely"

    return (
        f"{count} modules installed; "
        "exact channel mode not reported"
    )


# ------------------------------------------------------------
# Storage
# ------------------------------------------------------------

def collect_storage() -> list[dict[str, Any]]:
    """
    Collect physical disks from the Windows Storage module.

    Get-PhysicalDisk generally provides the most useful seller-facing
    fields, including media type and health status.
    """

    physical_disks = ensure_list(
        run_powershell_json(
            r"""
Get-PhysicalDisk |
    Select-Object `
        FriendlyName,
        Manufacturer,
        Model,
        MediaType,
        BusType,
        Size,
        HealthStatus,
        OperationalStatus,
        CanPool
"""
        )
    )

    drives: list[dict[str, Any]] = []

    for disk in physical_disks:
        media_type = clean(
            disk.get("MediaType"),
            "",
        )

        bus_type = clean(
            disk.get("BusType"),
            "",
        )

        drives.append({
            "name": clean(
                disk.get("FriendlyName"),
                "",
            ),
            "manufacturer": clean(
                disk.get("Manufacturer"),
                "",
            ),
            "model": clean(
                disk.get("Model"),
                "",
            ),
            "media_type": media_type,
            "bus_type": bus_type,
            "capacity_bytes": integer_value(
                disk.get("Size")
            ),
            "capacity": format_storage_size(
                disk.get("Size")
            ),
            "kind": determine_windows_drive_kind(
                media_type,
                bus_type,
                disk.get("FriendlyName"),
            ),
            "health": clean(
                disk.get("HealthStatus"),
                "Unknown",
            ),
            "operational_status": status_to_text(
                disk.get("OperationalStatus")
            ),
        })

    # Fallback for systems where Get-PhysicalDisk returns nothing.
    if not drives:
        drives = collect_storage_wmi_fallback()

    return drives


def collect_storage_wmi_fallback() -> list[dict[str, Any]]:
    """Fallback storage collection through Win32_DiskDrive."""

    disks = ensure_list(
        run_powershell_json(
            r"""
Get-CimInstance -ClassName Win32_DiskDrive |
    Select-Object `
        Model,
        Manufacturer,
        InterfaceType,
        MediaType,
        Size,
        Status
"""
        )
    )

    drives: list[dict[str, Any]] = []

    for disk in disks:
        interface_type = clean(
            disk.get("InterfaceType"),
            "",
        )

        media_type = clean(
            disk.get("MediaType"),
            "",
        )

        model = clean(
            disk.get("Model"),
            "",
        )

        drives.append({
            "name": model,
            "manufacturer": clean(
                disk.get("Manufacturer"),
                "",
            ),
            "model": model,
            "media_type": media_type,
            "bus_type": interface_type,
            "capacity_bytes": integer_value(
                disk.get("Size")
            ),
            "capacity": format_storage_size(
                disk.get("Size")
            ),
            "kind": determine_windows_drive_kind(
                media_type,
                interface_type,
                model,
            ),
            "health": clean(
                disk.get("Status"),
                "Unknown",
            ),
            "operational_status": "",
        })

    return drives


def status_to_text(value: Any) -> str:
    """Convert PowerShell status data to readable text."""

    if value is None:
        return ""

    if isinstance(value, list):
        return ", ".join(str(item) for item in value)

    return str(value).strip()


def determine_windows_drive_kind(
    media_type: str,
    bus_type: str,
    model: Any,
) -> str:
    """Determine NVMe, SATA SSD, HDD, or other storage type."""

    media = media_type.lower()
    bus = bus_type.lower()
    name = clean(model, "").lower()

    if bus == "nvme" or "nvme" in name:
        return "NVMe SSD"

    if media == "hdd" or "fixed hard disk" in media:
        return "Hard disk drive"

    if media == "ssd":
        if bus in {"sata", "ata"}:
            return "SATA SSD"

        return "Solid-state drive"

    if "ssd" in name:
        if bus in {"sata", "ata"}:
            return "SATA SSD"

        return "Solid-state drive"

    if bus == "usb":
        return "USB storage"

    return "Physical storage device"


def format_storage_size(value: Any) -> str:
    """
    Format storage in decimal units, matching how drives
    are normally advertised by sellers and manufacturers.
    """

    try:
        number = int(value)
    except (TypeError, ValueError):
        return "Unknown capacity"

    if number >= 1_000_000_000_000:
        terabytes = number / 1_000_000_000_000

        if terabytes >= 10:
            return f"{terabytes:.0f} TB"

        return f"{terabytes:.1f} TB"

    gigabytes = number / 1_000_000_000

    if gigabytes >= 100:
        return f"{gigabytes:.0f} GB"

    return f"{gigabytes:.1f} GB"


def drive_display_name(drive: dict[str, Any]) -> str:
    """Return a concise manufacturer and model description."""

    manufacturer = clean(
        drive.get("manufacturer"),
        "",
    )

    model = (
        clean(drive.get("model"), "")
        or clean(drive.get("name"), "")
    )

    parts: list[str] = []

    if (
        manufacturer
        and manufacturer.lower() not in model.lower()
        and manufacturer.lower() not in {
            "standard disk drives",
            "(standard disk drives)",
        }
    ):
        parts.append(manufacturer)

    if model:
        parts.append(model)

    return " ".join(parts) or "Model not reported"


def storage_health_text(drive: dict[str, Any]) -> str:
    """Return a concise health description."""

    health = clean(
        drive.get("health"),
        "Unknown",
    )

    status = clean(
        drive.get("operational_status"),
        "",
    )

    if health.lower() == "healthy":
        return "Drive health: HEALTHY"

    if health.lower() in {"unhealthy", "warning"}:
        return f"Drive health: {health.upper()}"

    if status.lower() in {"ok", "healthy"}:
        return f"Drive health: {status.upper()}"

    return f"Drive health: {health}"


# ------------------------------------------------------------
# Graphics
# ------------------------------------------------------------

def collect_graphics() -> list[dict[str, Any]]:
    """Collect installed display adapters."""

    adapters = ensure_list(
        run_powershell_json(
            r"""
Get-CimInstance -ClassName Win32_VideoController |
    Select-Object `
        Name,
        AdapterCompatibility,
        AdapterRAM,
        DriverVersion,
        VideoProcessor,
        CurrentHorizontalResolution,
        CurrentVerticalResolution
"""
        )
    )

    graphics: list[dict[str, Any]] = []

    for adapter in adapters:
        name = clean(adapter.get("Name"), "")

        if not name:
            continue

        graphics.append({
            "name": name,
            "manufacturer": clean(
                adapter.get("AdapterCompatibility"),
                "",
            ),
            "memory_bytes": integer_value(
                adapter.get("AdapterRAM")
            ),
            "memory": format_video_memory(
                adapter.get("AdapterRAM")
            ),
            "driver_version": clean(
                adapter.get("DriverVersion"),
                "",
            ),
            "processor": clean(
                adapter.get("VideoProcessor"),
                "",
            ),
        })

    return graphics


def format_video_memory(value: Any) -> str:
    """Format video memory when Windows reports it reliably."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""

    if number <= 0:
        return ""

    gib = number / (1024 ** 3)

    if gib >= 1:
        return f"{gib:.0f} GB video memory"

    mib = number / (1024 ** 2)
    return f"{mib:.0f} MB video memory"


def classify_graphics(
    adapters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify integrated and dedicated graphics."""

    integrated: list[dict[str, Any]] = []
    dedicated: list[dict[str, Any]] = []
    software: list[dict[str, Any]] = []

    software_terms = (
        "microsoft basic display",
        "remote display",
        "virtual display",
        "indirect display",
    )

    integrated_terms = (
        "intel(r) hd graphics",
        "intel(r) uhd graphics",
        "intel(r) iris",
        "intel hd graphics",
        "intel uhd graphics",
        "intel iris",
        "radeon graphics",
    )

    for adapter in adapters:
        name = adapter["name"].lower()

        if any(term in name for term in software_terms):
            software.append(adapter)

        elif any(term in name for term in integrated_terms):
            integrated.append(adapter)

        else:
            dedicated.append(adapter)

    return {
        "integrated": integrated,
        "dedicated": dedicated,
        "software": software,
    }


# ------------------------------------------------------------
# Networking
# ------------------------------------------------------------

def collect_networking() -> list[dict[str, Any]]:
    """Collect physical Ethernet and Wi-Fi adapters."""

    adapters = ensure_list(
        run_powershell_json(
            r"""
Get-CimInstance -ClassName Win32_NetworkAdapter |
    Where-Object {
        $_.PhysicalAdapter -eq $true -and
        $_.Name -notmatch "Bluetooth"
    } |
    Select-Object `
        Name,
        Manufacturer,
        AdapterType,
        NetEnabled,
        Speed,
        MACAddress
"""
        )
    )

    results: list[dict[str, Any]] = []

    for adapter in adapters:
        name = clean(adapter.get("Name"), "")

        if not name:
            continue

        results.append({
            "name": name,
            "manufacturer": clean(
                adapter.get("Manufacturer"),
                "",
            ),
            "adapter_type": clean(
                adapter.get("AdapterType"),
                "",
            ),
            "enabled": adapter.get("NetEnabled"),
            "speed": format_network_speed(
                adapter.get("Speed")
            ),
        })

    return results


def format_network_speed(value: Any) -> str:
    """Format a network adapter speed."""

    try:
        bits_per_second = int(value)
    except (TypeError, ValueError):
        return ""

    if bits_per_second >= 1_000_000_000:
        return (
            f"{bits_per_second / 1_000_000_000:g} Gbps"
        )

    if bits_per_second >= 1_000_000:
        return (
            f"{bits_per_second / 1_000_000:g} Mbps"
        )

    return f"{bits_per_second} bps"


# ------------------------------------------------------------
# Report collection
# ------------------------------------------------------------

def collect_report() -> dict[str, Any]:
    """Collect all buyer-facing system information."""

    return {
        "system": collect_system(),
        "cpu": collect_cpu(),
        "memory": collect_memory(),
        "storage": collect_storage(),
        "graphics": collect_graphics(),
        "networking": collect_networking(),
        "inspection": {
            "administrator": is_administrator(),
            "python_version": platform.python_version(),
        },
    }


# ------------------------------------------------------------
# Report rendering
# ------------------------------------------------------------

def print_rule(character: str = "-") -> None:
    """Print a horizontal divider."""

    print(character * REPORT_WIDTH)


def print_section(title: str) -> None:
    """Print a section title."""

    print()
    print(title)
    print_rule()


def marketplace_title(system: dict[str, Any]) -> str:
    """Build a clean manufacturer/model title."""

    manufacturer = clean(
        system.get("manufacturer"),
        "",
    )

    model = clean(
        system.get("model"),
        "",
    )

    if (
        manufacturer
        and model
        and manufacturer.lower() not in model.lower()
    ):
        return f"{manufacturer} {model}"

    return model or manufacturer or "Unknown computer"


def print_marketplace_report(
    report: dict[str, Any],
) -> None:
    """Print the concise buyer-friendly report."""

    system = report["system"]
    cpu = report["cpu"]
    memory = report["memory"]
    storage = report["storage"]
    graphics = report["graphics"]
    networking = report["networking"]

    print_rule("=")
    print("COMP SPEC — USED COMPUTER INSPECTION")
    print_rule("=")
    print()
    print(marketplace_title(system))

    system_details = []

    if system.get("bios_version"):
        system_details.append(
            f"BIOS {system['bios_version']}"
        )

    if system.get("bios_date"):
        system_details.append(
            system["bios_date"]
        )

    if system_details:
        print(" | ".join(system_details))

    if system.get("windows_name"):
        windows_text = system["windows_name"]

        if system.get("windows_build"):
            windows_text += (
                f" — build {system['windows_build']}"
            )

        print(windows_text)

    # CPU
    print_section("CPU")
    print(clean(cpu.get("model")))

    topology = []

    if cpu.get("physical_cores") is not None:
        topology.append(
            f"{cpu['physical_cores']} cores"
        )

    if cpu.get("logical_processors") is not None:
        topology.append(
            f"{cpu['logical_processors']} threads"
        )

    if topology:
        print(" / ".join(topology))

    maximum_clock = format_cpu_clock(
        cpu.get("max_mhz")
    )

    if maximum_clock:
        print(maximum_clock)

    # Memory
    print_section("MEMORY")

    print(
        f"{memory['total']} "
        f"{common_memory_type(memory['installed'])}"
    )

    print(
        describe_memory_modules(
            memory["installed"]
        )
    )

    speed = common_memory_speed(
        memory["installed"]
    )

    if speed:
        print(speed)

    print(memory["channel_mode"].capitalize())

    if memory["slot_count"]:
        print(
            f"{memory['populated_count']} of "
            f"{memory['slot_count']} slots populated"
        )

    # Storage
    print_section("STORAGE")

    if not storage:
        print("No physical drives reported")

    for index, drive in enumerate(storage, start=1):
        if len(storage) > 1:
            print(f"Drive {index}")

        print(
            f"{drive['capacity']} "
            f"{drive['kind']}"
        )

        print(drive_display_name(drive))
        print(storage_health_text(drive))

        if drive.get("bus_type"):
            print(
                f"Interface: {drive['bus_type']}"
            )

        if index < len(storage):
            print()

    # Graphics
    print_section("GRAPHICS")

    classification = classify_graphics(graphics)
    dedicated = classification["dedicated"]
    integrated = classification["integrated"]

    if dedicated:
        for adapter in dedicated:
            print(adapter["name"])

            if adapter.get("memory"):
                print(adapter["memory"])

        if integrated:
            print()
            print("Also includes integrated graphics:")

            for adapter in integrated:
                print(adapter["name"])

    elif integrated:
        for adapter in integrated:
            print(adapter["name"])

        print("Integrated graphics only")
        print("No dedicated gaming GPU detected")

    elif graphics:
        for adapter in graphics:
            print(adapter["name"])

        print("No dedicated gaming GPU identified")

    else:
        print("No graphics adapter information reported")

    # Networking
    print_section("NETWORKING")

    if networking:
        for adapter in networking:
            print(adapter["name"])

            details = []

            if adapter.get("speed"):
                details.append(adapter["speed"])

            if adapter.get("enabled") is True:
                details.append("enabled")

            if details:
                print(" | ".join(details))
    else:
        print("No physical network adapters reported")

    # Inspection
    print_section("INSPECTION")

    if cpu.get("model"):
        print("✓ CPU detected")
    else:
        print("⚠ CPU information incomplete")

    if memory["populated_count"]:
        print(
            f"✓ {memory['total']} memory recognized"
        )
    else:
        print("⚠ No installed memory reported")

    if memory["empty_count"] > 0:
        print(
            f"✓ {memory['empty_count']} empty memory "
            f"slot{'' if memory['empty_count'] == 1 else 's'}"
        )

    if storage:
        print(
            f"✓ {len(storage)} physical drive"
            f"{'' if len(storage) == 1 else 's'} detected"
        )

        healthy = [
            drive for drive in storage
            if str(drive.get("health", "")).lower()
            in {"healthy", "ok"}
        ]

        unhealthy = [
            drive for drive in storage
            if str(drive.get("health", "")).lower()
            in {"unhealthy", "warning", "failed"}
        ]

        if unhealthy:
            print("⚠ A drive health warning was reported")
        elif len(healthy) == len(storage):
            print("✓ Windows reports all drives healthy")
        else:
            print("⚠ Some drive health data was unavailable")
    else:
        print("⚠ No physical storage reported")

    if not report["inspection"]["administrator"]:
        print(
            "⚠ Not running as administrator; "
            "some information may be incomplete"
        )

    # Summary
    print()
    print_rule("=")
    print("SUMMARY")
    print_rule("=")

    summary_items = [
        marketplace_title(system),
        clean(cpu.get("model"), "Unknown CPU"),
        (
            f"{memory['total']} "
            f"{common_memory_type(memory['installed'])}"
        ),
    ]

    summary_items.extend(
        f"{drive['capacity']} {drive['kind']}"
        for drive in storage
    )

    if dedicated:
        summary_items.append(
            dedicated[0]["name"]
        )
    elif integrated:
        summary_items.append(
            f"{integrated[0]['name']} — integrated graphics"
        )

    for item in summary_items:
        print(f"• {item}")

    print()
    print(
        "Hardware information was read directly from Windows. "
        "Drive health reflects the state reported by Windows "
        "at the time of inspection."
    )

    print_rule("=")


def print_summary(report: dict[str, Any]) -> None:
    """Print one compact summary line."""

    system = report["system"]
    cpu = report["cpu"]
    memory = report["memory"]
    storage = report["storage"]
    graphics = report["graphics"]

    classification = classify_graphics(graphics)

    parts = [
        marketplace_title(system),
        clean(cpu.get("model"), "Unknown CPU"),
        (
            f"{memory['total']} "
            f"{common_memory_type(memory['installed'])}"
        ),
    ]

    parts.extend(
        f"{drive['capacity']} {drive['kind']}"
        for drive in storage
    )

    if classification["dedicated"]:
        parts.append(
            classification["dedicated"][0]["name"]
        )
    elif classification["integrated"]:
        parts.append(
            classification["integrated"][0]["name"]
        )

    print(" | ".join(parts))


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description=(
            "Create a buyer-friendly Windows computer "
            "inspection report"
        )
    )

    parser.add_argument(
        "--summary",
        action="store_true",
        help="print a compact one-line summary",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON",
    )

    return parser.parse_args()


def verify_environment() -> None:
    """Verify that the script is running on Windows."""

    global POWERSHELL

    if platform.system() != "Windows":
        print(
            "This version of Comp Spec is designed for Windows."
        )
        sys.exit(1)

    try:
        POWERSHELL = find_powershell()
    except RuntimeError as error:
        print(error)
        sys.exit(1)


def main() -> None:
    """Collect and display the Windows inspection report."""

    args = parse_arguments()
    verify_environment()

    try:
        report = collect_report()

    except RuntimeError as error:
        print(f"Unable to inspect this computer: {error}")
        sys.exit(1)

    if args.json:
        print(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if args.summary:
        print_summary(report)
        return

    print_marketplace_report(report)


if __name__ == "__main__":
    main()