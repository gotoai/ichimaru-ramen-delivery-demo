#!/usr/bin/env python3
"""
bash_tool.py — run model-generated shell commands inside a disposable sandbox.

Adapted from the test_langgraph reference for the Ichimaru analytics chatbot. The model
is expected to answer area-manager questions by querying the s10_analysis layer with
DuckDB, so the sandbox additionally mounts a vendored DuckDB CLI binary on PATH.

Design premise: the command is LLM-generated and treated as ADVERSARIAL. Safety comes
from OS-level isolation, not from inspecting the command or asking a human:

  * bubblewrap (`bwrap`) puts every command in fresh namespaces with NO access to the
    host filesystem. `rm -rf /` only wipes a throwaway tmpfs.
  * The only real host paths are a curated read-only list (DATA/, docs/, the duckdb
    binary). Nothing is writable outside an ephemeral /work + /tmp that vanish per call.
  * Network is blocked with a seccomp filter that rejects inet/inet6/packet socket()
    calls (we don't unshare the net namespace — on Ubuntu 24.04 the AppArmor userns
    mitigation denies CAP_NET_ADMIN inside the sandbox, so bwrap can't bring up loopback;
    seccomp needs no capabilities and still stops all networking).
  * ulimit caps (memory, processes, CPU, file size) + a wall-clock timeout stop fork
    bombs, memory bombs, and runaway loops.

Because the blast radius is "a container we immediately discard," no per-command user
approval is needed.
"""

import os
import struct
import subprocess
from pathlib import Path

# agent-service/agent/tools/bash_tool.py -> parents[2] = agent-service/
_AGENT_SERVICE_ROOT = Path(__file__).resolve().parents[2]


def _find_repo_root(start: Path) -> Path:
    """Walk up to the repo root (the dir holding both DATA/ and docs/)."""
    for p in [start, *start.parents]:
        if (p / "DATA").is_dir() and (p / "docs").is_dir():
            return p
    return _AGENT_SERVICE_ROOT.parent  # best-effort fallback


_REPO_ROOT = _find_repo_root(_AGENT_SERVICE_ROOT)
_DUCKDB_BIN = _AGENT_SERVICE_ROOT / ".tools" / "duckdb"

# Host directories exposed READ-ONLY inside the sandbox, remapped under /data (so the
# host's real paths are never revealed). Keep minimal — everything here is readable by a
# potentially-adversarial command.  {host path: sandbox path}
READONLY_MOUNTS = {
    str(_REPO_ROOT / "DATA"): "/data/DATA",
    str(_REPO_ROOT / "docs"): "/data/docs",
}

# Resource limits (defense against runaway / malicious commands).
TIMEOUT_SEC = 30          # wall-clock kill for the whole bwrap invocation
CPU_SEC = 25              # ulimit -t: CPU seconds
MEM_LIMIT_KB = 4_000_000  # ulimit -v: address space (~4GB; room for DuckDB)
MAX_PROCS = 256           # ulimit -u: processes (fork-bomb guard)
MAX_FILE_KB = 200_000     # ulimit -f: max file size a command may write (~200MB)
MAX_OUTPUT_CHARS = 8_000  # truncate output returned to the model


