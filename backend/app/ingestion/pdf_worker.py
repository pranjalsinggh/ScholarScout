"""Private native-parser subprocess entry point; no application/database imports."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys

_job_handle = None


def _limit_resources(memory_mb: int, seconds: float) -> None:
    memory = memory_mb * 1024 * 1024
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, math.ceil(seconds)), max(2, math.ceil(seconds) + 1)))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        job = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.ProcessMemoryLimit = memory
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise RuntimeError("PDF process memory limit unavailable")
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise RuntimeError("PDF process memory limit unavailable")
        global _job_handle
        _job_handle = job  # retain the job object throughout parsing
        return
    raise RuntimeError("PDF resource isolation is unavailable on this platform")


def main() -> int:
    path, output, encoded_limits, source_name = sys.argv[1:5]
    limits = json.loads(encoded_limits)
    try:
        _limit_resources(int(limits["memory_mb"]), float(limits["seconds"]))
        # -I ignores caller-controlled PYTHONPATH. Only this repository root
        # is used to load the parser; provider config and DB remain unimported.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from dataclasses import asdict
        from app.ingestion.pdf_extractor import _extract_pdf_in_process
        document = _extract_pdf_in_process(path, source_name=source_name,
                                          max_pages=int(limits["pages"]),
                                          max_text_chars=int(limits["text"]))
        payload = asdict(document)
    except Exception as error:
        # Native errors can expose paths and source content; send fixed text.
        message = str(error) if isinstance(error, ValueError) and type(error).__name__ == "PDFLimitError" else (
            "This PDF could not be read within the resource budget. Try a shorter or simpler PDF.")
        payload = {"error": message}
    Path(output).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
