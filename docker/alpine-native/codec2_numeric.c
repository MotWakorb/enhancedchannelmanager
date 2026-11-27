/* ECM-owned wrappers. Arm scalar kernels are separately pinned, MIT-selected.
 * These symbols stay hidden inside Codec2; this is not a libc ABI replacement.
 */
#include <math.h>

extern void codec2_sincosf(float, float *, float *);

float codec2_sinf(float x) {
    float s, c;
    codec2_sincosf(x, &s, &c);
    return s;
}

float codec2_cosf(float x) {
    float s, c;
    codec2_sincosf(x, &s, &c);
    return c;
}

float codec2_atan2f(float y, float x) {
    return (float)atan2((double)y, (double)x);
}

float codec2_log10f(float x) {
    return (float)log10((double)x);
}
