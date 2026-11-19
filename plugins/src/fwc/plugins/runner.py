"""Trusted entry inside the private mount namespace. Load limits before user code."""

import ctypes
import errno
import resource
import runpy
import sys


def restrict_calls():
    library = ctypes.CDLL("libseccomp.so.2")
    pointer = ctypes.c_void_p
    integer = ctypes.c_int
    uint = ctypes.c_uint32
    library.seccomp_init.argtypes = [uint]
    library.seccomp_init.restype = pointer
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_rule_add.argtypes = [pointer, uint, integer, uint]
    library.seccomp_load.argtypes = [pointer]
    library.seccomp_release.argtypes = [pointer]
    library.seccomp_release.restype = None
    context = library.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise RuntimeError("could not initialize process limits")
    try:
        # The loader uses read-only paths. Results go to preopened, size-limited files.
        calls = """
            read write close lseek pread64 fstat newfstatat stat statx lstat
            open openat access faccessat faccessat2 readlink readlinkat getdents64
            fcntl ioctl mmap mmap2 mremap munmap mprotect brk madvise
            rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack
            futex futex_time64 clock_gettime clock_gettime64 gettimeofday
            clock_nanosleep clock_nanosleep_time64 nanosleep
            getpid gettid getuid geteuid getgid getegid getcwd getrandom
            getrlimit prlimit64 uname sched_yield exit exit_group
        """
        for name in calls.split():
            number = library.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and library.seccomp_rule_add(context, 0x7FFF0000, number, 0):
                raise RuntimeError(f"could not configure {name}")
        if library.seccomp_load(context):
            raise RuntimeError("could not apply process limits")
    finally:
        library.seccomp_release(context)


def main():
    memory, cpu = map(int, sys.argv[1:])
    for kind, limit in [
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_AS, memory * 1024**2),
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_FSIZE, 8192),
        (resource.RLIMIT_NOFILE, 24),
    ]:
        resource.setrlimit(kind, (limit, limit))
    sys.path.insert(0, "/sdk")
    restrict_calls()
    runpy.run_path("/work/main.py", run_name="__main__")


if __name__ == "__main__":
    main()
