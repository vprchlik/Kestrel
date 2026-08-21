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
 *  - Between ifup and that datagram, one RTM_SETNEIGHTBL over
 *    AF_NETLINK shortens eth0's ARP retransmit from 1 s to 50 ms
 *    (D-0075). It sits on the measured path, so its cost is stamped
 *    (`neigh`) rather than assumed.
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
#include <errno.h>
#include <linux/neighbour.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
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

/* D-0075. net/core/neighbour.c __neigh_event_send() arms the ARP
 * retransmit at max(NEIGH_VAR(parms, RETRANS_TIME), HZ/100); arp_tbl's
 * default is 1*HZ. At CONFIG_HZ=250 that is 250 jiffies, which
 * kernel/time/timer.c places in wheel level 1 (8-jiffy granularity),
 * so it fires 1004-1032 ms later -- past slirp's ~1 s ARP-pending
 * drop. 50 ms is msecs_to_jiffies(50) = 13 jiffies, which lands in
 * level 0 (1-jiffy granularity) and fires at 14 jiffies = 56 ms. It
 * is also well clear of the HZ/100 = 8 ms floor, so the number still
 * means what it says. MCAST_PROBES 20 keeps ~1.04 s of total
 * resolution budget at the shorter interval (default 3 x 1 s). */
#define NEIGH_RETRANS_MS   50ULL
#define NEIGH_MCAST_PROBES 20U

