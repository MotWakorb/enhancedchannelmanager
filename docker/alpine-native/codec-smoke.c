/* Bounded real public-API operations, not FFmpeg or full media parity proof. */
#include <codec2/codec2.h>
#include <shine/layer3.h>
#include <twolame.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define REQUIRE(condition) do { if (!(condition)) { \
    fprintf(stderr, "codec smoke failed at line %d\n", __LINE__); exit(1); \
} } while (0)

static void save(FILE *file, const unsigned char *data, int size) {
    REQUIRE(size >= 0);
    if (size) {
        REQUIRE(data != NULL);
        REQUIRE(fwrite(data, 1, (size_t)size, file) == (size_t)size);
    }
}

int main(void) {
    short input[1152], decoded[320];
    unsigned char packed[16], mp2[16384];
    for (int i = 0; i < 1152; ++i) input[i] = (short)((i % 80 - 40) * 200);

    struct CODEC2 *encoder = codec2_create(CODEC2_MODE_3200);
    struct CODEC2 *decoder = codec2_create(CODEC2_MODE_3200);
    REQUIRE(encoder && decoder);
    int samples = codec2_samples_per_frame(encoder);
    int bytes = (codec2_bits_per_frame(encoder) + 7) / 8;
    REQUIRE(samples == 160 && bytes == 8);
    FILE *c2 = fopen("codec2.bin", "wb");
    FILE *pcm = fopen("codec2-decoded.raw", "wb");
    REQUIRE(c2 && pcm);
    for (int i = 0; i < 16; ++i) {
        codec2_encode(encoder, packed, input);
        codec2_decode(decoder, decoded, packed);
        save(c2, packed, bytes);
        REQUIRE(fwrite(decoded, sizeof(short), (size_t)samples, pcm) == (size_t)samples);
    }
    REQUIRE(fclose(c2) == 0 && fclose(pcm) == 0);
    codec2_destroy(encoder);
    codec2_destroy(decoder);

    twolame_options *two = twolame_init();
    REQUIRE(two);
    REQUIRE(twolame_set_num_channels(two, 1) == 0);
    REQUIRE(twolame_set_in_samplerate(two, 44100) == 0);
    REQUIRE(twolame_set_out_samplerate(two, 44100) == 0);
    REQUIRE(twolame_set_bitrate(two, 128) == 0);
    REQUIRE(twolame_init_params(two) == 0);
    FILE *mp2file = fopen("twolame.mp2", "wb");
    REQUIRE(mp2file);
    int two_bytes = 0;
    for (int i = 0; i < 8; ++i) {
        int n = twolame_encode_buffer(two, input, input, 1152, mp2, sizeof(mp2));
        save(mp2file, mp2, n);
        two_bytes += n;
    }
    int n = twolame_encode_flush(two, mp2, sizeof(mp2));
    save(mp2file, mp2, n);
    two_bytes += n;
    REQUIRE(two_bytes > 4 && fclose(mp2file) == 0);
    twolame_close(&two);

    shine_config_t config = {0};
    shine_set_config_mpeg_defaults(&config.mpeg);
    config.wave.channels = PCM_MONO;
    config.wave.samplerate = 44100;
    config.mpeg.mode = MONO;
    config.mpeg.bitr = 128;
    REQUIRE(shine_check_config(44100, 128) >= 0);
    shine_t shine = shine_initialise(&config);
    REQUIRE(shine && shine_samples_per_pass(shine) == 1152);
    FILE *mp3file = fopen("shine.mp3", "wb");
    REQUIRE(mp3file);
    int shine_bytes = 0;
    for (int i = 0; i < 8; ++i) {
        unsigned char *data = shine_encode_buffer_interleaved(shine, input, &n);
        save(mp3file, data, n);
        shine_bytes += n;
    }
    unsigned char *data = shine_flush(shine, &n);
    save(mp3file, data, n);
    shine_bytes += n;
    REQUIRE(shine_bytes > 4 && fclose(mp3file) == 0);
    shine_close(shine);

    printf("{\"codec2_samples\":%d,\"codec2_bytes\":%d,\"twolame_bytes\":%d,\"shine_bytes\":%d}\n",
           samples, bytes, two_bytes, shine_bytes);
    return 0;
}
