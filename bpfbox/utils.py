import os
import sys
import itertools
import signal
import subprocess

from bcc import syscall

# Mappings for syscall number to name, used in syscall_name()
__syscalls = {
    key: value.decode('utf-8') for key, value in syscall.syscalls.items()
}

# Mappings for syscall name to number, used in syscall_number()
__syscalls_reverse = {value: key for key, value in __syscalls.items()}

# Patch pread64 and pwrite64 into table
__syscalls_reverse['pread64'] = __syscalls_reverse['pread']
__syscalls_reverse['pwrite64'] = __syscalls_reverse['pwrite']


def syscall_number(name):
    """
    Convert a system call name to a number. Case insensitive.
    """
    try:
        return __syscalls_reverse[name.lower().strip()]
    except KeyError:
        return -1


def syscall_name(num):
    """
    Convert a system call number to a name.
    """
    try:
        return __syscalls[num]
    except KeyError:
        return '[unknown]'


def access_name(num):
    """
    Convert file access const to name.
    """
    from bpfbox.rules import AccessMode

    if num & AccessMode.MAY_READ:
        r = 'r'
    else:
        r = ''

    if num & AccessMode.MAY_WRITE:
        w = 'w'
    else:
        w = ''

    if num & AccessMode.MAY_APPEND:
        a = 'a'
    else:
        a = ''

    if num & AccessMode.MAY_EXEC:
        x = 'x'
    else:
        x = ''

    return ''.join([r, w, a, x])


def get_inode_and_device(path, follow_symlink=True):
    """
    Return (inode#, device#) tuple for path.
    """
    stat = os.stat(path) if follow_symlink else os.lstat(path)
    return (stat.st_ino, stat.st_dev)


def calculate_profile_key(path, follow_symlink=True):
    """
    Convert a path to a profile key using the same
    logic as bpf_program.c
    """
    st_ino, st_dev = get_inode_and_device(path, follow_symlink)
    return st_ino | (st_dev << 32)


def check_root():
    """
    Check for root privileges.
    """
    return os.geteuid() == 0


def drop_privileges(function):
    """
    Decorator to drop root privileges.
    """

    def inner(*args, **kwargs):
        # Get sudoer's UID
        try:
            sudo_uid = int(os.environ['SUDO_UID'])
        except (KeyError, ValueError):
            print("Could not get UID for sudoer", file=sys.stderr)
            return
        # Get sudoer's GID
        try:
            sudo_gid = int(os.environ['SUDO_GID'])
        except (KeyError, ValueError):
            print("Could not get GID for sudoer", file=sys.stderr)
            return
        # Make sure groups are reset
        try:
            os.setgroups([])
        except PermissionError:
            pass
        # Drop root
        os.setresgid(sudo_gid, sudo_gid, -1)
        os.setresuid(sudo_uid, sudo_uid, -1)
        # Execute function
        ret = function(*args, **kwargs)
        # Get root back
        os.setresgid(0, 0, -1)
        os.setresuid(0, 0, -1)
        return ret

    return inner


def read_chunks(f, size=1024):
    """
    Read a file in chunks.
    Default chunk size is 1024.
    """
    while 1:
        data = f.read(size)
        if not data:
            break
        yield data


def powerperm(ell):
    """
    Calculate powerset permutations.
    """
    s = list(ell)
    perms = itertools.chain.from_iterable(
        itertools.permutations(s, r) for r in range(1, len(s) + 1)
    )
    perms = map(lambda p: ''.join(list(p)), perms)
    return list(perms)


def which(binary):
    """
    Find a binary if it exists.
    """
    try:
        w = subprocess.Popen(
            ["which", binary], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        res = w.stdout.readlines()
        if len(res) == 0:
            raise Exception(f"{binary} not found")
        return os.path.realpath(res[0].strip())
    except Exception:
        if os.path.isfile(binary):
            return os.path.realpath(binary)
        else:
            raise Exception(f"{binary} not found")


@drop_privileges
def run_binary(args_str):
    """
    Drop privileges and run a binary if it exists.
    """
    # Wake up and do nothing on SIGCLHD
    signal.signal(signal.SIGUSR1, lambda x, y: None)
    # Reap zombies
    signal.signal(signal.SIGCHLD, lambda x, y: os.wait())
    args = args_str.split()
    try:
        binary = which(args[0])
    except Exception:
        return -1
    pid = os.fork()
    # Setup traced process
    if pid == 0:
        signal.pause()
        os.execvp(binary, args)
    # Return pid of traced process
    return pid