enum stamp {
    T_LISTEN, T_IFUP, T_NEIGH, T_ANNOUNCE, T_READY, T_ACCEPT, T_READ,
    T_RESP, T_N
};
static const char *const STAMP_NAME[T_N] = {
    "listen", "ifup", "neigh", "announce", "ready", "accept", "read",
    "response",
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

static void die_num(const char *what, long long v)
{
    dprintf(2, "INIT FAIL: %s (%lld)\n", what, v);
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

/* One netlink attribute into buf at off; returns the next offset.
 * Overflow dies rather than truncating: buf is sized for exactly the
 * one message below, so a short buffer is a bug, not a condition. */
static size_t nla_append(unsigned char *buf, size_t off, size_t cap,
                         unsigned short type, const void *val, size_t len)
{
    struct nlattr a;
    size_t need = NLA_ALIGN(sizeof(a) + len);

    if (off + need > cap)
        die_num("netlink buffer too small for attribute", (long long)type);
    a.nla_len = (unsigned short)(sizeof(a) + len);
    a.nla_type = type;
    memcpy(buf + off, &a, sizeof(a));
    memcpy(buf + off + sizeof(a), val, len);
    memset(buf + off + sizeof(a) + len, 0, need - sizeof(a) - len);
    return off + need;
}

/* RTM_SETNEIGHTBL on arp_cache, eth0's parameters (D-0075).
 *
 * CONFIG_PROC_FS is off, so the /proc/sys/net/ipv4/neigh/eth0 knobs
 * do not exist and sysctl(2) is long gone: netlink is the only route.
 * NDTPA_IFINDEX is not optional. neigh_parms_alloc() kmemdups
 * tbl->parms when the netdev registers, so eth0's parameters are a
 * private snapshot; writing the table default (ifindex 0) would set
 * a copy nothing reads. eth0's IPv4 parms exist once it has an
 * address, hence after ifup().
 *
 * The ifindex comes from SIOCGIFINDEX on the AF_INET socket we
 * already hold, not from if_nametoindex(3): musl's implementation
 * opens an AF_UNIX socket to carry the same ioctl, and this Image
 * has `# CONFIG_UNIX is not set`, so it returns 0 on every boot. */
static void neigh_fast_retrans(int ctl)
{
    static const char TBL[] = "arp_cache";
    unsigned char buf[128] __attribute__((aligned(4)));
    struct sockaddr_nl kern;
    struct nlmsghdr nlh;
    struct nlmsgerr nle;
    struct ndtmsg ndt;
    struct nlattr parms;
    struct ifreq ifr;
    unsigned long long retrans_ms = NEIGH_RETRANS_MS;
    unsigned int u32;
    size_t off, parms_off;
    ssize_t n;
    int fd;

    memset(&ifr, 0, sizeof(ifr));
    strcpy(ifr.ifr_name, "eth0");
    if (ioctl(ctl, SIOCGIFINDEX, &ifr) != 0)
        die("SIOCGIFINDEX eth0");
    u32 = (unsigned int)ifr.ifr_ifindex;

    memset(buf, 0, sizeof(buf));
    off = NLMSG_HDRLEN; /* nlmsghdr is written last: it carries the length */

    memset(&ndt, 0, sizeof(ndt));
    ndt.ndtm_family = AF_INET; /* picks arp_tbl before the name compare */
    memcpy(buf + off, &ndt, sizeof(ndt));
    off += NLMSG_ALIGN(sizeof(ndt));

    off = nla_append(buf, off, sizeof(buf), NDTA_NAME, TBL, sizeof(TBL));

    parms_off = off;
    off += NLA_HDRLEN; /* NDTA_PARMS header; its length is known at the end */
    off = nla_append(buf, off, sizeof(buf), NDTPA_IFINDEX, &u32, sizeof(u32));
    off = nla_append(buf, off, sizeof(buf), NDTPA_RETRANS_TIME,
                     &retrans_ms, sizeof(retrans_ms)); /* u64 msecs */
    u32 = NEIGH_MCAST_PROBES;
    off = nla_append(buf, off, sizeof(buf), NDTPA_MCAST_PROBES,
                     &u32, sizeof(u32));
    parms.nla_len = (unsigned short)(off - parms_off);
    parms.nla_type = NDTA_PARMS | NLA_F_NESTED;
    memcpy(buf + parms_off, &parms, sizeof(parms));

    memset(&nlh, 0, sizeof(nlh));
    nlh.nlmsg_len = (unsigned int)off;
    nlh.nlmsg_type = RTM_SETNEIGHTBL;
    nlh.nlmsg_flags = NLM_F_REQUEST | NLM_F_ACK;
    nlh.nlmsg_seq = 1;
    memcpy(buf, &nlh, sizeof(nlh));

    fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_ROUTE);
    if (fd < 0)
        die_num("socket netlink route", errno);
    memset(&kern, 0, sizeof(kern));
    kern.nl_family = AF_NETLINK;
    n = sendto(fd, buf, off, 0, (struct sockaddr *)&kern, sizeof(kern));
    if (n != (ssize_t)off)
        die_num("RTM_SETNEIGHTBL sendto", (long long)(n < 0 ? -errno : n));

    /* NLM_F_ACK: the kernel always answers, so a rejected request is
     * a loud death here rather than a silent 1 s retransmit later. */
    n = recv(fd, buf, sizeof(buf), 0);
    if (n < (ssize_t)(NLMSG_HDRLEN + sizeof(nle)))
        die_num("RTM_SETNEIGHTBL ack short", (long long)(n < 0 ? -errno : n));
    memcpy(&nlh, buf, sizeof(nlh));
    if (nlh.nlmsg_type != NLMSG_ERROR)
        die_num("RTM_SETNEIGHTBL ack type", (long long)nlh.nlmsg_type);
    memcpy(&nle, buf + NLMSG_HDRLEN, sizeof(nle));
    if (nle.error != 0)
        die_num("RTM_SETNEIGHTBL rejected", (long long)nle.error);
    if (close(fd) != 0)
        die_num("close netlink", errno);
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

    /* 3. Shorten the ARP retransmit (D-0075). After ifup, because
     * eth0's arp_parms follow its IPv4 in_device; before the
     * announce, because that datagram is what forces the solicit. */
    neigh_fast_retrans(ctl);
    stamp_ns[T_NEIGH] = mono_ns();

    /* 4. Announce — confound A. First wire TX is the invariant,
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

    /* 5. Gate marker. The one console write on the measured path. */
    out(1, "READY\n");
    stamp_ns[T_READY] = mono_ns();

    /* 6. One connection, one read, the 92 bytes, close. */
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
