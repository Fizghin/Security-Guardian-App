"""
macOS camera permission.

macOS asks the user before a program may use the camera, and a program started from a
terminal is covered by the terminal app's permission (Terminal, iTerm, VS Code, ...).
OpenCV can only ask from the main thread, while Guardian opens cameras in background
threads, where every attempt fails and prints the same warnings again. So:

  * run_server.py asks on the main thread at startup if macOS never got an answer
    (request_camera_access); it does not wait, since cameras retry and pick up the answer, and
  * background threads check the answer first (local_camera_blocked) instead of trying.

The status is read straight from AVFoundation through the Objective-C runtime, so no extra
package is needed. On other systems, or if it can't be read, everything behaves as before.
"""
import ctypes
import ctypes.util
import platform

NOT_DETERMINED, RESTRICTED, DENIED, AUTHORIZED = 0, 1, 2, 3  # AVAuthorizationStatus

_reader = None  # callable returning the status; False once it is known to be unavailable


def _make_reader():
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
    avfoundation = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    # NSInteger +[AVCaptureDevice authorizationStatusForMediaType:(AVMediaType)type]
    send = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    device = objc.objc_getClass(b"AVCaptureDevice")
    selector = objc.sel_registerName(b"authorizationStatusForMediaType:")
    video = ctypes.c_void_p.in_dll(avfoundation, "AVMediaTypeVideo").value
    if not (device and selector and video):
        raise OSError("AVFoundation is not available")
    return lambda: int(send(device, selector, video))


def camera_permission() -> int | None:
    """macOS camera permission (one of the constants above), or None elsewhere or if unknown."""
    global _reader
    if platform.system() != "Darwin":
        return None
    if _reader is None:
        try:
            _reader = _make_reader()
        except Exception:
            _reader = False
    if not _reader:
        return None
    try:
        return _reader()
    except Exception:
        return None


def local_camera_blocked() -> str | None:
    """Why a local camera can't be opened right now, or None if it may be tried."""
    status = camera_permission()
    if status in (DENIED, RESTRICTED):
        return ("macOS is blocking the camera. In System Settings → Privacy & Security → Camera, turn on the app "
                "Guardian runs in (Terminal, iTerm, …), then restart Guardian.")
    if status == NOT_DETERMINED:
        return ("Click OK when macOS asks whether your terminal may use the camera. "
                "If no question appeared, restart Guardian.")
    return None


def request_camera_access() -> None:
    """On macOS, ask for camera access if it was never answered. Only the main thread can ask."""
    if camera_permission() == NOT_DETERMINED:
        import cv2

        cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION).release()  # OpenCV asks macOS and returns at once
