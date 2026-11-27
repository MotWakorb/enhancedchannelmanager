/* Bounded append cases against the actual native library or failure-injected
 * source build. Large input is a valid, lazy, read-only virtual mapping.
 */
#define _GNU_SOURCE
#include <limits.h>
#include <speex/speex_bits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#ifdef ECM_SPEEX_REALLOC_FAILURE
#define OVERRIDE_SPEEX_REALLOC
static void *speex_realloc(void *ptr, int size) {
    (void)ptr;
    (void)size;
    return NULL;
}
#include "bits.c"
#endif

static void require(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "Speex capacity smoke failed: %s\n", message);
        exit(1);
    }
}

static void check_case(int owner, int bits_count, int consumed, const char *data,
                       int length, int expected_bits) {
    unsigned char buffer[16];
    SpeexBits bits;
    memset(buffer, 0x5a, sizeof(buffer));
    speex_bits_set_bit_buffer(&bits, buffer, 8);
    bits.nbBits = bits_count;
    bits.charPtr = consumed;
    bits.owner = owner;
    speex_bits_read_whole_bytes(&bits, data, length);
    require(bits.nbBits == expected_bits, "bounded bit count");
    require(bits.charPtr == 0, "consumed bytes flushed");
    for (int i = 8; i < 16; ++i) require(buffer[i] == 0x5a, "external buffer canary");
    int copied = expected_bits / 8 - (bits_count / 8 - consumed);
    if (copied > 0) require(memcmp(buffer + bits_count / 8 - consumed, data, (size_t)copied) == 0, "append payload bytes");
    /* No destroy: the failure-injection case deliberately uses a stack buffer
     * with owner=1 to exercise the unchanged realloc-failure branch. */
}

int main(void) {
    int owner = 0;
#ifdef ECM_SPEEX_REALLOC_FAILURE
    owner = 1;
#endif
    check_case(owner, 64, 0, "x", 1, 64);
    check_case(owner, 32, 0, "abcdefgh", 8, 56);
    check_case(owner, 64, 2, "x", 1, 48);
    check_case(owner, 64, 0, "x", -1, 64);
    /* All existing bytes consumed near INT_MAX: post-flush capacity must still
     * accept both bytes. Reserve virtual address space, not a huge input copy. */
    size_t capacity = (size_t)INT_MAX / 8 + 1;
    char *consumed = mmap(NULL, capacity + 1, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    require(consumed != MAP_FAILED, "valid lazy consumed buffer mapping");
    consumed[capacity] = 0x5a;
    SpeexBits boundary;
    speex_bits_init_buffer(&boundary, consumed, (int)capacity);
    boundary.nbBits = INT_MAX - 15;
    boundary.charPtr = boundary.nbBits / 8;
    speex_bits_read_whole_bytes(&boundary, "xy", 2);
    require(boundary.nbBits == 16 && boundary.charPtr == 0 && memcmp(consumed, "xy", 2) == 0,
            "consumed INT_MAX boundary preserves both payload bytes");
    require(consumed[capacity] == 0x5a, "consumed boundary canary");
    require(munmap(consumed, capacity + 1) == 0, "consumed buffer mapping cleanup");
    void *large = mmap(NULL, (size_t)INT_MAX, PROT_READ,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    require(large != MAP_FAILED, "valid lazy large input mapping");
    check_case(owner, 64, 0, large, INT_MAX, 64);
    require(munmap(large, (size_t)INT_MAX) == 0, "large input mapping cleanup");
    puts("SPEEX_CAPACITY_BOUNDARIES_PASS");
    return 0;
}
