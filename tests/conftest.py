import importlib.util
import sys
import types


def _ensure_pyusb() -> None:
    if importlib.util.find_spec("usb.core") and importlib.util.find_spec("usb.util"):
        return

    usb_module = types.ModuleType("usb")
    core_module = types.ModuleType("usb.core")
    util_module = types.ModuleType("usb.util")

    class USBError(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.errno = kwargs.get("errno")

    def find(**kwargs):
        return None

    def find_descriptor(*args, **kwargs):
        return None

    def endpoint_direction(address: int) -> int:
        return address

    core_module.USBError = USBError
    core_module.find = find

    util_module.find_descriptor = find_descriptor
    util_module.endpoint_direction = endpoint_direction
    util_module.ENDPOINT_OUT = 0
    util_module.ENDPOINT_IN = 0

    usb_module.core = core_module
    usb_module.util = util_module

    sys.modules.setdefault("usb", usb_module)
    sys.modules.setdefault("usb.core", core_module)
    sys.modules.setdefault("usb.util", util_module)


def _ensure_crypto() -> None:
    try:
        from Crypto.Cipher import DES  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        crypto_module = types.ModuleType("Crypto")
        cipher_module = types.ModuleType("Crypto.Cipher")

        class _DummyCipher:
            def encrypt(self, data: bytes) -> bytes:
                return data

        class _DES:
            MODE_CBC = 1

            @staticmethod
            def new(key: bytes, mode: int, iv: bytes) -> _DummyCipher:  # type: ignore[name-defined]
                return _DummyCipher()

        cipher_module.DES = _DES
        crypto_module.Cipher = cipher_module

        sys.modules.setdefault("Crypto", crypto_module)
        sys.modules.setdefault("Crypto.Cipher", cipher_module)


def _ensure_pillow() -> None:
    try:
        from PIL import Image  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        pil_module = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")

        class _DummyImage:
            size = (480, 1920)

            def __init__(self, *args, **kwargs):
                pass

            def convert(self, mode: str) -> "_DummyImage":
                return self

            def crop(self, box):
                return self

            def paste(self, image, box):
                return None

            def save(self, buffer, format="PNG", optimize=True):
                return None

        def open(path):
            return _DummyImage()

        def new(mode, size, color):
            return _DummyImage()

        image_module.Image = _DummyImage
        image_module.open = open
        image_module.new = new

        pil_module.Image = image_module

        sys.modules.setdefault("PIL", pil_module)
        sys.modules.setdefault("PIL.Image", image_module)


def pytest_configure(config):
    _ensure_pyusb()
    _ensure_crypto()
    _ensure_pillow()