def _seccomp_filter_bytes():
    """A compiled cBPF seccomp program (x86_64) that blocks network sockets.

    Rejects socket(AF_INET/AF_INET6/AF_PACKET, ...) with EPERM; everything else
    (including AF_UNIX and AF_NETLINK, needed by libc) is allowed. Layout matches
    struct seccomp_data (nr@0, arch@4, args[0]@16).
    """
    AUDIT_ARCH_X86_64 = 0xC000003E
    LD_ABS_W = 0x20
    JEQ = 0x15
    RET = 0x06
    ALLOW = 0x7FFF0000
    ERRNO_EPERM = 0x00050001
    SYS_socket = 41
    AF_INET, AF_INET6, AF_PACKET = 2, 10, 17
    prog = [
        (LD_ABS_W, 0, 0, 4),                 # 0: A = arch
        (JEQ, 0, 6, AUDIT_ARCH_X86_64),      # 1: if arch != x86_64 -> ALLOW(8)
        (LD_ABS_W, 0, 0, 0),                 # 2: A = syscall nr
        (JEQ, 0, 4, SYS_socket),             # 3: if nr != socket -> ALLOW(8)
        (LD_ABS_W, 0, 0, 16),                # 4: A = args[0] (domain)
        (JEQ, 3, 0, AF_INET),                # 5: AF_INET   -> BLOCK(9)
        (JEQ, 2, 0, AF_INET6),               # 6: AF_INET6  -> BLOCK(9)
        (JEQ, 1, 0, AF_PACKET),              # 7: AF_PACKET -> BLOCK(9) else ALLOW(8)
        (RET, 0, 0, ALLOW),                  # 8: ALLOW
        (RET, 0, 0, ERRNO_EPERM),            # 9: BLOCK
    ]
    return b"".join(struct.pack("<HBBI", *ins) for ins in prog)


def _bwrap_argv(seccomp_fd):
    """Build the locked-down bubblewrap command prefix."""
    argv = [
        "bwrap",
        "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--unshare-cgroup",
        "--seccomp", str(seccomp_fd),  # block inet/inet6/packet sockets
        "--die-with-parent",
        "--new-session",               # own session -> blocks TIOCSTI terminal injection
        "--clearenv",
        "--setenv", "PATH", "/opt/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LC_ALL", "C.UTF-8",   # UTF-8 so DuckDB prints Japanese cleanly
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/sbin", "/sbin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", "/work",
        "--chdir", "/work",
    ]
    # Vendored DuckDB CLI on PATH (the analytics query engine). Mount under /opt/bin,
    # which lives on the sandbox's writable tmpfs root — /usr/local/bin would sit under
    # the read-only /usr bind and can't host a new mount point.
    if _DUCKDB_BIN.is_file():
        argv += ["--dir", "/opt/bin", "--ro-bind", str(_DUCKDB_BIN), "/opt/bin/duckdb"]
    for host_path, sandbox_path in READONLY_MOUNTS.items():
        if os.path.isdir(host_path):
            argv += ["--ro-bind", host_path, sandbox_path]
    return argv


def run_bash(command: str) -> str:
    """Execute `command` in the sandbox and return combined stdout+stderr + exit code.

    Never raises for command failures — the exit code and output are returned as text so
    the model can read the result. Only unexpected launcher failures surface as a
    bracketed error string.
    """
    limits = (
        f"ulimit -t {CPU_SEC}; "
        f"ulimit -v {MEM_LIMIT_KB}; "
        f"ulimit -u {MAX_PROCS}; "
        f"ulimit -f {MAX_FILE_KB}; "
    )

    seccomp_fd = os.memfd_create("seccomp_filter", 0)
    os.write(seccomp_fd, _seccomp_filter_bytes())
    os.lseek(seccomp_fd, 0, os.SEEK_SET)

    argv = _bwrap_argv(seccomp_fd) + ["--", "/bin/bash", "-c", limits + command]

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=TIMEOUT_SEC,
            check=False, pass_fds=(seccomp_fd,),
        )
    except subprocess.TimeoutExpired:
        return f"[timed out after {TIMEOUT_SEC}s and was killed]"
    except FileNotFoundError:
        return "[bash tool unavailable: bubblewrap (bwrap) is not installed]"
    finally:
        os.close(seccomp_fd)

    output = (proc.stdout or "") + (proc.stderr or "")
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f"\n[... output truncated at {MAX_OUTPUT_CHARS} chars]"
    return f"(exit code {proc.returncode})\n{output}".rstrip()


if __name__ == "__main__":
    import sys
    cmd = " ".join(sys.argv[1:]) or "echo hello from the sandbox; duckdb --version; ls /data/DATA/s10_analysis"
    print(run_bash(cmd))
