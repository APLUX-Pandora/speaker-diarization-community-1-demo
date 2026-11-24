"""Separate speakers in the bundled meeting recording."""

from pandora_speaker_diarization_community_1 import DiarizationModel


def main():
    with DiarizationModel.from_pretrained("models") as model:
        result = model.diarize("examples/audio/meeting.wav")
    for segment in result.segments:
        print(f"{segment.start:.3f}–{segment.end:.3f} s {segment.speaker}")


if __name__ == "__main__":
    main()
