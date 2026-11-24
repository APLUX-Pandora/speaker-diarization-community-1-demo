"""Install the paired prebuilt library; never compile the model engine at pip install."""
import hashlib
import json
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.errors import SetupError
from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent


class BuildSDK(build_py):
    def run(self):
        pairing = json.loads((ROOT / "native-library.json").read_text())
        library = ROOT / "lib" / pairing["filename"]
        if not library.is_file():
            raise SetupError("Missing matched lib/libspeaker_diarization.so")
        payload = library.read_bytes()
        if payload[:4] != b"\x7fELF" or int.from_bytes(payload[18:20], "little") != 183:
            raise SetupError("The SDK payload must be a Linux AArch64 ELF")
        if hashlib.sha256(payload).hexdigest() != pairing["sha256"]:
            raise SetupError("Native SDK SHA256 does not match native-library.json")
        notices = ("LICENSE", "THIRD_PARTY_NOTICES.txt", "LICENSES")
        for name in notices:
            if not (ROOT / "lib" / name).exists():
                raise SetupError("Missing native SDK licensing: " + name)
        super().run()
        target = Path(self.build_lib) / "pandora_speaker_diarization_community_1"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(library, target / library.name)
        shutil.copytree(ROOT / "lib" / "qnn248", target / "qnn248", dirs_exist_ok=True)
        shutil.copy2(ROOT / "native-library.json", target / "native-library.json")
        for name in notices:
            source = ROOT / "lib" / name
            if source.is_dir():
                shutil.copytree(source, target / name, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target / name)


class LinuxAArch64Wheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False
        self.plat_name = "linux_aarch64"

    def get_tag(self):
        return "py3", "none", "linux_aarch64"


setup(cmdclass={"build_py": BuildSDK, "bdist_wheel": LinuxAArch64Wheel})
