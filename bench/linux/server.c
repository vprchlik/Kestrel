/* bench/linux/server.c — PID 1 is the server (D-0062 / T4.8).
 *
 * Order is load-bearing (D-0062 amendment):
 *  - socket/bind/listen BEFORE ifup: a SYN flushed at our first
 *    wire TX must meet LISTEN, never a pre-listen RST (RST gate).
 *  - One UDP datagram toward the gateway right after ifup. The
 *    invariant is the guest's FIRST WIRE TX, not ARP: with a cold
 *    ARP cache the kernel emits an ARP request for 10.0.2.2 first
 *    (Whimbrel's D-0046 shape); with a warm cache the datagram
 *    itself is the first frame. Either way slirp learns our MAC
 *    from that first frame and flushes the queued hostfwd SYN
 *    then, not on its retransmit grid (SYN-grid gate).
 *  - Stamps are captured in memory and printed after close
 *    (D-0068 discipline). loglevel gates printk, not userspace
 *    console writes, so printing earlier would be UART cost on
 *    the measured path. Only the 6-byte READY precedes the
 *    response.
 *
 * Static musl. No busybox, no shell, no malloc. Any failure prints
 * INIT FAIL naming the cause and parks: PID 1 must not return, and
 * a parked guest fails the trial loudly by timeout.
 */

/* clock_gettime, dprintf, struct ifreq, IFF_UP under strict -std=c11. */
#define _DEFAULT_SOURCE 1

#include <arpa/inet.h>
#include <net/if.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/reboot.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

/* Byte-identical to Whimbrel's app RESP (app/src/lib.rs, D-0053). */
static const char RESP[] =
    "HTTP/1.0 200 OK\r\n"
    "Content-Type: text/plain\r\n"
    "Connection: close\r\n"
    "Content-Length: 9\r\n"
    "\r\n"
    "whimbrel\n";
_Static_assert(sizeof(RESP) - 1 == 92, "response must be 92 bytes");

#define GUEST_IP 0x0A00020FU /* 10.0.2.15 (slirp guest)   */
#define GW_IP    0x0A000202U /* 10.0.2.2  (slirp gateway) */
#define NETMASK  0xFFFFFF00U /* /24                       */

enum stamp {
    T_LISTEN, T_IFUP, T_ANNOUNCE, T_READY, T_ACCEPT, T_READ, T_RESP, T_N
};
static const char *const STAMP_NAME[T_N] = {
    "listen", "ifup", "announce", "ready", "accept", "read", "response",
};
static unsigned long long stamp_ns[T_N];

static void out(int fd, const char *s)
{
    write(fd, s, strlen(s));
}

static void die(const char *what)
{
    out(2, "INIT FAIL: ");
    out(2, what);
    out(2, "\n");
    for (;;)
        pause();
}

static unsigned long long mono_ns(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        die("clock_gettime");
    return (unsigned long long)ts.tv_sec * 1000000000ull
         + (unsigned long long)ts.tv_nsec;
}

static void ifup(int ctl)
{
    struct ifreq ifr;
    struct sockaddr_in *sin = (struct sockaddr_in *)&ifr.ifr_addr;

    memset(&ifr, 0, sizeof(ifr));
    strcpy(ifr.ifr_name, "eth0");
    sin->sin_family = AF_INET;
    sin->sin_addr.s_addr = htonl(GUEST_IP);
    if (ioctl(ctl, SIOCSIFADDR, &ifr) != 0)
        die("SIOCSIFADDR eth0 10.0.2.15");
    sin->sin_addr.s_addr = htonl(NETMASK);
    if (ioctl(ctl, SIOCSIFNETMASK, &ifr) != 0)
        die("SIOCSIFNETMASK /24");
    if (ioctl(ctl, SIOCGIFFLAGS, &ifr) != 0)
        die("SIOCGIFFLAGS");
    ifr.ifr_flags |= IFF_UP;
    if (ioctl(ctl, SIOCSIFFLAGS, &ifr) != 0)
        die("SIOCSIFFLAGS up");
}

int main(void)
{
    /* The kernel opened /dev/console (cpio node) as fds 0-2. */

    /* 1. LISTEN first — confound B. */
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0)
        die("socket tcp");
    int one = 1;
    if (setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) != 0)
        die("SO_REUSEADDR");
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(80);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) != 0)
        die("bind :80");
    if (listen(srv, 1) != 0)
        die("listen");
    stamp_ns[T_LISTEN] = mono_ns();

    /* 2. Interface up. */
    int ctl = socket(AF_INET, SOCK_DGRAM, 0);
    if (ctl < 0)
        die("socket udp");
    ifup(ctl);
    stamp_ns[T_IFUP] = mono_ns();

    /* 3. Announce — confound A. First wire TX is the invariant,
     * not ARP: cold cache, the ARP request this datagram forces
     * goes out first; warm cache, the datagram itself is the
     * first frame. slirp learns our MAC from whichever frame
     * leaves first and flushes the queued hostfwd SYN. */
    struct sockaddr_in gw;
    memset(&gw, 0, sizeof(gw));
    gw.sin_family = AF_INET;
    gw.sin_port = htons(9); /* discard */
    gw.sin_addr.s_addr = htonl(GW_IP);
    static const char probe = 'w';
    if (sendto(ctl, &probe, 1, 0, (struct sockaddr *)&gw, sizeof(gw)) != 1)
        die("announce sendto 10.0.2.2");
    stamp_ns[T_ANNOUNCE] = mono_ns();

    /* 4. Gate marker. The one console write on the measured path. */
    out(1, "READY\n");
    stamp_ns[T_READY] = mono_ns();

    /* 5. One connection, one read, the 92 bytes, close. */
    int conn = accept(srv, NULL, NULL);
    if (conn < 0)
        die("accept");
    stamp_ns[T_ACCEPT] = mono_ns();

    char req[512];
    ssize_t got = read(conn, req, sizeof(req)); /* single read (D-0062) */
    if (got < 0)
        die("read");
    if (got == 0)
        die("peer closed before sending");
    stamp_ns[T_READ] = mono_ns();

    size_t off = 0;
    while (off < sizeof(RESP) - 1) {
        ssize_t put = write(conn, RESP + off, sizeof(RESP) - 1 - off);
        if (put <= 0)
            die("write response");
        off += (size_t)put;
    }
    stamp_ns[T_RESP] = mono_ns();
    if (close(conn) != 0)
        die("close");

    /* Off the measured path from here (D-0068 discipline). */
    for (int i = 0; i < T_N; i++)
        dprintf(1, "INIT %s mono_ns=%llu\n", STAMP_NAME[i], stamp_ns[i]);
    dprintf(1, "LINUX INIT OK\n");

    reboot(RB_POWER_OFF);
    die("reboot returned");
}
