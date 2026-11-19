/* Tiny PID 1 for the two throwaway test slots. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/io.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <unistd.h>

int main(void) {
    char cmdline[2048];
    if (mount("proc", "/proc", "proc", 0, NULL) != 0) return 1;
    FILE *input = fopen("/proc/cmdline", "r");
    if (!input || !fgets(cmdline, sizeof(cmdline), input)) return 1;
    fclose(input);
    if (strstr(cmdline, "fwc.slot=B fwc.trial=1") != NULL) {
        puts("FWC_TEST_FAILED_TRIAL_B");
        fflush(stdout);
        reboot(RB_AUTOBOOT);
    }
    if (strstr(cmdline, "fwc.slot=A fwc.trial=0") != NULL) {
        puts("FWC_TEST_CONFIRMED_A");
        fflush(stdout);
        if (ioperm(0xf4, 1, 1) == 0) outb(0x10, 0xf4);
    }
    pause();
    return 1;
}
