/* Separate speakers in the bundled meeting recording using the C SDK. */
#include "speaker_diarization.h"
#include <stdio.h>
#include <string.h>

static uint32_t read_le32(const unsigned char *bytes) {
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 |
           (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

/* The bundled recording has a 44-byte header and mono 16 kHz PCM16.
 * This example reads that layout and hands the SDK normalized PCM. */
static int feed_example_audio(sdiar_engine *engine, double *duration) {
    const char *path = "examples/audio/meeting.wav";
    FILE *audio = fopen(path, "rb");
    if (!audio) {
        perror(path);
        return SDIAR_ERROR_ARGUMENT;
    }

    static const unsigned char pcm_format[] = {
        1, 0, 1, 0,              /* PCM, mono */
        0x80, 0x3e, 0, 0,        /* 16,000 samples per second */
        0, 0x7d, 0, 0, 2, 0,    /* 32,000 bytes per second, two bytes per sample */
        16, 0
    };
    unsigned char header[44];
    uint32_t remaining = 0;
    if (fread(header, 1, sizeof(header), audio) == sizeof(header) &&
        !memcmp(header, "RIFF", 4) &&
        !memcmp(header + 8, "WAVEfmt ", 8) &&
        read_le32(header + 16) == sizeof(pcm_format) &&
        !memcmp(header + 20, pcm_format, sizeof(pcm_format)) &&
        !memcmp(header + 36, "data", 4)) {
        remaining = read_le32(header + 40);
        uint32_t riff_size = read_le32(header + 4);
        if (remaining % 2 || riff_size < 36 || remaining != riff_size - 36)
            remaining = 0;
    }

    *duration = remaining / (2.0 * 16000);
    unsigned char encoded[4096];
    float samples[sizeof(encoded) / 2];
    int rc = SDIAR_ERROR_ARGUMENT;
    while (remaining) {
        size_t bytes = remaining < sizeof(encoded) ? remaining : sizeof(encoded);
        if (fread(encoded, 1, bytes, audio) != bytes) {
            rc = SDIAR_ERROR_ARGUMENT;
            break;
        }
        for (size_t i = 0; i < bytes / 2; ++i) {
            int value = encoded[2 * i] | encoded[2 * i + 1] << 8;
            samples[i] = (value < 32768 ? value : value - 65536) / 32768.0f;
        }
        rc = sdiar_feed(engine, samples, bytes / 2);
        if (rc) break;
        remaining -= (uint32_t)bytes;
    }
    fclose(audio);
    if (rc == SDIAR_ERROR_ARGUMENT) fprintf(stderr, "Can't read bundled PCM recording: %s\n", path);
    return rc;
}

static int print_segments(sdiar_engine *engine, double duration) {
    sdiar_segment segments[32];
    int count;
    while ((count = sdiar_read_segments(engine, segments, 32)) > 0) {
        for (int i = 0; i < count; ++i) {
            const sdiar_segment *segment = &segments[i];
            double end = segment->end_seconds < duration ? segment->end_seconds : duration;
            if (segment->kind == SDIAR_SEGMENT_STANDARD && segment->start_seconds < end)
                printf("%.3f–%.3f s SPEAKER_%02u\n", segment->start_seconds, end, segment->speaker_id);
        }
    }
    return count;
}

int main(int argc, char **argv) {
    if (argc != 2 || !argv[1][0]) {
        fprintf(stderr, "Usage: %s MODEL_DIR\n", argv[0]);
        return 2;
    }

    sdiar_engine *engine = sdiar_create(argv[1], NULL);
    if (!engine) {
        fprintf(stderr, "Cannot load model: %s\n", sdiar_create_error());
        return 1;
    }

    double duration = 0;
    int rc = feed_example_audio(engine, &duration);
    if (!rc) {
        int count = sdiar_finish(engine);
        if (count < 0) rc = count;
    }
    if (!rc) rc = print_segments(engine, duration);
    if (rc) {
        const char *error = sdiar_last_error(engine);
        if (error && *error) fprintf(stderr, "%s\n", error);
    }
    if (sdiar_destroy_owned(&engine)) {
        fprintf(stderr, "Cannot release the model\n");
        return 1;
    }
    return rc ? 1 : 0;
}
