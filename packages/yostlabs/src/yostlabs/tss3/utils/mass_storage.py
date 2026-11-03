"""
Given a USB VID/PID find the mounted volume / drive letter for that
removable disk.

Platform notes
--------------
Windows : requires `pywin32` and `WMI`   -> pip install pywin32 WMI
Others : not implemented yet

Usage
-----
    from find_removable_volume import find_removable_volumes

    mount = find_removable_volumes(
        vid="0483", pid="5740"
    )
"""

import sys


def find_removable_volumes(vid, pid):
    """
    vid, pid        : hex strings, e.g. "0483", "5740" (case-insensitive,
                      with or without a leading "0x")

    Returns all found mount points / drive letters as a list of strings, or None if not found.
    """
    vid = str(vid).lower().replace("0x", "")
    pid = str(pid).lower().replace("0x", "")

    if sys.platform.startswith("win"):
        return _find_windows(vid, pid)
    else:
        raise NotImplementedError(f"Unsupported platform: {sys.platform}")


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
#
# IMPORTANT: Win32_DiskDrive.PNPDeviceID is assigned by the USBSTOR class
# driver, e.g.:
#     USBSTOR\DISK&VEN_YOSTLABS&PROD_USB_MASS_STORAGE&REV_1.00\9&1E2F2FBF&0
# VEN_/PROD_/REV_ here ARE the SCSI Inquiry vendor/product/revision strings
# (useful for cross-checking), but the USB VID_/PID_ descriptor values live
# one level UP the device tree, on the parent node owned by the generic USB
# driver -- there is no WMI association that reaches it directly. We have to
# walk up via the Configuration Manager API (cfgmgr32.dll).
def _find_windows(vid, pid, max_ancestor_depth=10):
    import ctypes
    from ctypes import wintypes
    import wmi

    cfgmgr32 = ctypes.WinDLL("cfgmgr32")
    CR_SUCCESS = 0

    def locate_devnode(pnp_device_id):
        devinst = wintypes.DWORD()
        ret = cfgmgr32.CM_Locate_DevNodeW(
            ctypes.byref(devinst), pnp_device_id, 0
        )
        return devinst.value if ret == CR_SUCCESS else None

    def get_parent(devinst):
        parent = wintypes.DWORD()
        ret = cfgmgr32.CM_Get_Parent(ctypes.byref(parent), devinst, 0)
        return parent.value if ret == CR_SUCCESS else None

    def get_device_id(devinst):
        buf = ctypes.create_unicode_buffer(400)
        ret = cfgmgr32.CM_Get_Device_IDW(devinst, buf, 400, 0)
        return buf.value if ret == CR_SUCCESS else None

    target = f"VID_{vid.upper()}&PID_{pid.upper()}"
    c = wmi.WMI()

    disks = []

    for disk in c.Win32_DiskDrive():
        pnp_id = disk.PNPDeviceID
        if not pnp_id:
            continue

        devinst = locate_devnode(pnp_id)
        if devinst is None:
            continue

        # Walk up the device tree looking for the VID_/PID_ ancestor node
        found = False
        current = devinst
        for _ in range(max_ancestor_depth):
            parent = get_parent(current)
            if parent is None:
                break
            parent_id = get_device_id(parent)
            if parent_id and target in parent_id.upper():
                found = True
                break
            current = parent

        if not found:
            continue

        for partition in disk.associators("Win32_DiskDriveToDiskPartition"):
            for logical_disk in partition.associators("Win32_LogicalDiskToPartition"):
                disks.append(logical_disk.DeviceID)  # e.g. "E:"

    if len(disks) > 0:
        return disks

    return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vid", required=True, help="USB Vendor ID, e.g. 0483")
    parser.add_argument("--pid", required=True, help="USB Product ID, e.g. 5740")
    args = parser.parse_args()

    result = find_removable_volumes(
        args.vid, args.pid
    )
    
    if result:
        for mount_point in result:
            print(mount_point)
    else:
        print("No matching volume found.")